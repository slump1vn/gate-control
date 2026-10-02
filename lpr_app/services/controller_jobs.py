"""
Installer jobs on a gate controller, asked for from the admin UI: capture a
remote button's 433 MHz code, then keep it as that button's code.

    admin  --POST controller-jobs-->  queued
    agent  --GET agent-jobs-------->  dispatched   (claimed once)
    agent  --POST /capture to the controller, reports-->  running  (capture)
                                                     or  done     (save_code)
    controller heartbeat "capture": {job, state}  -->  done | failed
    nobody picks it up / no result in time  -->  expired / failed

A capture moves nothing, and neither does keeping it, so both run in shadow
mode too. The code itself never leaves the controller; the service sees only
fingerprints (first 8 hex of SHA-256 of "<bits>:<code>").
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import ControllerJob, GateConfigChange

logger = logging.getLogger(__name__)

# Controllers that can capture: the 433 MHz one, and the simulator standing in for it
CAPTURE_CONTROLLERS = ('esp32_rf', 'simulator')
BUTTONS = tuple(b for b, _ in ControllerJob.BUTTONS)
# A job the agent has not taken within this long is not run late
CLAIM_TTL_SECONDS = 30
# A capture has this long beyond its listening time to report back
RESULT_GRACE_SECONDS = 30
CAPTURE_STATES = ('capturing', 'captured', 'nothing', 'aborted', 'saved', 'idle')
FINISHED_CAPTURE = {'captured': 'done', 'nothing': 'failed', 'aborted': 'failed'}


class JobRefused(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


def create(gate, kind, button, user, seconds=10):
    if gate.controller_type not in CAPTURE_CONTROLLERS:
        raise JobRefused('This controller cannot capture remote codes', 'NOT_SUPPORTED')
    if kind not in dict(ControllerJob.KINDS):
        raise JobRefused('kind must be capture or save_code', 'INVALID_KIND')
    if button not in BUTTONS:
        raise JobRefused('button must be up, down or stop', 'INVALID_BUTTON')
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        raise JobRefused('seconds must be a number', 'INVALID_SECONDS')
    if not 2 <= seconds <= 15:
        raise JobRefused('seconds must be between 2 and 15', 'INVALID_SECONDS')
    expire_stale()
    with transaction.atomic():
        if ControllerJob.objects.select_for_update().filter(
                gate=gate, state__in=ControllerJob.ACTIVE_STATES).exists():
            raise JobRefused('Another job is still running on this controller', 'BUSY')
        detail = {}
        if kind == 'save_code':
            # What the button held before: by the time the agent reports, the
            # controller's heartbeat may already show the new code
            detail['previous_fingerprint'] = _stored(gate, button).get('fingerprint')
        return ControllerJob.objects.create(
            gate=gate, kind=kind, button=button, seconds=seconds, detail=detail,
            created_by=user if getattr(user, 'is_authenticated', False) else None,
        )


def expire_stale(now=None):
    """Jobs nobody took in time never run late; captures that never reported fail."""
    now = now or timezone.now()
    ControllerJob.objects.filter(
        state='queued', created_at__lt=now - timedelta(seconds=CLAIM_TTL_SECONDS),
    ).update(state='expired', result='not picked up by the agent', updated_at=now)
    for job in ControllerJob.objects.filter(state__in=('dispatched', 'running')):
        deadline = job.updated_at + timedelta(seconds=job.seconds + RESULT_GRACE_SECONDS)
        if now > deadline:
            ControllerJob.objects.filter(pk=job.pk, state=job.state).update(
                state='failed', result='no result from the controller', updated_at=now)


def claim(now=None):
    """Jobs for the agent, each handed out once."""
    expire_stale(now)
    claimed = []
    for job in ControllerJob.objects.filter(state='queued').select_related('gate').order_by('created_at'):
        if ControllerJob.objects.filter(pk=job.pk, state='queued').update(
                state='dispatched', updated_at=timezone.now()):
            job.state = 'dispatched'
            claimed.append(job)
    return claimed


def record_agent_result(job, sent, result, detail=None):
    """What the agent got from the controller for this job."""
    result = str(result or '')[:100]
    if not sent:
        job.state, job.result = 'failed', result or 'failed'
    elif job.kind == 'capture':
        # Listening now; the outcome comes with the controller's heartbeat
        if job.state in ('queued', 'dispatched'):
            job.state, job.result = 'running', result or 'capturing'
    else:
        job.state, job.result = 'done', result or 'saved'
        if isinstance(detail, dict):
            job.detail = dict(job.detail or {}, **_clean_code(detail))
        _audit_saved_code(job)
    job.save(update_fields=['state', 'result', 'detail', 'updated_at'])
    return job


def apply_capture_report(gate, capture):
    """A controller reported its capture (heartbeat, or the simulator at once): finish its job."""
    job_id = capture.get('job')
    outcome = FINISHED_CAPTURE.get(capture.get('state'))
    if not job_id or not outcome:
        return None
    job = ControllerJob.objects.filter(
        pk=job_id, gate=gate, kind='capture', state__in=('dispatched', 'running'),
    ).first()
    if job is None:
        return None
    job.state = outcome
    job.result = capture['state']
    job.detail = {k: capture[k] for k in ('fingerprint', 'bits', 'pulse_us', 'frames', 'edges') if k in capture}
    job.save(update_fields=['state', 'result', 'detail', 'updated_at'])
    return job


def _stored(gate, button):
    return ((gate.controller_health or {}).get('buttons') or {}).get(button) or {}


def _clean_code(entry):
    clean = {}
    if isinstance(entry.get('set'), bool):
        clean['set'] = entry['set']
    _code_fields(entry, clean)
    return clean


def _audit_saved_code(job):
    GateConfigChange.objects.create(
        user=job.created_by,
        object_type='gatedevice',
        object_id=job.gate.pk,
        object_repr=str(job.gate)[:255],
        action='update',
        changes={f'remote_code_{job.button}': {
            'old': (job.detail or {}).get('previous_fingerprint'),
            'new': (job.detail or {}).get('fingerprint'),
        }},
    )


# ---------------------------------------------------------------- heartbeat fields

def _int(value, low=0, high=10**9):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _fingerprint(value):
    return isinstance(value, str) and len(value) == 8 and all(c in '0123456789abcdef' for c in value)


def _code_fields(src, out):
    if _fingerprint(src.get('fingerprint')):
        out['fingerprint'] = src['fingerprint']
    if _int(src.get('bits'), 1, 64):
        out['bits'] = src['bits']
    if _int(src.get('pulse_us'), 1, 100000):
        out['pulse_us'] = src['pulse_us']


def clean_heartbeat(body):
    """The heartbeat's capture and buttons objects, type-checked (anything else is dropped)."""
    out = {}
    capture = body.get('capture')
    if isinstance(capture, dict) and capture.get('state') in CAPTURE_STATES:
        cap = {'state': capture['state']}
        if capture.get('button') in BUTTONS:
            cap['button'] = capture['button']
        for key in ('job', 'age_s', 'edges', 'frames'):
            if _int(capture.get(key)):
                cap[key] = capture[key]
        _code_fields(capture, cap)
        out['capture'] = cap
    buttons = body.get('buttons')
    if isinstance(buttons, dict):
        clean = {}
        for name in BUTTONS:
            entry = buttons.get(name)
            if isinstance(entry, dict) and isinstance(entry.get('set'), bool):
                item = {'set': entry['set']}
                _code_fields(entry, item)
                clean[name] = item
        if clean:
            out['buttons'] = clean
    return out


def serialize(job):
    return {
        'id': job.id,
        'gate_id': job.gate_id,
        'kind': job.kind,
        'button': job.button,
        'seconds': job.seconds,
        'state': job.state,
        'result': job.result,
        'detail': job.detail or {},
        'created_by': job.created_by.get_username() if job.created_by else None,
        'created_at': job.created_at.isoformat() if job.created_at else None,
        'updated_at': job.updated_at.isoformat() if job.updated_at else None,
    }


def agent_view(job):
    return {'id': job.id, 'gate_id': job.gate_id, 'kind': job.kind, 'button': job.button, 'seconds': job.seconds}
