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

from ..models import AccessEvent, Camera, ControllerJob, GateDevice
from ..services import barrier_simulator, controller_jobs, gate_service
from ..services.api_service import ApiService
from ..services.camera_service import VENDOR_PRESETS
from ..utils.auth import device_request, require_agent_token, require_gate_operator
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
    # Which of the gate's cameras saw the vehicle, and so which way it was going
    camera_id = request.POST.get('camera_id') or ''
    camera_id = int(camera_id) if camera_id.isdigit() else None

    decision = gate_service.decide(gate_id, frames, started=started, camera_id=camera_id)
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
        'direction': event.direction or None,
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


@require_http_methods(["GET"])
@require_agent_token
def api_gate_agent_jobs(request):
    """Installer jobs (remote capture) for the agent to carry out on controllers."""
    return JsonResponse({'jobs': [controller_jobs.agent_view(j) for j in controller_jobs.claim()]})


@require_http_methods(["POST"])
@require_agent_token
def api_controller_job_result(request, job_id):
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    job = ControllerJob.objects.filter(pk=job_id).select_related('gate', 'created_by').first()
    if job is None:
        return error('Job not found', 'NOT_FOUND', status=404)
    controller_jobs.record_agent_result(job, body.get('sent'), body.get('result', ''), body.get('detail'))
    return JsonResponse({'success': True})


# Numbers only, and only the ones the UI shows: the agent is trusted to
# report, not to decide what gets stored.
TRIGGER_NUMBERS = ('fps', 'motion', 'presence', 'motion_threshold', 'presence_threshold', 'grab_seconds')
TRIGGER_STATES = {'idle', 'motion', 'occupied', 'passing'}


def _trigger_readout(item):
    readout = {}
    for key in TRIGGER_NUMBERS:
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            readout[key] = round(float(value), 4)
    state = item.get('trigger_state')
    if state in TRIGGER_STATES:
        readout['state'] = state
    if readout:
        readout['at'] = timezone.now().isoformat()
    return readout


def _agent_camera(camera, direction=''):
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
        'direction': direction,
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
        # Which vehicles to read: 'any', 'toward' or 'away' from the camera
        'travel_direction': camera.travel_direction,
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
        'has_safety_input': gate.has_safety_input,
        'exit_policy': gate.exit_policy,
        'controller_type': gate.controller_type,
        'controller_url': controller_url,
        'controller_token': token,
        'controller_token_error': token_error,
        'auto_close': auto_close,
        'auto_close_seconds': settings.GATE_AUTO_CLOSE_SECONDS,
        'config_errors': config_errors,
        'cameras': [c for c in (_agent_camera(link.camera, link.direction) for link in gate.gate_cameras.all()) if c],
    }


@require_http_methods(["GET"])
@require_agent_token
def api_gate_agent_config(request):
    gates = GateDevice.objects.filter(is_enabled=True).prefetch_related('gate_cameras__camera').order_by('id')
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
        updated += Camera.objects.filter(pk=item.get('id')).update(
            agent_status=status, agent_status_at=now, agent_trigger=_trigger_readout(item),
        )
    return JsonResponse({'success': True, 'updated': updated})


# ---------------------------------------------------------------------------
# ESP32 controller
# ---------------------------------------------------------------------------

# A signed heartbeat is refused this far from our clock. A replay inside the
# window can only refresh last_seen, which the device does every 10s anyway.
HEARTBEAT_TS_WINDOW_SECONDS = 30
CONTROLLER_TRANSPORTS = ('relay', 'rf433')


def _controller_health(body):
    """The self-reported health fields of a heartbeat, type-checked; unknown keys are dropped."""
    health = {}
    if body.get('transport') in CONTROLLER_TRANSPORTS:
        health['transport'] = body['transport']
    rssi = body.get('wifi_rssi')
    if isinstance(rssi, (int, float)) and not isinstance(rssi, bool) and -120 <= rssi <= 0:
        health['wifi_rssi'] = int(rssi)
    for key in ('clock_synced', 'dry_run'):
        if isinstance(body.get(key), bool):
            health[key] = body[key]
    count = body.get('rf_tx_count')
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        health['rf_tx_count'] = count
    if isinstance(body.get('uptime_s'), int) and not isinstance(body.get('uptime_s'), bool):
        health['uptime_s'] = max(0, body['uptime_s'])
    return health

def _simulated_capture(request, gate, auth, command):
    """The 433 MHz controller's /capture and /capture/save, as the simulator does them."""
    if auth.version < 2:
        return error('Capture needs a signed request', 'UNAUTHORIZED', status=401)
    try:
        body = json_body(request)
        barrier_simulator.accept_request(gate, auth.nonce, auth.ts)
        if command == 'capture':
            report = barrier_simulator.capture(gate, body.get('button'), body.get('job'))
            # A real controller reports this in its next heartbeat
            controller_jobs.apply_capture_report(gate, report)
            return JsonResponse({'ok': True, 'command': 'capture', 'result': 'capturing'}, status=202)
        buttons = barrier_simulator.save_code(gate, body.get('button'))
        return JsonResponse({'ok': True, 'command': 'save_code', 'result': 'saved', 'buttons': buttons})
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    except barrier_simulator.CommandRejected as exc:
        return error(exc.message, 'REJECTED', status=exc.status)


@csrf_exempt
@require_http_methods(["POST"])
def api_gate_heartbeat(request):
    try:
        body = json_body(request)
    except BadRequest as exc:
        return error(str(exc), 'INVALID_JSON')
    gate = GateDevice.objects.filter(pk=body.get('gate_id')).first() if str(body.get('gate_id', '')).isdigit() else None
    auth = device_request(request, gate) if gate is not None else None
    # Same response for an unknown gate and a wrong token
    if auth is None:
        return error('Permission denied', 'FORBIDDEN', status=403)
    if auth.version >= 2 and abs(time.time() - auth.ts) > HEARTBEAT_TS_WINDOW_SECONDS:
        # Genuinely signed, but by a device whose clock is off (just booted, no
        # NTP): tell it the time. Commands still need fresh signatures and
        # never-seen nonces, so a wrong time cannot replay one.
        return error('Device clock out of range', 'CLOCK_SKEW', status=403, server_ts=time.time())

    gate.last_seen = timezone.now()
    fields = ['last_seen']
    health = _controller_health(body)
    health.update(controller_jobs.clean_heartbeat(body))
    if health:
        gate.controller_health = health
        fields.append('controller_health')
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
    if 'capture' in health:
        controller_jobs.apply_capture_report(gate, health['capture'])
    # server_ts lets a device without SNTP set its clock (the signature window needs one)
    return JsonResponse({'success': True, 'server_time': iso(gate.last_seen), 'server_ts': time.time()})


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
    for gate in GateDevice.objects.prefetch_related('gate_cameras__camera').order_by('id'):
        simulator = barrier_simulator.refresh(gate) if gate.is_simulated else None
        last = gate.events.select_related('vehicle', 'near_miss_vehicle', 'operator', 'uploaded_image').first()
        data = serialize_gate(gate)
        data['simulator'] = simulator
        # serialize_gate already carries the cameras, their directions and read zones
        data['last_event'] = serialize_event(last) if last else None
        gates.append(data)
    return JsonResponse({'mode': gate_service.effective_mode(), 'gates': gates})


@require_http_methods(["GET"])
@require_gate_operator
def api_gate_event_image(request, event_id, image_type):
    """The event's evidence frame, or with ?frame=<id> one of its other frames."""
    event = AccessEvent.objects.select_related('uploaded_image').filter(pk=event_id).first()
    image = event.uploaded_image if event else None
    frame_id = request.GET.get('frame', '')
    if event is not None and frame_id.isdigit():
        # Only frames of this event: an id from elsewhere must not be served
        image = event.frames.filter(pk=int(frame_id)).first()
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
    'simulator': POST open/close/stop, GET status, signed per contract v2
    (utils/gate_signing), or v1 (bearer token, {nonce, ts} in the body) for one
    release. The agent talks to it exactly as it will talk to the real board.
    """
    gate = GateDevice.objects.filter(pk=gate_id, controller_type='simulator').first()
    auth = device_request(request, gate) if gate is not None else None
    if auth is None:
        return error('Unauthorized', 'UNAUTHORIZED', status=401)

    if command == 'status':
        if request.method != 'GET':
            return error('Use GET for status', 'METHOD_NOT_ALLOWED', status=405)
        return JsonResponse(barrier_simulator.refresh(gate))

    if request.method != 'POST':
        return error('Use POST for commands', 'METHOD_NOT_ALLOWED', status=405)
    if command in ('capture', 'capture/save'):
        return _simulated_capture(request, gate, auth, command)
    if auth.version >= 2:
        # Signed headers: the body is not needed for anything but the signature
        nonce, ts = auth.nonce, auth.ts
    else:
        try:
            body = json_body(request)
        except BadRequest as exc:
            return error(str(exc), 'INVALID_JSON')
        nonce, ts = body.get('nonce'), body.get('ts')
    try:
        return JsonResponse(barrier_simulator.execute(gate, command, nonce, ts))
    except barrier_simulator.CommandRejected as exc:
        return error(exc.message, 'REJECTED', status=exc.status)
