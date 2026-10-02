"""
Simulated barrier controller.

Implements the ESP32 controller contract (design.md §6) against a virtual arm,
so recognition, decisions, the agent and the command path can be tested end to
end before any hardware is wired. It enforces the same rules the firmware must:
bearer token (checked by the view), strictly increasing nonce, timestamp within
±30 s, UP/DOWN interlock (1 s), the same motion command at most once per 3 s (STOP exempt).

Arm motion is derived from timestamps and advanced whenever the state is read,
including the controller's auto-close timer (auto_close_seconds, like the
BR6_CT's TIMING DIP).
"""

import hashlib
import secrets
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import GateDevice, SimulatedBarrier

INTERLOCK_SECONDS = 1.0
MIN_COMMAND_INTERVAL_SECONDS = 3.0
TIMESTAMP_WINDOW_SECONDS = 30
HISTORY_LIMIT = 50
FIRMWARE_VERSION = 'simulator-1'

ARM_STATE = {
    'down': 'down',
    'up': 'up',
    'moving_up': 'moving',
    'moving_down': 'moving',
    'stopped': 'stopped',
}
OPPOSITE = {'open': 'close', 'close': 'open'}


class CommandRejected(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def get_simulator(gate):
    return SimulatedBarrier.objects.get_or_create(gate=gate)[0]


def _travel(sim):
    return max(float(sim.travel_seconds), 0.1)


def _elapsed(sim, now):
    return max(0.0, (now - sim.phase_started_at).total_seconds())


def position(sim, now):
    """Arm position from 0.0 (down) to 1.0 (up)."""
    if sim.phase == 'moving_up':
        return min(1.0, sim.start_position + _elapsed(sim, now) / _travel(sim))
    if sim.phase == 'moving_down':
        return max(0.0, sim.start_position - _elapsed(sim, now) / _travel(sim))
    return sim.start_position


def _set_phase(sim, phase, start_position, at):
    sim.phase = phase
    sim.start_position = start_position
    sim.phase_started_at = at


def _log(sim, at, event, **extra):
    entry = {'at': at.isoformat(), 'event': event, 'arm_state': ARM_STATE[sim.phase]}
    entry.update(extra)
    sim.history = (list(sim.history or []) + [entry])[-HISTORY_LIMIT:]


def advance(sim, now):
    """Apply limit-switch arrivals and auto-close up to `now`. Returns True if anything changed."""
    changed = False
    for _ in range(6):
        if sim.phase == 'moving_up':
            done_at = sim.phase_started_at + timedelta(seconds=(1.0 - sim.start_position) * _travel(sim))
            if now >= done_at:
                _set_phase(sim, 'up', 1.0, done_at)
                _log(sim, done_at, 'reached_up')
                changed = True
                continue
        elif sim.phase == 'moving_down':
            done_at = sim.phase_started_at + timedelta(seconds=sim.start_position * _travel(sim))
            if now >= done_at:
                _set_phase(sim, 'down', 0.0, done_at)
                _log(sim, done_at, 'reached_down')
                changed = True
                continue
        elif sim.phase == 'up' and sim.auto_close_seconds:
            close_at = sim.phase_started_at + timedelta(seconds=sim.auto_close_seconds)
            if now >= close_at:
                _set_phase(sim, 'moving_down', 1.0, close_at)
                _log(sim, close_at, 'auto_close')
                changed = True
                continue
        break
    return changed


def state(sim, now=None):
    now = now or timezone.now()
    return {
        'arm_state': ARM_STATE[sim.phase],
        'phase': sim.phase,
        'position': round(position(sim, now), 3),
        'travel_seconds': sim.travel_seconds,
        'auto_close_seconds': sim.auto_close_seconds,
        'last_command': sim.last_motion_command or None,
        'last_command_at': sim.last_motion_command_at.isoformat() if sim.last_motion_command_at else None,
        'firmware_version': FIRMWARE_VERSION,
        'history': list(sim.history or [])[-10:],
    }


def _sync_gate(gate, sim, now):
    arm_state = ARM_STATE[sim.phase]
    GateDevice.objects.filter(pk=gate.pk).update(
        arm_state=arm_state, arm_state_at=now, last_seen=now, firmware_version=FIRMWARE_VERSION,
    )
    gate.arm_state, gate.arm_state_at = arm_state, now


def refresh(gate, now=None):
    """Advance a simulated gate's arm to now, persist it, and return its state."""
    now = now or timezone.now()
    sim = get_simulator(gate)
    if advance(sim, now):
        sim.save()
    _sync_gate(gate, sim, now)
    return state(sim, now)


def next_nonce(sim):
    return max(int(sim.last_nonce) + 1, int(time.time() * 1000))


def execute(gate, command, nonce, ts, now=None):
    """
    Apply a controller command under the firmware's rules. Returns the new
    state; raises CommandRejected with the HTTP status the firmware would use.
    """
    now = now or timezone.now()
    if command not in ('open', 'close', 'stop'):
        raise CommandRejected(404, f'Unknown command {command!r}')
    try:
        nonce = int(nonce)
        ts = float(ts)
    except (TypeError, ValueError):
        raise CommandRejected(400, 'nonce and ts are required')

    with transaction.atomic():
        sim = SimulatedBarrier.objects.select_for_update().get(pk=get_simulator(gate).pk)
        advance(sim, now)

        if nonce <= sim.last_nonce:
            raise CommandRejected(409, 'Replayed nonce')
        if abs(now.timestamp() - ts) > TIMESTAMP_WINDOW_SECONDS:
            raise CommandRejected(401, 'Timestamp outside the allowed window')

        if command != 'stop' and sim.last_motion_command_at:
            since = (now - sim.last_motion_command_at).total_seconds()
            if sim.last_motion_command == OPPOSITE[command] and since < INTERLOCK_SECONDS:
                raise CommandRejected(409, 'UP/DOWN interlock')
            if sim.last_motion_command == command and since < MIN_COMMAND_INTERVAL_SECONDS:
                raise CommandRejected(429, 'Motion commands are rate limited')

        pos = position(sim, now)
        result = 'ok'
        if command == 'open':
            if sim.phase in ('up', 'moving_up'):
                result = 'already_up'
            else:
                _set_phase(sim, 'moving_up', pos, now)
        elif command == 'close':
            if sim.phase in ('down', 'moving_down'):
                result = 'already_down'
            else:
                _set_phase(sim, 'moving_down', pos, now)
        else:
            if sim.phase in ('moving_up', 'moving_down'):
                _set_phase(sim, 'stopped', pos, now)
            else:
                result = 'not_moving'

        sim.last_nonce = nonce
        if command != 'stop':
            sim.last_motion_command = command
            sim.last_motion_command_at = now
        _log(sim, now, command, result=result)
        sim.save()
        _sync_gate(gate, sim, now)

    body = state(sim, now)
    body.update({'ok': True, 'command': command, 'result': result})
    return body


# ---------------------------------------------------------------- remote capture
#
# The 433 MHz controller's capture, simulated: listening "hears" a made-up
# remote at once, so the admin UI's capture flow can be tried end to end
# before the hardware exists. Like the firmware, only fingerprints leave it.

CAPTURE_BUTTONS = ('up', 'down', 'stop')


def code_fingerprint(code, bits):
    """First 8 hex of SHA-256("<bits>:<code as upper hex>"), as the firmware computes it."""
    digits = (bits + 3) // 4
    return hashlib.sha256(f'{bits}:{code:0{digits}X}'.encode()).hexdigest()[:8]


def accept_request(gate, nonce, ts, now=None):
    """The nonce and timestamp rules of a command, for requests that move nothing."""
    now = now or timezone.now()
    try:
        nonce = int(nonce)
        ts = float(ts)
    except (TypeError, ValueError):
        raise CommandRejected(400, 'nonce and ts are required')
    with transaction.atomic():
        sim = SimulatedBarrier.objects.select_for_update().get(pk=get_simulator(gate).pk)
        if nonce <= sim.last_nonce:
            raise CommandRejected(409, 'Replayed nonce')
        if abs(now.timestamp() - ts) > TIMESTAMP_WINDOW_SECONDS:
            raise CommandRejected(401, 'Timestamp outside the allowed window')
        sim.last_nonce = nonce
        sim.save(update_fields=['last_nonce'])


def _health(gate):
    gate.refresh_from_db(fields=['controller_health'])
    health = dict(gate.controller_health or {})
    health.setdefault('buttons', {b: {'set': False} for b in CAPTURE_BUTTONS})
    return health


def capture(gate, button, job=None):
    """Listen for a remote button: the simulator hears one straight away."""
    if button not in CAPTURE_BUTTONS:
        raise CommandRejected(400, 'bad_request')
    code = secrets.randbits(24)
    report = {
        'state': 'captured', 'button': button, 'age_s': 0, 'edges': 250, 'frames': 5,
        'fingerprint': code_fingerprint(code, 24), 'bits': 24, 'pulse_us': 350,
    }
    if isinstance(job, int) and not isinstance(job, bool) and job > 0:
        report['job'] = job
    health = _health(gate)
    health['capture'] = report
    GateDevice.objects.filter(pk=gate.pk).update(controller_health=health)
    gate.controller_health = health
    return report


def save_code(gate, button):
    """Keep the last capture as `button`'s code. Returns the buttons as reported."""
    health = _health(gate)
    report = health.get('capture') or {}
    if report.get('state') != 'captured':
        raise CommandRejected(409, 'no_capture')
    if report.get('button') != button:
        raise CommandRejected(409, 'captured_for_another_button')
    health['buttons'][button] = {
        'set': True, 'fingerprint': report['fingerprint'], 'bits': report['bits'], 'pulse_us': report['pulse_us'],
    }
    health['capture'] = dict(report, state='saved')
    GateDevice.objects.filter(pk=gate.pk).update(controller_health=health)
    gate.controller_health = health
    return health['buttons']


def execute_now(gate, command):
    """Run a command in-process (admin test page), with a fresh nonce and timestamp."""
    return execute(gate, command, next_nonce(get_simulator(gate)), time.time())
