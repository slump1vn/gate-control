"""
Gate runtime endpoints.

- Gate agent (GATE_AGENT_TOKEN): decide, config, status reports, command queue
- ESP32 controller (its own device token): heartbeat
- Operators (session): manual override, gate status, event images

Django never contacts a controller itself; the agent relays every command.
"""

import hashlib
import json
import logging
import os
import time

from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import AccessEvent, Camera, GateDevice
from ..services import barrier_simulator, gate_service
from ..services.api_service import ApiService
from ..services.camera_service import VENDOR_PRESETS
from ..utils.auth import device_token_matches, require_agent_token, require_gate_operator
from ..utils.gate_serializers import (
    BadRequest, error, iso, json_body, serialize_event, serialize_gate, vehicle_ref,
)
from ..utils.secrets import SecretDecryptError, SecretKeyMissing

logger = logging.getLogger(__name__)

AGENT_CAMERA_STATUSES = {'streaming', 'reconnecting', 'auth_failed', 'unreachable', 'disabled'}
ARM_STATES = {choice for choice, _ in GateDevice.ARM_STATES}


# ---------------------------------------------------------------------------
# Gate agent
# ---------------------------------------------------------------------------

def validate_frames(request):
    """Return (frames, None) or (None, error_response) for a multipart burst."""
    frames = request.FILES.getlist('frames')
    max_frames = settings.GATE_BURST_FRAMES
    if not frames:
        return None, error('No frames provided', 'MISSING_FRAMES')
    if len(frames) > max_frames:
        return None, error(f'Too many frames: {len(frames)} > GATE_BURST_FRAMES ({max_frames})', 'TOO_MANY_FRAMES')
    for frame in frames:
        frame_error = ApiService.validate_image_file(frame)
        if frame_error:
            return None, frame_error
    return frames, None


@require_http_methods(["POST"])
@require_agent_token
def api_gate_decide(request):
    started = time.monotonic()
    frames, frames_error = validate_frames(request)
    if frames_error:
        return frames_error

    try:
        gate_id = int(request.POST.get('gate_id', ''))
    except ValueError:
        return error('gate_id is required', 'MISSING_GATE')

    decision = gate_service.decide(gate_id, frames, started=started)
    event = decision.event
    return JsonResponse({
        'success': True,
        'decision': event.decision,
        'reason': event.reason,
        'plate': event.plate_normalized or None,
        'plate_raw': event.plate_raw or None,
        'confidence': event.confidence,
        'frames_read': event.frames_read,
        'frames_agreed': event.frames_agreed,
        'vehicle': vehicle_ref(event.vehicle),
        'near_miss_vehicle': vehicle_ref(event.near_miss_vehicle),
        'event_id': event.id,
        'mode': event.mode,
        'actuate': decision.actuate,
        'command': 'open' if decision.actuate else None,
        'decision_latency_ms': event.decision_latency_ms,
    })


@require_http_methods(["POST"])
@require_agent_token
def api_gate_command_result(request, event_id):
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    event = AccessEvent.objects.filter(pk=event_id).exclude(command='').first()
    if event is None:
        return error('Event not found or has no command', 'NOT_FOUND', status=404)
    gate_service.record_command_result(event, body.get('sent'), body.get('result', ''))
    return JsonResponse({'success': True})


@require_http_methods(["GET"])
@require_agent_token
def api_gate_agent_commands(request):
    commands = [
        {
            'event_id': e.id,
            'gate_id': e.gate_id,
            'command': e.command,
            'issued_at': iso(e.timestamp),
        }
        for e in gate_service.claim_pending_commands()
    ]
    return JsonResponse({'commands': commands})


def _agent_camera(camera):
    if camera is None or not camera.is_enabled:
        return None
    try:
        password, credential_error = camera.get_password(), None
    except (SecretKeyMissing, SecretDecryptError) as exc:
        logger.error('Cannot decrypt password for camera %s: %s', camera.pk, exc)
        password, credential_error = None, 'password_unavailable'
    presets = VENDOR_PRESETS.get(camera.vendor, {})
    return {
        'id': camera.id,
        'name': camera.name,
        'host': camera.host,
        'rtsp_port': camera.rtsp_port,
        'http_port': camera.http_port,
        'username': camera.username,
        'password': password,
        'credential_error': credential_error,
        'vendor': camera.vendor,
        'main_stream_path': camera.main_stream_path or presets.get('main_stream_path', ''),
        'sub_stream_path': camera.sub_stream_path or presets.get('sub_stream_path', ''),
        'snapshot_path': camera.snapshot_path or presets.get('snapshot_path', ''),
        'prefer_snapshot': camera.prefer_snapshot,
        'roi': camera.roi,
        'motion_threshold': camera.motion_threshold,
        'settle_ms': camera.settle_ms,
        'cooldown_s': camera.cooldown_s,
        'config_version': camera.config_version,
    }


def _agent_gate(request, gate):
    try:
        token, token_error = gate.get_controller_token(), None
    except (SecretKeyMissing, SecretDecryptError) as exc:
        logger.error('Cannot decrypt controller token for gate %s: %s', gate.pk, exc)
        token, token_error = None, 'token_unavailable'

    auto_close = settings.GATE_AUTO_CLOSE
    config_errors = []
    if auto_close == 'software' and not gate.has_safety_input:
        # Never lower the arm on a timer without a safety input on the controller
        config_errors.append(
            'GATE_AUTO_CLOSE=software requires has_safety_input; software auto-close disabled for this gate'
        )
        auto_close = 'controller'
    controller_url = gate.controller_url
    if gate.is_simulated:
        # The simulated controller lives in this service; reach it the way the agent reached us
        controller_url = request.build_absolute_uri(f'/api/v1/gate/sim/{gate.id}/')
    return {
        'id': gate.id,
        'name': gate.name,
        'direction': gate.direction,
        'has_safety_input': gate.has_safety_input,
        'controller_type': gate.controller_type,
        'controller_url': controller_url,
        'controller_token': token,
        'controller_token_error': token_error,
        'auto_close': auto_close,
        'auto_close_seconds': settings.GATE_AUTO_CLOSE_SECONDS,
        'config_errors': config_errors,
        'camera': _agent_camera(gate.camera),
    }


@require_http_methods(["GET"])
@require_agent_token
def api_gate_agent_config(request):
    gates = GateDevice.objects.filter(is_enabled=True).select_related('camera').order_by('id')
    payload = {
        'mode': gate_service.effective_mode(),
        'burst_frames': settings.GATE_BURST_FRAMES,
        'decide_timeout_seconds': settings.GATE_DECIDE_TIMEOUT,
        'max_upload_bytes': settings.UPLOAD_FILE_MAX_SIZE,
        'gates': [_agent_gate(request, g) for g in gates],
    }
    version = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    known = request.GET.get('version') or request.META.get('HTTP_IF_NONE_MATCH', '').strip('"')
    if known == version:
        response = HttpResponse(status=304)
    else:
        response = JsonResponse({'version': version, **payload})
    response['ETag'] = f'"{version}"'
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(["POST"])
@require_agent_token
def api_gate_agent_status(request):
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    now = timezone.now()
    updated = 0
    for item in body.get('cameras') or []:
        status = str(item.get('status', ''))
        if status not in AGENT_CAMERA_STATUSES:
            return error(f'Unknown camera status {status!r}', 'INVALID_STATUS')
        updated += Camera.objects.filter(pk=item.get('id')).update(agent_status=status, agent_status_at=now)
    return JsonResponse({'success': True, 'updated': updated})


# ---------------------------------------------------------------------------
# ESP32 controller
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def api_gate_heartbeat(request):
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    gate = GateDevice.objects.filter(pk=body.get('gate_id')).first() if str(body.get('gate_id', '')).isdigit() else None
    # Same response for an unknown gate and a wrong token
    if gate is None or not device_token_matches(request, gate):
        return error('Permission denied', 'FORBIDDEN', status=403)

    gate.last_seen = timezone.now()
    fields = ['last_seen']
    if body.get('firmware_version'):
        gate.firmware_version = str(body['firmware_version'])[:50]
        fields.append('firmware_version')
    if body.get('last_command_result'):
        gate.last_command_result = str(body['last_command_result'])[:100]
        fields.append('last_command_result')
    if body.get('arm_state') in ARM_STATES:
        gate.arm_state = body['arm_state']
        gate.arm_state_at = gate.last_seen
        fields += ['arm_state', 'arm_state_at']
    gate.save(update_fields=fields)
    return JsonResponse({'success': True, 'server_time': iso(gate.last_seen)})


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

@require_http_methods(["POST"])
@require_gate_operator
def api_gate_override(request):
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    command = body.get('command')
    if command not in gate_service.COMMANDS:
        return error(f'command must be one of {", ".join(gate_service.COMMANDS)}', 'INVALID_COMMAND')
    gate = GateDevice.objects.filter(pk=body.get('gate_id')).first() if str(body.get('gate_id', '')).isdigit() else None
    if gate is None:
        return error('Gate not found', 'NOT_FOUND', status=404)

    event = gate_service.create_override(gate, command, request.user)
    logger.info('Manual %s on gate %s by %s (mode=%s)', command, gate.name, request.user, event.mode)
    return JsonResponse({'success': True, 'event': serialize_event(event)})


@require_http_methods(["GET"])
@require_gate_operator
def api_gate_status(request):
    gates = []
    for gate in GateDevice.objects.select_related('camera').order_by('id'):
        simulator = barrier_simulator.refresh(gate) if gate.is_simulated else None
        last = gate.events.select_related('vehicle', 'near_miss_vehicle', 'operator', 'uploaded_image').first()
        data = serialize_gate(gate)
        data['simulator'] = simulator
        data['camera_status'] = (gate.camera.agent_status or None) if gate.camera else None
        data['last_event'] = serialize_event(last) if last else None
        gates.append(data)
    return JsonResponse({'mode': gate_service.effective_mode(), 'gates': gates})


@require_http_methods(["GET"])
@require_gate_operator
def api_gate_event_image(request, event_id, image_type):
    event = AccessEvent.objects.select_related('uploaded_image').filter(pk=event_id).first()
    image = event.uploaded_image if event else None
    field = None
    if image is not None:
        field = image.original_image if image_type == 'original' else image.processed_image if image_type == 'processed' else None
    if not field:
        return error('Image not found', 'NOT_FOUND', status=404)
    try:
        path = field.path
    except ValueError:
        return error('Image not found', 'NOT_FOUND', status=404)
    if not os.path.exists(path):
        return error('Image not found', 'NOT_FOUND', status=404)
    response = FileResponse(open(path, 'rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, max-age=300'
    return response


# ---------------------------------------------------------------------------
# Simulated controller (the ESP32 contract, served by Django)
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_gate_simulator_device(request, gate_id, command):
    """
    Speaks the ESP32 controller protocol for a gate whose controller_type is
    'simulator': POST open/close/stop with {nonce, ts}, GET status. The agent
    talks to it exactly as it will talk to the real board.
    """
    gate = GateDevice.objects.filter(pk=gate_id, controller_type='simulator').first()
    if gate is None or not device_token_matches(request, gate):
        return error('Unauthorized', 'UNAUTHORIZED', status=401)

    if command == 'status':
        if request.method != 'GET':
            return error('Use GET for status', 'METHOD_NOT_ALLOWED', status=405)
        return JsonResponse(barrier_simulator.refresh(gate))

    if request.method != 'POST':
        return error('Use POST for commands', 'METHOD_NOT_ALLOWED', status=405)
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    try:
        return JsonResponse(barrier_simulator.execute(gate, command, body.get('nonce'), body.get('ts')))
    except barrier_simulator.CommandRejected as exc:
        return error(exc.message, 'REJECTED', status=exc.status)
