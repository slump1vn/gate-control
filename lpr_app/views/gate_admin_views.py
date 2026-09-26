"""
Gate management endpoints for the SPA.

gate_admin: cameras, gate devices, configuration audit log
gate_operator: vehicle registry, access event log

Writes use the same forms and audited save helpers as the Django admin.
PUT and PATCH are both partial: omitted fields keep their current values.
"""

import base64
import logging

from django.contrib.auth.models import Group, User
from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from ..forms import CameraForm, GateDeviceForm, UserForm, VehicleForm
from ..models import AccessEvent, Camera, GateCamera, GateConfigChange, GateDevice, Vehicle
from ..services import barrier_simulator, camera_service, config_audit, gate_service
from ..utils.auth import primary_role, require_gate_admin, require_gate_operator
from ..utils.gate_serializers import (
    BadRequest, error, form_errors, json_body, paginate, serialize_camera,
    serialize_config_change, serialize_event, serialize_gate, serialize_user, serialize_vehicle,
)
from ..utils.plates import clean_plate, normalize_plate
from ..utils.secrets import SecretDecryptError, SecretKeyMissing

logger = logging.getLogger(__name__)


def _bound_form(form_class, body, instance=None, extra_fields=()):
    """
    Build a form from a JSON body, merged over the instance's current values
    (or the model defaults, for a new instance).
    """
    fields = form_class._meta.fields
    data = model_to_dict(instance, fields=fields) if instance is not None else {}
    for key, value in body.items():
        if key in fields or key in extra_fields:
            data[key] = value
    if 'roi' in body:
        roi = body['roi'] or {}
        for axis in ('x', 'y', 'w', 'h'):
            data[f'roi_{axis}'] = roi.get(axis) if roi else None
    return form_class(data=data, instance=instance)


def _parse(request):
    try:
        return json_body(request), None
    except BadRequest as exc:
        return None, error(str(exc), 'INVALID_JSON')


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

def _parse_camera_gates(body):
    """
    Read the gates a camera is assigned to from a list of {gate, direction}.
    Returns (links, error response); links is None when the body leaves
    'gates' out, which keeps the current assignment.
    """
    if 'gates' not in body:
        return None, None
    items = body['gates'] or []
    invalid = error('gates must be a list of {gate, direction}', 'VALIDATION_ERROR')
    if not isinstance(items, list):
        return None, invalid

    links = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return None, invalid
        gate_id = item.get('gate') or item.get('id')
        direction = item.get('direction', 'in')
        if direction not in ('in', 'out'):
            return None, error(f'Unknown direction {direction!r}', 'VALIDATION_ERROR')
        gate = GateDevice.objects.filter(pk=gate_id).first() if str(gate_id).isdigit() else None
        if gate is None:
            return None, error(f'Gate {gate_id} not found', 'NOT_FOUND', status=404)
        if gate.pk in seen:
            return None, error(f'Gate "{gate.name}" is listed twice', 'VALIDATION_ERROR')
        seen.add(gate.pk)
        links.append((gate, direction))
    return links, None


def _save_camera(request, body, instance):
    """Validate and save a camera and, when the body lists them, its gates."""
    links, err = _parse_camera_gates(body)
    if err:
        return None, err
    form = _bound_form(CameraForm, body, instance, extra_fields=('password',))
    if not form.is_valid():
        return None, form_errors(form)
    with transaction.atomic():
        camera = config_audit.save_camera(form.instance, request.user, password=form.cleaned_data.get('password'))
        if links is not None:
            config_audit.set_camera_gates(camera, links, request.user)
    return camera, None


@require_http_methods(["GET", "POST"])
@require_gate_admin
def api_cameras(request):
    if request.method == 'GET':
        cameras = Camera.objects.select_related('updated_by').prefetch_related('gate_links__gate')
        return JsonResponse({'results': [serialize_camera(c) for c in cameras]})

    body, err = _parse(request)
    if err:
        return err
    camera, err = _save_camera(request, body, Camera())
    if err:
        return err
    return JsonResponse(serialize_camera(camera), status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_gate_admin
def api_camera_detail(request, camera_id):
    camera = Camera.objects.select_related('updated_by').filter(pk=camera_id).first()
    if camera is None:
        return error('Camera not found', 'NOT_FOUND', status=404)

    if request.method == 'GET':
        return JsonResponse(serialize_camera(camera))

    if request.method == 'DELETE':
        config_audit.delete_with_audit(camera, request.user)
        return JsonResponse({'success': True})

    body, err = _parse(request)
    if err:
        return err
    camera, err = _save_camera(request, body, camera)
    if err:
        return err
    return JsonResponse(serialize_camera(camera))


@require_http_methods(["GET"])
@require_gate_operator
def api_camera_snapshot(request, camera_id):
    """
    One live frame from the camera, for the monitoring view. Operators may
    watch the lane; only admins see or change the camera's settings.
    """
    camera = Camera.objects.filter(pk=camera_id).first()
    if camera is None:
        return error('Camera not found', 'NOT_FOUND', status=404)

    image, failure = camera_service.live_snapshot(camera)
    if image is None:
        return error(failure or 'Snapshot unavailable', 'SNAPSHOT_FAILED', status=503)

    response = HttpResponse(image, content_type='image/jpeg')
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(["GET"])
@require_gate_admin
def api_camera_presets(request):
    return JsonResponse({'presets': camera_service.VENDOR_PRESETS})


@require_http_methods(["POST"])
@require_gate_admin
def api_camera_test(request):
    """
    Test a saved camera, or unsaved form values (optionally over a saved
    camera, whose stored password is used when none is given). The result is
    recorded on the camera only when a saved camera is tested as-is.
    """
    body, err = _parse(request)
    if err:
        return err

    camera_id = body.pop('camera_id', None)
    instance = None
    if camera_id is not None:
        instance = Camera.objects.filter(pk=camera_id).first()
        if instance is None:
            return error('Camera not found', 'NOT_FOUND', status=404)

    password = body.get('password') or ''
    overrides = {k for k in body if k != 'password'}
    form = _bound_form(CameraForm, body, instance or Camera(), extra_fields=('password',))
    if not form.is_valid():
        return form_errors(form)
    candidate = form.instance
    camera_service.apply_preset(candidate)

    if not password and instance is not None and instance.password_set:
        try:
            password = instance.get_password()
        except (SecretKeyMissing, SecretDecryptError) as exc:
            return error(f'Stored password cannot be read: {exc}', 'PASSWORD_UNAVAILABLE')

    result = camera_service.test_connection(candidate, password)

    recorded = instance is not None and not overrides and not body.get('password')
    if recorded:
        Camera.objects.filter(pk=instance.pk).update(
            last_test_at=timezone.now(), last_test_ok=result.ok, last_test_error=result.error,
        )
    logger.info('Camera test for %s by %s: ok=%s %s', candidate.host, request.user, result.ok, result.error)

    return JsonResponse({
        'ok': result.ok,
        'steps': [s.as_dict() for s in result.steps],
        'image': 'data:image/jpeg;base64,' + base64.b64encode(result.image).decode() if result.image else None,
        'recorded': recorded,
    })


# ---------------------------------------------------------------------------
# Gate devices
# ---------------------------------------------------------------------------

def _save_gate_cameras(gate, body):
    """
    Replace the gate's cameras from a list of {camera, direction}. Returns an
    error response, or None. Leaving 'cameras' out keeps the current ones.
    """
    if 'cameras' not in body:
        return None
    items = body['cameras'] or []
    if not isinstance(items, list):
        return error('cameras must be a list of {camera, direction}', 'VALIDATION_ERROR')

    links = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return error('cameras must be a list of {camera, direction}', 'VALIDATION_ERROR')
        camera_id = item.get('camera') or item.get('id')
        direction = item.get('direction', 'in')
        if direction not in ('in', 'out'):
            return error(f'Unknown direction {direction!r}', 'VALIDATION_ERROR')
        camera = Camera.objects.filter(pk=camera_id).first()
        if camera is None:
            return error(f'Camera {camera_id} not found', 'NOT_FOUND', status=404)
        if camera.pk in seen:
            return error(f'Camera "{camera.name}" is listed twice', 'VALIDATION_ERROR')
        seen.add(camera.pk)
        links.append(GateCamera(gate=gate, camera=camera, direction=direction))

    gate.gate_cameras.exclude(camera_id__in=seen).delete()
    for link in links:
        GateCamera.objects.update_or_create(
            gate=gate, camera=link.camera, defaults={'direction': link.direction},
        )
    # The caller may hold a prefetched list of the old links
    gate._prefetched_objects_cache = {}
    return None


@require_http_methods(["GET", "POST"])
@require_gate_admin
def api_gate_devices(request):
    if request.method == 'GET':
        gates = GateDevice.objects.prefetch_related('gate_cameras__camera')
        return JsonResponse({'results': [serialize_gate(g) for g in gates]})

    body, err = _parse(request)
    if err:
        return err
    form = _bound_form(GateDeviceForm, body, GateDevice(), extra_fields=('controller_token',))
    if not form.is_valid():
        return form_errors(form)
    gate = config_audit.save_gate_device(form.instance, request.user, token=form.cleaned_data.get('controller_token'))
    err = _save_gate_cameras(gate, body)
    if err:
        return err
    return JsonResponse(serialize_gate(gate), status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_gate_admin
def api_gate_device_detail(request, gate_id):
    gate = GateDevice.objects.prefetch_related('gate_cameras__camera').filter(pk=gate_id).first()
    if gate is None:
        return error('Gate not found', 'NOT_FOUND', status=404)

    if request.method == 'GET':
        return JsonResponse(serialize_gate(gate))

    if request.method == 'DELETE':
        config_audit.delete_with_audit(gate, request.user)
        return JsonResponse({'success': True})

    body, err = _parse(request)
    if err:
        return err
    form = _bound_form(GateDeviceForm, body, gate, extra_fields=('controller_token',))
    if not form.is_valid():
        return form_errors(form)
    gate = config_audit.save_gate_device(form.instance, request.user, token=form.cleaned_data.get('controller_token'))
    err = _save_gate_cameras(gate, body)
    if err:
        return err
    return JsonResponse(serialize_gate(gate))


@require_http_methods(["GET"])
@require_gate_admin
def api_config_changes(request):
    queryset = GateConfigChange.objects.select_related('user')
    if request.GET.get('object_type'):
        queryset = queryset.filter(object_type=request.GET['object_type'])
    if request.GET.get('object_id', '').isdigit():
        queryset = queryset.filter(object_id=int(request.GET['object_id']))
    try:
        return paginate(request, queryset, serialize_config_change)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_PARAMS')


# ---------------------------------------------------------------------------
# Vehicle registry
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@require_gate_operator
def api_vehicles(request):
    if request.method == 'GET':
        queryset = Vehicle.objects.all()
        q = request.GET.get('q', '').strip()
        if q:
            plate = clean_plate(q)
            match = Q(owner_name__icontains=q) | Q(department__icontains=q) | Q(plate_display__icontains=q)
            if plate:
                match |= Q(plate_normalized__icontains=plate) | Q(plate_normalized=normalize_plate(q))
            queryset = queryset.filter(match)
        active = request.GET.get('is_active')
        if active in ('true', 'false'):
            queryset = queryset.filter(is_active=(active == 'true'))
        try:
            return paginate(request, queryset, serialize_vehicle)
        except BadRequest as exc:
            return error(str(exc), 'INVALID_PARAMS')

    body, err = _parse(request)
    if err:
        return err
    form = _bound_form(VehicleForm, body, Vehicle())
    if not form.is_valid():
        return form_errors(form)
    vehicle = form.save()
    logger.info('Vehicle %s added by %s', vehicle.plate_normalized, request.user)
    return JsonResponse(serialize_vehicle(vehicle), status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_gate_operator
def api_vehicle_detail(request, vehicle_id):
    vehicle = Vehicle.objects.filter(pk=vehicle_id).first()
    if vehicle is None:
        return error('Vehicle not found', 'NOT_FOUND', status=404)

    if request.method == 'GET':
        return JsonResponse(serialize_vehicle(vehicle))

    if request.method == 'DELETE':
        # Deactivate rather than delete, so access events keep their reference
        vehicle.is_active = False
        vehicle.save(update_fields=['is_active', 'updated_at'])
        logger.info('Vehicle %s deactivated by %s', vehicle.plate_normalized, request.user)
        return JsonResponse(serialize_vehicle(vehicle))

    body, err = _parse(request)
    if err:
        return err
    form = _bound_form(VehicleForm, body, vehicle)
    if not form.is_valid():
        return form_errors(form)
    vehicle = form.save()
    return JsonResponse(serialize_vehicle(vehicle))


@require_http_methods(["GET"])
@require_gate_operator
def api_plate_preview(request):
    """Normalise a plate as the registry would, for live preview while typing."""
    plate = request.GET.get('plate', '')
    normalized = normalize_plate(plate)
    existing = Vehicle.objects.filter(plate_normalized=normalized).first() if normalized else None
    return JsonResponse({
        'plate': plate,
        'normalized': normalized,
        'existing_vehicle': {'id': existing.id, 'plate_display': existing.plate_display} if existing else None,
    })


# ---------------------------------------------------------------------------
# Access events (read-only)
# ---------------------------------------------------------------------------

@require_http_methods(["GET"])
@require_gate_operator
def api_access_events(request):
    queryset = AccessEvent.objects.select_related(
        'gate', 'camera', 'vehicle', 'near_miss_vehicle', 'operator', 'uploaded_image',
    ).prefetch_related('frames')
    params = request.GET
    if params.get('gate', '').isdigit():
        queryset = queryset.filter(gate_id=int(params['gate']))
    if params.get('direction') in ('in', 'out'):
        queryset = queryset.filter(direction=params['direction'])
    if params.get('decision'):
        queryset = queryset.filter(decision=params['decision'])
    if params.get('reason'):
        queryset = queryset.filter(reason=params['reason'])
    for param, lookup in (('date_from', 'timestamp__date__gte'), ('date_to', 'timestamp__date__lte')):
        if params.get(param):
            day = parse_date(params[param])
            if day is None:
                return error(f'{param} must be a date (YYYY-MM-DD)', 'INVALID_PARAMS')
            queryset = queryset.filter(**{lookup: day})
    if params.get('is_test') in ('true', 'false'):
        queryset = queryset.filter(is_test=(params['is_test'] == 'true'))
    plate = clean_plate(params.get('plate', ''))
    if plate:
        queryset = queryset.filter(Q(plate_normalized__icontains=plate) | Q(plate_raw__icontains=plate))
    try:
        return paginate(request, queryset, serialize_event)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_PARAMS')


@require_http_methods(["GET"])
@require_gate_operator
def api_access_event_detail(request, event_id):
    event = AccessEvent.objects.select_related(
        'gate', 'vehicle', 'near_miss_vehicle', 'operator', 'uploaded_image',
    ).prefetch_related('frames').filter(pk=event_id).first()
    if event is None:
        return error('Event not found', 'NOT_FOUND', status=404)
    data = serialize_event(event)
    data['detections'] = event.uploaded_image.get_detection_results() if event.uploaded_image else None
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# Gate testing (admin): recognition from uploaded photos, simulated barrier
# ---------------------------------------------------------------------------

def run_test_decision(gate, frames):
    """
    Run the real decision pipeline on uploaded photos as a test event. A grant
    drives the gate's simulated barrier in-process; a real controller is never
    actuated from a test.
    """
    decision = gate_service.decide(gate.id, frames, is_test=True)
    event = decision.event
    simulator, note = None, None
    if decision.actuate and gate.is_simulated:
        simulator = gate_service.run_on_simulator(event)
        event.refresh_from_db()
    elif decision.outcome.granted and not gate.is_simulated:
        AccessEvent.objects.filter(pk=event.pk).update(command_result='not_sent_test')
        event.refresh_from_db()
        note = 'Test decisions only drive simulated barriers; the real controller was not actuated.'
    if gate.is_simulated and simulator is None:
        simulator = barrier_simulator.refresh(gate)
    return event, simulator, note


@require_http_methods(["POST"])
@require_gate_admin
def api_gate_test_decide(request, gate_id):
    from .gate_views import validate_frames

    gate = GateDevice.objects.filter(pk=gate_id).first()
    if gate is None:
        return error('Gate not found', 'NOT_FOUND', status=404)
    frames, frames_error = validate_frames(request)
    if frames_error:
        return frames_error
    event, simulator, note = run_test_decision(gate, frames)
    event = AccessEvent.objects.select_related(
        'gate', 'vehicle', 'near_miss_vehicle', 'operator', 'uploaded_image',
    ).get(pk=event.pk)
    return JsonResponse({
        'event': serialize_event(event),
        'simulator': simulator,
        'note': note,
    })


@require_http_methods(["GET", "POST"])
def api_gate_simulator(request, gate_id):
    """GET: simulated arm state (operator). POST {command}: drive it now (admin)."""
    from ..utils.auth import GATE_ADMIN, GATE_OPERATOR, forbidden, user_roles

    roles = user_roles(request.user)
    needed = GATE_OPERATOR if request.method == 'GET' else GATE_ADMIN
    if needed not in roles:
        return forbidden()

    gate = GateDevice.objects.filter(pk=gate_id, controller_type='simulator').first()
    if gate is None:
        return error('Simulated gate not found', 'NOT_FOUND', status=404)
    if request.method == 'GET':
        return JsonResponse(barrier_simulator.refresh(gate))

    body, err = _parse(request)
    if err:
        return err
    command = body.get('command')
    if command not in gate_service.COMMANDS:
        return error(f'command must be one of {", ".join(gate_service.COMMANDS)}', 'INVALID_COMMAND')
    event = gate_service.create_override(gate, command, request.user, is_test=True)
    gate_service.run_on_simulator(event)
    event.refresh_from_db()
    state = barrier_simulator.refresh(gate)
    return JsonResponse({'event': serialize_event(event), 'simulator': state})


# ---------------------------------------------------------------------------
# Users (gate_admin, gate_operator accounts)
# ---------------------------------------------------------------------------

def _set_role(user, role):
    group = Group.objects.filter(name=role).first()
    user.groups.set([group] if group else [])


@require_http_methods(["GET", "POST"])
@require_gate_admin
def api_users(request):
    if request.method == 'GET':
        queryset = User.objects.all().order_by('username')
        q = request.GET.get('q', '').strip()
        if q:
            queryset = queryset.filter(Q(username__icontains=q) | Q(email__icontains=q))
        active = request.GET.get('is_active')
        if active in ('true', 'false'):
            queryset = queryset.filter(is_active=(active == 'true'))
        try:
            return paginate(request, queryset, serialize_user)
        except BadRequest as exc:
            return error(str(exc), 'INVALID_PARAMS')

    body, err = _parse(request)
    if err:
        return err
    form = _bound_form(UserForm, body, User(), extra_fields=('password', 'role'))
    if not form.is_valid():
        return form_errors(form)
    user = form.save(commit=False)
    user.set_password(form.cleaned_data['password'])
    user.save()
    _set_role(user, form.cleaned_data['role'])
    config_audit.record_user_change(request.user, user, 'create', {
        'username': user.username, 'email': user.email,
        'role': form.cleaned_data['role'], 'is_active': user.is_active,
    })
    logger.info('User %s created by %s', user.username, request.user)
    return JsonResponse(serialize_user(user), status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_gate_admin
def api_user_detail(request, user_id):
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return error('User not found', 'NOT_FOUND', status=404)

    is_self = user.id == request.user.id

    if request.method == 'GET':
        return JsonResponse(serialize_user(user))

    if request.method == 'DELETE':
        if is_self:
            return error('You cannot deactivate your own account', 'SELF_LOCKOUT', status=400)
        user.is_active = False
        user.save(update_fields=['is_active'])
        config_audit.record_user_change(request.user, user, 'update', {'is_active': {'old': True, 'new': False}})
        logger.info('User %s deactivated by %s', user.username, request.user)
        return JsonResponse(serialize_user(user))

    body, err = _parse(request)
    if err:
        return err
    if is_self and body.get('is_active') is False:
        return error('You cannot deactivate your own account', 'SELF_LOCKOUT', status=400)
    if is_self and body.get('role') and body['role'] != 'gate_admin':
        return error('You cannot remove your own admin role', 'SELF_LOCKOUT', status=400)

    before_role, before_active, before_email = primary_role(user), user.is_active, user.email
    body_for_form = dict(body)
    body_for_form.setdefault('role', before_role or 'gate_operator')
    form = _bound_form(UserForm, body_for_form, user, extra_fields=('password', 'role'))
    if not form.is_valid():
        return form_errors(form)
    user = form.save(commit=False)
    password_changed = bool(form.cleaned_data.get('password'))
    if password_changed:
        user.set_password(form.cleaned_data['password'])
    user.save()
    _set_role(user, form.cleaned_data['role'])

    changes = {}
    if before_email != user.email:
        changes['email'] = {'old': before_email, 'new': user.email}
    if before_active != user.is_active:
        changes['is_active'] = {'old': before_active, 'new': user.is_active}
    if before_role != form.cleaned_data['role']:
        changes['role'] = {'old': before_role, 'new': form.cleaned_data['role']}
    if password_changed:
        changes['password'] = 'changed'
    if changes:
        config_audit.record_user_change(request.user, user, 'update', changes)
    return JsonResponse(serialize_user(user))
