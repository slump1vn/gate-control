"""
Gate access decisions.

A decision request carries a short burst of frames from one gate camera. Each
frame runs through the existing detection + OCR pipeline in a bounded worker
pool; the request waits at most GATE_DECIDE_TIMEOUT seconds and stops early
once enough frames agree. The nearest plate in each frame (largest box) is
that frame's read. A plate is granted only if at least GATE_CONSENSUS_MIN
frames agree on it, the best agreeing confidence reaches GATE_MIN_CONFIDENCE,
and it matches a valid registry entry exactly. Everything else is denied.

Only the frame behind the decision is kept, as the event's evidence; the other
frames are deleted, including frames that finish after the deadline.
"""

import logging
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import List, Optional

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .. import metrics
from ..models import AccessEvent, GateCamera, GateDevice, ProcessingLog, UploadedImage
from ..utils.plates import normalize_plate
from . import plate_matcher
from .image_processing_service import ImageProcessingService

logger = logging.getLogger(__name__)

FRAME_SOURCE = 'gate'

_executor = ThreadPoolExecutor(
    max_workers=getattr(settings, 'GATE_WORKER_THREADS', 3),
    thread_name_prefix='gate-frame',
)


@dataclass
class FrameRead:
    image_id: int
    ok: bool
    plate_raw: str = ''
    plate: str = ''
    confidence: Optional[float] = None
    error: str = ''


@dataclass
class Outcome:
    reason: str
    plate: str = ''
    plate_raw: str = ''
    confidence: Optional[float] = None
    frames_read: int = 0
    frames_agreed: int = 0
    evidence: Optional[FrameRead] = None
    match: Optional[plate_matcher.MatchResult] = None

    @property
    def granted(self):
        return self.reason == 'whitelist_hit'


@dataclass
class Decision:
    event: AccessEvent
    outcome: Outcome
    actuate: bool
    discarded: List[int] = field(default_factory=list)


def effective_mode():
    """'live' only when explicitly configured; anything else is shadow."""
    return 'live' if str(getattr(settings, 'GATE_MODE', 'shadow')).lower() == 'live' else 'shadow'


def can_actuate(gate, mode):
    """
    Whether commands for this gate may be sent. Shadow mode forbids physical
    actuation; a simulated barrier moves no hardware, so it always may.
    """
    return gate is not None and (mode == 'live' or gate.is_simulated)


# ---------------------------------------------------------------------------
# Frame reading
# ---------------------------------------------------------------------------

def primary_plate(api_response):
    """
    The plate of the nearest vehicle in a frame: the detection with the largest
    plate box that has OCR text. Returns (text, confidence) or ('', None).
    """
    detections = (api_response or {}).get('detections') or []
    if isinstance(detections, dict):
        detections = list(detections.values())

    best = None
    for det in detections:
        ocr = det.get('ocr') or []
        if not ocr or not isinstance(ocr[0], dict) or not ocr[0].get('text'):
            continue
        coords = (det.get('plate') or {}).get('coordinates') or {}
        try:
            area = (float(coords['x2']) - float(coords['x1'])) * (float(coords['y2']) - float(coords['y1']))
        except (KeyError, TypeError, ValueError):
            area = 0.0
        if best is None or area > best[0]:
            best = (area, ocr[0]['text'], ocr[0].get('confidence'))

    if best is None:
        return '', None
    confidence = best[2]
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    return best[1], confidence


def create_frame_record(uploaded_file):
    image = UploadedImage.objects.create(
        original_image=uploaded_file,
        filename=uploaded_file.name,
        processing_status='pending',
        source=FRAME_SOURCE,
    )
    ProcessingLog.objects.create(
        uploaded_image=image, status='started', message='Gate frame processing started',
    )
    return image


def read_frame(image_id):
    """Run one frame through the recognition pipeline. Runs in a worker thread."""
    try:
        image = UploadedImage.objects.get(pk=image_id)
        result = ImageProcessingService.process_uploaded_image(image, save_image=True)
        if not result.get('success'):
            return FrameRead(image_id=image_id, ok=False, error=str(result.get('error', 'processing failed')))
        image.refresh_from_db()
        raw, confidence = primary_plate(image.api_response)
        return FrameRead(
            image_id=image_id, ok=True, plate_raw=raw,
            plate=normalize_plate(raw), confidence=confidence,
        )
    except Exception as exc:
        logger.exception('Gate frame %s failed', image_id)
        return FrameRead(image_id=image_id, ok=False, error=str(exc))
    finally:
        _release_thread_connection()


def discard_frame(image_id):
    """Delete a frame's files (original, processed, comparison) and its record."""
    try:
        image = UploadedImage.objects.filter(pk=image_id).first()
        if image is None:
            return
        paths = []
        if image.original_image:
            original = image.original_image.path
            paths.append(original)
            paths.append(os.path.join(os.path.dirname(original), f'comparison_{os.path.basename(original)}'))
        if image.processed_image:
            paths.append(image.processed_image.path)
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                logger.warning('Could not delete gate frame file %s', path)
        image.delete()
    except Exception:
        logger.exception('Failed to discard gate frame %s', image_id)
    finally:
        _release_thread_connection()


def _release_thread_connection():
    # Worker threads get their own DB connection; close it when done.
    if threading.current_thread() is not threading.main_thread():
        connection.close()


def _discard_when_done(future):
    read = future.result()
    logger.info('Gate frame %s finished after the decision; discarding', read.image_id)
    discard_frame(read.image_id)


def read_frames(image_ids, deadline):
    """
    Read frames in parallel until all finish, consensus is reached, or the
    deadline passes. Returns (reads, pending_count). Frames still running are
    discarded when they finish; frames not yet started are cancelled.
    """
    futures = {_executor.submit(read_frame, image_id): image_id for image_id in image_ids}
    pending = set(futures)
    reads = []

    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
        reads.extend(f.result() for f in done)
        if _consensus_reached(reads):
            break

    for future in pending:
        if future.cancel():
            discard_frame(futures[future])
        else:
            future.add_done_callback(_discard_when_done)

    return reads, len(pending)


def _plate_groups(reads):
    groups = defaultdict(list)
    for r in reads:
        if r.ok and r.plate:
            groups[r.plate].append(r)
    return groups


def _consensus_reached(reads):
    need = settings.GATE_CONSENSUS_MIN
    return any(len(group) >= need for group in _plate_groups(reads).values())


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------

def _conf(read):
    return read.confidence if read.confidence is not None else 0.0


def evaluate(reads, pending=0, at=None):
    """Apply the consensus, confidence and registry rules to a set of frame reads."""
    need = settings.GATE_CONSENSUS_MIN
    ok_reads = [r for r in reads if r.ok]
    groups = _plate_groups(reads)

    if not ok_reads:
        return Outcome(reason='inference_timeout' if pending else 'processing_error')

    if not groups:
        reason = 'inference_timeout' if pending >= need else 'no_plate'
        return Outcome(reason=reason, frames_read=len(ok_reads), evidence=ok_reads[0])

    plate, agreeing = max(groups.items(), key=lambda kv: (len(kv[1]), max(_conf(r) for r in kv[1])))
    best = max(agreeing, key=_conf)
    base = dict(
        plate=plate, plate_raw=best.plate_raw, confidence=best.confidence,
        frames_read=len(ok_reads), frames_agreed=len(agreeing), evidence=best,
    )

    if len(agreeing) < need:
        reason = 'inference_timeout' if len(agreeing) + pending >= need else 'no_consensus'
        return Outcome(reason=reason, **base)

    if _conf(best) < settings.GATE_MIN_CONFIDENCE:
        return Outcome(reason='low_confidence', **base)

    match = plate_matcher.match(plate, at)
    return Outcome(reason=match.reason, match=match, **base)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def decide(gate_id, uploaded_files, started=None, is_test=False, camera_id=None):
    """
    Make and record a decision for a burst of frames from one gate.

    camera_id says which of the gate's cameras saw the vehicle, and with it
    which way the vehicle was going. A gate whose exit_policy is 'any' opens
    for every vehicle leaving: the plate is still read and logged, so the
    entry and exit of a visitor can still be matched up afterwards.

    is_test marks decisions run from the admin test page; they are logged but
    kept out of the gate metrics.
    """
    started = started if started is not None else time.monotonic()
    mode = effective_mode()
    gate = GateDevice.objects.filter(pk=gate_id).first() if gate_id else None

    if gate is None or not gate.is_enabled:
        event = AccessEvent.objects.create(
            gate=gate, decision='denied', reason='device_disabled', mode=mode,
            decision_latency_ms=_elapsed_ms(started), is_test=is_test,
        )
        if not is_test:
            metrics.record_gate_decision(event)
        return Decision(event=event, outcome=Outcome(reason='device_disabled'), actuate=False)

    direction = camera_direction(gate, camera_id)
    images = [create_frame_record(f) for f in uploaded_files]
    deadline = started + settings.GATE_DECIDE_TIMEOUT
    reads, pending = read_frames([img.pk for img in images], deadline)
    outcome = evaluate(reads, pending, at=timezone.now())

    evidence_id = outcome.evidence.image_id if outcome.evidence and outcome.evidence.ok else None
    kept, discarded = frames_to_keep(reads, evidence_id, granted=outcome.granted)
    for image_id in discarded:
        discard_frame(image_id)

    match = outcome.match
    # Leaving a gate that is open to everyone: record what was read, open anyway.
    exit_free = direction == 'out' and gate.exit_policy == 'any' and not outcome.granted
    granted = outcome.granted or exit_free
    event = AccessEvent.objects.create(
        gate=gate,
        camera_id=camera_id if direction else None,
        direction=direction,
        plate_raw=outcome.plate_raw[:64],
        plate_normalized=outcome.plate[:20],
        confidence=outcome.confidence,
        frames_read=outcome.frames_read,
        frames_agreed=outcome.frames_agreed,
        vehicle=match.vehicle if match else None,
        near_miss_vehicle=match.near_miss if match else None,
        decision='granted' if granted else 'denied',
        reason='exit_free' if exit_free else outcome.reason,
        mode=mode,
        uploaded_image_id=evidence_id,
        command='open' if granted else '',
        decision_latency_ms=_elapsed_ms(started),
        is_test=is_test,
    )
    if kept:
        event.frames.set(kept)
    if not is_test:
        metrics.record_gate_decision(event)
    actuate = granted and can_actuate(gate, mode)
    logger.info(
        'Gate %s decision: %s (%s) plate=%s conf=%s frames=%d/%d mode=%s latency=%dms',
        gate.name, event.decision, event.reason, event.plate_normalized or '-',
        event.confidence, event.frames_agreed, event.frames_read, mode, event.decision_latency_ms,
    )
    return Decision(event=event, outcome=outcome, actuate=actuate, discarded=discarded)


def frames_to_keep(reads, evidence_id, granted):
    """
    Split a burst into the frames worth keeping and the ones to delete.

    Only the evidence frame is kept by default: the others cost disk and say
    little once a plate has been read. When a plate was misread, though, the
    frames that were thrown away are exactly the ones worth looking at, so
    GATE_KEEP_FRAMES can keep the whole burst for refusals, or always.
    """
    policy = str(getattr(settings, 'GATE_KEEP_FRAMES', 'evidence')).lower()
    all_ids = [r.image_id for r in reads]
    keep_all = policy == 'all' or (policy == 'denied' and not granted)
    if keep_all:
        return all_ids, []
    kept = [evidence_id] if evidence_id else []
    return kept, [image_id for image_id in all_ids if image_id != evidence_id]


def camera_direction(gate, camera_id):
    """Which way the camera that took these frames is pointing ('' if unknown)."""
    if not camera_id:
        return ''
    link = GateCamera.objects.filter(gate=gate, camera_id=camera_id).first()
    return link.direction if link else ''


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


# ---------------------------------------------------------------------------
# Manual overrides and the agent command queue
# ---------------------------------------------------------------------------

COMMANDS = ('open', 'close', 'stop')


def create_override(gate, command, user, is_test=False):
    """
    Record a guard's manual command. When the gate may be actuated, the agent
    picks it up from the command queue; otherwise (shadow mode, real
    controller) it is recorded but never dispatched.
    """
    if command not in COMMANDS:
        raise ValueError(f'Unknown command {command!r}')
    mode = effective_mode()
    return AccessEvent.objects.create(
        gate=gate, decision='manual', reason='manual_override', mode=mode,
        command=command, operator=user, is_test=is_test,
        command_result='' if can_actuate(gate, mode) else 'not_sent_shadow_mode',
    )


def run_on_simulator(event):
    """
    Execute a command on a simulated gate in-process (admin test page), so the
    test works without the agent running. Claims the event first, so an agent
    polling the queue cannot run it a second time.
    """
    from . import barrier_simulator

    if not (event.gate and event.gate.is_simulated and event.command in COMMANDS):
        raise ValueError('Only commands for simulated gates can run in-process')
    if not AccessEvent.objects.filter(pk=event.pk, command_result='').update(command_result='dispatched'):
        return None
    try:
        body = barrier_simulator.execute_now(event.gate, event.command)
        record_command_result(event, True, body['result'])
        return body
    except barrier_simulator.CommandRejected as exc:
        record_command_result(event, False, f'rejected: {exc.message}')
        return None


def claim_pending_commands(now=None):
    """
    Return manual commands for the agent to execute, marking each 'dispatched'
    so it runs once. Commands older than GATE_COMMAND_TTL_SECONDS are marked
    'expired' instead: a late 'open' must never fire. 'stop' comes first.
    """
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(seconds=settings.GATE_COMMAND_TTL_SECONDS)
    # Commands that may not be dispatched were created with a non-empty result
    queue = AccessEvent.objects.filter(
        decision='manual', command__in=COMMANDS,
        command_sent=False, command_result='',
    )
    for stale in queue.filter(timestamp__lt=cutoff).select_related('gate'):
        if AccessEvent.objects.filter(pk=stale.pk, command_result='').update(command_result='expired'):
            logger.warning('Manual %s on gate %s expired before the agent picked it up', stale.command, stale.gate)
            metrics.record_gate_command(stale, 'expired')

    claimed = []
    for event in queue.filter(timestamp__gte=cutoff).select_related('gate').order_by('timestamp'):
        if AccessEvent.objects.filter(pk=event.pk, command_result='').update(command_result='dispatched'):
            event.command_result = 'dispatched'
            claimed.append(event)
    claimed.sort(key=lambda e: (e.command != 'stop', e.timestamp))
    return claimed


def record_command_result(event, sent, result):
    """Store what happened when the agent sent a command to the controller."""
    event.command_sent = bool(sent)
    event.command_result = str(result or ('ok' if sent else 'failed'))[:100]
    event.save(update_fields=['command_sent', 'command_result'])
    if not event.is_test:
        metrics.record_gate_command(event, 'ok' if event.command_sent else 'failed')
    if event.gate_id:
        GateDevice.objects.filter(pk=event.gate_id).update(
            last_command_result=f'{event.command}: {event.command_result}'[:100],
        )
