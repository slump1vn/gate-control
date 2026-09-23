"""
Agent orchestration.

- One GateWorker thread per enabled gate with a camera: grab frames, run the
  presence trigger, capture a burst, request a decision, open on a grant.
- The Agent syncs config from the LPR service (hot reload, no restart),
  executes manual commands from the service's queue, and reports camera state.

Every failure path ends in "do nothing": the barrier stays where it is and the
guard's buttons still work.
"""

import logging
import threading
import time

from . import metrics
from .api import ApiError
from .camera import CameraError, EndOfSource, build_source, crop_roi, encode_jpeg
from .controller import ControllerClient, ControllerError
from .trigger import PresenceTrigger, prepare

logger = logging.getLogger('gate_agent')

RECONNECT_BACKOFF = (1, 2, 5, 10, 30)
DIRECTION_LABELS = {'in': 'entry', 'out': 'exit'}


def make_controller(gate):
    """A ControllerClient for a gate config, or None if it cannot be commanded."""
    if not gate.get('controller_url') or not gate.get('controller_token'):
        return None
    return ControllerClient(gate['controller_url'], gate['controller_token'])


class GateWorker(threading.Thread):
    """Watches one camera of one gate. A gate has one worker per camera."""

    def __init__(self, gate, camera, config, settings, api, controller, source_factory=build_source,
                 clock=time.monotonic, sleep=time.sleep, allow_actuation=True):
        super().__init__(name=f"gate-{gate['id']}-camera-{camera['id']}", daemon=True)
        self.gate = gate
        self.camera = camera
        self.config = config
        self.settings = settings
        self.api = api
        self.controller = controller
        self.source_factory = source_factory
        self.clock = clock
        self.sleep = sleep
        self.allow_actuation = allow_actuation
        direction = camera.get('direction')
        self.label = f"{gate['name']} ({DIRECTION_LABELS.get(direction, 'camera ' + str(camera['id']))})"
        self.stop_event = threading.Event()
        self.source = None
        self.camera_status = 'reconnecting'
        self.finished = False
        self.trigger = PresenceTrigger(
            motion_threshold=self.camera.get('motion_threshold', 0.02),
            settle_ms=self.camera.get('settle_ms', 800),
            cooldown_s=self.camera.get('cooldown_s', 5),
            presence_factor=settings.presence_factor,
            max_attempts=settings.max_attempts,
            max_occupied_seconds=settings.max_occupied_seconds,
        )

    # -- camera ---------------------------------------------------------
    def _grab(self):
        if self.source is None:
            self.source = self.source_factory(self.camera)
            logger.info('Gate %s: camera source %s', self.label, self.source.describe())
        try:
            frame = self.source.grab()
        except CameraError:
            self._close_source()
            raise
        metrics.FRAMES.labels(gate=self.label, result='ok').inc()
        self.camera_status = 'streaming'
        return crop_roi(frame, self.camera.get('roi'))

    def _close_source(self):
        if self.source is not None:
            try:
                self.source.close()
            finally:
                self.source = None

    # -- main loop ------------------------------------------------------
    def run(self):
        failures = 0
        while not self.stop_event.is_set():
            try:
                frame = self._grab()
                failures = 0
            except EndOfSource:
                logger.info('Gate %s: replay finished', self.label)
                self.finished = True
                break
            except CameraError as exc:
                metrics.FRAMES.labels(gate=self.label, result='error').inc()
                self.camera_status = exc.status
                delay = RECONNECT_BACKOFF[min(failures, len(RECONNECT_BACKOFF) - 1)]
                failures += 1
                logger.warning('Gate %s: camera %s (%s); retrying in %ss', self.label, exc.status, exc, delay)
                self.stop_event.wait(delay)
                continue
            if self.trigger.update(prepare(frame), self.clock()):
                self.handle_trigger(frame)
            self.sleep(self.settings.frame_interval)
        self._close_source()

    def stop(self):
        self.stop_event.set()

    # -- one vehicle ----------------------------------------------------
    def capture_burst(self, first_frame):
        frames = [first_frame]
        for _ in range(max(0, self.config.get('burst_frames', 3) - 1)):
            self.sleep(self.settings.burst_interval)
            try:
                frames.append(self._grab())
            except (CameraError, EndOfSource):
                break
        max_bytes = self.config.get('max_upload_bytes', 2 * 1024 * 1024)
        encoded = []
        for frame in frames:
            try:
                data, quality = encode_jpeg(frame, max_bytes, quality=self.settings.jpeg_quality)
            except ValueError as exc:
                logger.warning('Gate %s: frame dropped: %s', self.label, exc)
                continue
            if quality < self.settings.jpeg_quality:
                logger.warning('Gate %s: frame re-encoded at quality %d to fit the upload limit', self.label, quality)
            encoded.append(data)
        return encoded

    def handle_trigger(self, first_frame):
        """Capture a burst, ask for a decision, open on a grant. Returns the decision dict or None."""
        metrics.TRIGGERS.labels(gate=self.label).inc()
        started = self.clock()
        result = None
        try:
            frames = self.capture_burst(first_frame)
            if not frames:
                return None
            timeout = self.config.get('decide_timeout_seconds', 8) + 5
            result = self.api.decide(self.gate['id'], frames, timeout=timeout, camera_id=self.camera['id'])
            metrics.DECISION_ROUNDTRIP.labels(gate=self.label).observe(self.clock() - started)
            metrics.DECISIONS.labels(gate=self.label, decision=result['decision'], reason=result['reason']).inc()
            logger.info(
                'Gate %s: %s (%s) plate=%s confidence=%s actuate=%s latency=%sms',
                self.label, result['decision'], result['reason'], result.get('plate'),
                result.get('confidence'), result.get('actuate'), result.get('decision_latency_ms'),
            )
            if result.get('actuate'):
                self.open_barrier(result['event_id'])
            return result
        except ApiError as exc:
            # The service is down or refused: treat as a denial, never actuate
            logger.error('Gate %s: decision request failed: %s', self.label, exc)
            return None
        except Exception:
            logger.exception('Gate %s: decision request failed', self.label)
            return None
        finally:
            self.trigger.decided(bool(result and result.get('decision') == 'granted'), self.clock())

    def open_barrier(self, event_id):
        if not self.allow_actuation:
            logger.info('Gate %s: actuation disabled in this mode; not opening', self.label)
            self.api.command_result(event_id, False, 'not_sent_agent_mode')
            return
        send_and_report(self.api, self.controller, self.label, 'open', event_id)
        if self.gate.get('auto_close') == 'software' and self.controller is not None:
            delay = self.gate.get('auto_close_seconds', 10)
            timer = threading.Timer(delay, self._software_close)
            timer.daemon = True
            timer.start()

    def _software_close(self):
        # Only reachable when the gate has a safety input (enforced by the LPR service)
        try:
            self.controller.send('close')
            metrics.COMMANDS.labels(gate=self.label, command='close', result='ok').inc()
        except ControllerError as exc:
            metrics.COMMANDS.labels(gate=self.label, command='close', result='failed').inc()
            logger.error('Gate %s: software close failed: %s', self.label, exc)


def send_and_report(api, controller, label, command, event_id):
    """Send a command to a controller and report the outcome for the event."""
    if controller is None:
        logger.error('Gate %s: no usable controller for %s', label, command)
        metrics.COMMANDS.labels(gate=label, command=command, result='failed').inc()
        _report(api, event_id, False, 'no_controller')
        return False
    try:
        body = controller.send(command)
        result = body.get('result', 'ok')
        metrics.COMMANDS.labels(gate=label, command=command, result='ok').inc()
        logger.info('Gate %s: %s sent (%s)', label, command, result)
        _report(api, event_id, True, result)
        return True
    except ControllerError as exc:
        metrics.COMMANDS.labels(gate=label, command=command, result='failed').inc()
        logger.error('Gate %s: %s failed: %s', label, command, exc.message)
        _report(api, event_id, False, exc.message)
        return False


def _report(api, event_id, sent, result):
    try:
        api.command_result(event_id, sent, result)
    except ApiError as exc:
        logger.error('Could not report command result for event %s: %s', event_id, exc)


def _worker_signature(gate, camera):
    """What must stay the same for a running worker to be kept."""
    return (
        camera.get('id'), camera.get('config_version'), camera.get('host'), camera.get('password'),
        camera.get('direction'), camera.get('roi') and tuple(sorted(camera['roi'].items())),
        gate.get('controller_url'), gate.get('controller_token'), gate.get('auto_close'),
    )


class Agent:
    def __init__(self, settings, api, worker_factory=GateWorker, clock=time.monotonic):
        self.settings = settings
        self.api = api
        self.worker_factory = worker_factory
        self.clock = clock
        self.config = None
        self.version = None
        self.gates = {}
        self.controllers = {}
        self.workers = {}
        self.signatures = {}
        self.stop_event = threading.Event()

    # -- config ---------------------------------------------------------
    def sync_config(self):
        try:
            config = self.api.agent_config(self.version)
        except Exception as exc:
            logger.error('Config sync failed, keeping the last known config: %s', exc)
            return False
        if config is None:
            return False
        self.apply_config(config)
        return True

    def apply_config(self, config):
        self.config = config
        self.version = config.get('version')
        gates = config.get('gates', [])
        cameras = sum(len(gate.get('cameras') or []) for gate in gates)
        logger.info('Config %s: mode=%s, %d gate(s), %d camera(s)',
                    self.version, config.get('mode'), len(gates), cameras)
        seen_gates = set()
        seen_workers = set()
        for gate in gates:
            gate_id = gate['id']
            seen_gates.add(gate_id)
            for message in gate.get('config_errors', []):
                logger.warning('Gate %s: %s', gate['name'], message)
            if gate.get('controller_token_error'):
                logger.error('Gate %s: controller token unavailable (%s)', gate['name'], gate['controller_token_error'])
            self.gates[gate_id] = gate
            self.controllers[gate_id] = make_controller(gate)
            if not gate.get('cameras'):
                logger.info('Gate %s has no enabled camera; commands only', gate['name'])
            for camera in gate.get('cameras') or []:
                key = (gate_id, camera['id'])
                seen_workers.add(key)
                signature = _worker_signature(gate, camera)
                if self.signatures.get(key) != signature:
                    self._restart_worker(gate, camera)
                    self.signatures[key] = signature
        for key in list(self.workers):
            if key not in seen_workers:
                self._stop_worker(key)
                self.signatures.pop(key, None)
        for gate_id in list(self.gates):
            if gate_id not in seen_gates:
                logger.info('Gate %s removed or disabled', self.gates[gate_id]['name'])
                self.gates.pop(gate_id)
                self.controllers.pop(gate_id, None)

    def _restart_worker(self, gate, camera):
        key = (gate['id'], camera['id'])
        self._stop_worker(key)
        worker = self.worker_factory(
            gate, camera, self.config, self.settings, self.api, self.controllers.get(gate['id']),
        )
        self.workers[key] = worker
        worker.start()

    def _stop_worker(self, key):
        worker = self.workers.pop(key, None)
        if worker is not None:
            worker.stop()

    # -- manual commands ------------------------------------------------
    def process_commands(self):
        try:
            commands = self.api.agent_commands()
        except Exception as exc:
            logger.error('Command poll failed: %s', exc)
            return 0
        for command in commands:
            gate = self.gates.get(command['gate_id'])
            label = gate['name'] if gate else f"gate {command['gate_id']}"
            send_and_report(self.api, self.controllers.get(command['gate_id']), label,
                            command['command'], command['event_id'])
        return len(commands)

    # -- status ---------------------------------------------------------
    def report_status(self):
        cameras = [
            {'id': worker.camera['id'], 'status': worker.camera_status}
            for worker in self.workers.values()
        ]
        if not cameras:
            return
        try:
            self.api.agent_status(cameras)
        except Exception as exc:
            logger.error('Status report failed: %s', exc)

    # -- run ------------------------------------------------------------
    def run_forever(self):
        self.sync_config()
        next_config = self.clock() + self.settings.config_interval
        next_status = self.clock()
        while not self.stop_event.is_set():
            now = self.clock()
            if now >= next_config:
                self.sync_config()
                next_config = now + self.settings.config_interval
            if now >= next_status:
                self.report_status()
                next_status = now + self.settings.status_interval
            self.process_commands()
            self.stop_event.wait(self.settings.command_poll_interval)
        self.shutdown()

    def shutdown(self):
        for key in list(self.workers):
            self._stop_worker(key)
