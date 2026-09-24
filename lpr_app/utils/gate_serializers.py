"""JSON shapes for gate objects. Secrets are never serialised here."""

import json
import os

from django.http import JsonResponse


def iso(dt):
    return dt.isoformat() if dt else None


def vehicle_ref(v):
    if v is None:
        return None
    return {'id': v.id, 'plate_display': v.plate_display, 'owner_name': v.owner_name}


def serialize_vehicle(v):
    allowed, status = v.access_status()
    return {
        'id': v.id,
        'plate_display': v.plate_display,
        'plate_normalized': v.plate_normalized,
        'owner_name': v.owner_name,
        'owner_phone': v.owner_phone,
        'department': v.department,
        'vehicle_type': v.vehicle_type,
        'valid_from': iso(v.valid_from),
        'valid_until': iso(v.valid_until),
        'is_active': v.is_active,
        'access_status': status,
        'allowed_now': allowed,
        'notes': v.notes,
        'created_at': iso(v.created_at),
        'updated_at': iso(v.updated_at),
    }


def serialize_camera(c):
    return {
        'id': c.id,
        'name': c.name,
        'is_enabled': c.is_enabled,
        'host': c.host,
        'rtsp_port': c.rtsp_port,
        'http_port': c.http_port,
        'username': c.username,
        'password_set': c.password_set,
        'vendor': c.vendor,
        'main_stream_path': c.main_stream_path,
        'sub_stream_path': c.sub_stream_path,
        'snapshot_path': c.snapshot_path,
        'live_snapshot_path': c.live_snapshot_path,
        'prefer_snapshot': c.prefer_snapshot,
        'roi': c.roi,
        'motion_threshold': c.motion_threshold,
        'settle_ms': c.settle_ms,
        'cooldown_s': c.cooldown_s,
        'config_version': c.config_version,
        'updated_by': c.updated_by.get_username() if c.updated_by else None,
        'updated_at': iso(c.updated_at),
        'last_test_at': iso(c.last_test_at),
        'last_test_ok': c.last_test_ok,
        'last_test_error': c.last_test_error,
        'agent_status': c.agent_status or None,
        'agent_status_at': iso(c.agent_status_at),
        'agent_trigger': c.agent_trigger or None,
        'gates': [{'id': g.id, 'name': g.name} for g in c.gates.all()],
    }


def gate_camera(link):
    camera = link.camera
    return {
        'id': camera.id,
        'name': camera.name,
        'direction': link.direction,
        'is_enabled': camera.is_enabled,
        'roi': camera.roi,
        'agent_status': camera.agent_status or None,
        'agent_trigger': camera.agent_trigger or None,
    }


def serialize_gate(g):
    links = list(g.gate_cameras.all())
    return {
        'id': g.id,
        'name': g.name,
        'location': g.location,
        'cameras': [gate_camera(link) for link in links],
        'camera_warning': g.camera_warning(),
        'exit_policy': g.exit_policy,
        'controller_type': g.controller_type,
        'controller_url': g.controller_url,
        'controller_token_set': g.controller_token_set,
        'has_safety_input': g.has_safety_input,
        'is_enabled': g.is_enabled,
        'online': g.is_online(),
        'last_seen': iso(g.last_seen),
        'firmware_version': g.firmware_version,
        'last_command_result': g.last_command_result,
        'arm_state': g.arm_state or None,
        'arm_state_at': iso(g.arm_state_at),
    }


def frame_on_disk(image, field):
    """A record can outlive its file: report what can actually be shown."""
    stored = getattr(image, field, None) if image else None
    if not stored:
        return False
    try:
        return os.path.exists(stored.path)
    except (ValueError, NotImplementedError):
        return False


def serialize_event(e):
    image = e.uploaded_image
    return {
        'id': e.id,
        'timestamp': iso(e.timestamp),
        'gate': {'id': e.gate.id, 'name': e.gate.name} if e.gate else None,
        'plate_raw': e.plate_raw,
        'plate_normalized': e.plate_normalized,
        'confidence': e.confidence,
        'frames_read': e.frames_read,
        'frames_agreed': e.frames_agreed,
        'vehicle': vehicle_ref(e.vehicle),
        'near_miss_vehicle': vehicle_ref(e.near_miss_vehicle),
        'camera': {'id': e.camera.id, 'name': e.camera.name} if e.camera else None,
        'direction': e.direction,
        'decision': e.decision,
        'reason': e.reason,
        'reason_display': e.get_reason_display(),
        'mode': e.mode,
        'command': e.command,
        'command_sent': e.command_sent,
        'command_result': e.command_result,
        'decision_latency_ms': e.decision_latency_ms,
        'operator': e.operator.get_username() if e.operator else None,
        'is_test': e.is_test,
        # The frame kept as evidence, if its file is still there
        'has_image': frame_on_disk(image, 'original_image'),
        'has_processed_image': frame_on_disk(image, 'processed_image'),
        # A record with no file left: the media directory lost it
        'frame_lost': bool(image) and not frame_on_disk(image, 'original_image'),
    }


def serialize_config_change(c):
    return {
        'id': c.id,
        'timestamp': iso(c.timestamp),
        'user': c.user.get_username() if c.user else None,
        'object_type': c.object_type,
        'object_id': c.object_id,
        'object_repr': c.object_repr,
        'action': c.action,
        'changes': c.changes,
    }


class BadRequest(Exception):
    pass


def json_body(request):
    """Parse a JSON object body, raising BadRequest otherwise."""
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise BadRequest('Request body must be valid JSON')
    if not isinstance(data, dict):
        raise BadRequest('Request body must be a JSON object')
    return data


def error(message, code, status=400, **extra):
    payload = {'success': False, 'error': message, 'error_code': code}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def form_errors(form):
    return error(
        'Validation failed', 'VALIDATION_ERROR',
        errors={field: [e['message'] for e in errs] for field, errs in form.errors.get_json_data().items()},
    )


def paginate(request, queryset, serializer, default_size=20):
    from django.core.paginator import Paginator
    try:
        page_size = max(1, min(int(request.GET.get('page_size', default_size)), 100))
        page_number = int(request.GET.get('page', 1))
    except ValueError:
        raise BadRequest('page and page_size must be integers')
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(page_number)
    base_url = request.build_absolute_uri(request.path)
    params = request.GET.copy()

    def link(n):
        params['page'] = n
        params['page_size'] = page_size
        return f'{base_url}?{params.urlencode()}'

    return JsonResponse({
        'count': paginator.count,
        'next': link(page.next_page_number()) if page.has_next() else None,
        'previous': link(page.previous_page_number()) if page.has_previous() else None,
        'results': [serializer(obj) for obj in page],
    })
