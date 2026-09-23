import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

from PIL import Image, ImageDraw

from gate_agent import __main__ as cli
from gate_agent.agent import Agent, GateWorker, make_controller, send_and_report
from gate_agent.api import ApiError
from gate_agent.camera import CameraAuthError, CameraError, EndOfSource
from gate_agent.config import AgentSettings
from gate_agent.controller import ControllerError


def frame(car=False):
    image = Image.new('RGB', (320, 180), (150, 150, 150))
    if car:
        ImageDraw.Draw(image).rectangle([60, 40, 260, 170], fill=(40, 40, 40))
    return image


def camera_config(camera_id, direction='in'):
    return {
        'id': camera_id, 'name': f'Camera {camera_id}', 'direction': direction,
        'host': '192.168.1.64', 'snapshot_path': '/snap', 'prefer_snapshot': True,
        'roi': None, 'motion_threshold': 0.02, 'settle_ms': 800, 'cooldown_s': 5, 'config_version': 1,
        'password': 'pw',
    }


def gate_config(gate_id=1, camera=True, cameras=None, **extra):
    """camera=True gives the gate one entry camera; pass cameras=[...] for more."""
    if cameras is None:
        cameras = [camera_config(10 + gate_id)] if camera else []
    gate = {
        'id': gate_id, 'name': f'Gate {gate_id}', 'controller_type': 'simulator',
        'controller_url': f'http://lpr/api/v1/gate/sim/{gate_id}/', 'controller_token': 'tok',
        'auto_close': 'controller', 'auto_close_seconds': 10, 'config_errors': [],
        'exit_policy': 'registered',
        'cameras': cameras,
    }
    gate.update(extra)
    return gate


def config(*gates, version='v1', mode='shadow'):
    return {'version': version, 'mode': mode, 'burst_frames': 3, 'decide_timeout_seconds': 8,
            'max_upload_bytes': 2 * 1024 * 1024, 'gates': list(gates)}


class FakeSource:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    def grab(self):
        if not self.frames:
            raise EndOfSource()
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True

    def describe(self):
        return 'fake'


def settings(**kw):
    s = AgentSettings(agent_token='t', frame_interval=0, burst_interval=0)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.4
        return self.t


class GateWorkerTest(unittest.TestCase):
    def make_worker(self, frames, api=None, controller=None, gate=None, camera=None, **kw):
        api = api or mock.Mock()
        gate = gate or gate_config()
        worker = GateWorker(
            gate, camera or gate['cameras'][0], config(), settings(), api, controller,
            source_factory=lambda camera: FakeSource(frames), clock=Clock(), sleep=lambda s: None, **kw,
        )
        return worker, api

    def test_vehicle_triggers_burst_decision_and_open(self):
        api = mock.Mock()
        api.decide.return_value = {'decision': 'granted', 'reason': 'whitelist_hit', 'actuate': True,
                                   'event_id': 7, 'plate': '30A12345'}
        controller = mock.Mock()
        controller.send.return_value = {'result': 'ok'}
        frames = [frame()] * 4 + [frame(car=True)] * 8
        worker, _ = self.make_worker(frames, api, controller)
        worker.run()
        self.assertTrue(worker.finished)
        api.decide.assert_called_once()
        gate_id, jpegs = api.decide.call_args.args
        self.assertEqual(gate_id, 1)
        self.assertEqual(len(jpegs), 3)
        self.assertTrue(all(j[:2] == b'\xff\xd8' for j in jpegs))
        self.assertEqual(api.decide.call_args.kwargs['timeout'], 13)
        controller.send.assert_called_once_with('open')
        api.command_result.assert_called_once_with(7, True, 'ok')
        self.assertEqual(worker.camera_status, 'streaming')

    def test_denied_never_opens(self):
        api = mock.Mock()
        api.decide.return_value = {'decision': 'denied', 'reason': 'not_registered', 'actuate': False, 'event_id': 8}
        controller = mock.Mock()
        worker, _ = self.make_worker([frame()] * 4 + [frame(car=True)] * 8, api, controller)
        worker.run()
        controller.send.assert_not_called()
        self.assertFalse(worker.trigger.last_granted)

    def test_api_failure_is_a_denial(self):
        api = mock.Mock()
        api.decide.side_effect = ApiError('HTTP 500', 500)
        controller = mock.Mock()
        worker, _ = self.make_worker([], api, controller)
        self.assertIsNone(worker.handle_trigger(frame(car=True)))
        controller.send.assert_not_called()
        self.assertFalse(worker.trigger.awaiting_decision)

    def test_unexpected_failure_is_a_denial(self):
        api = mock.Mock()
        api.decide.side_effect = RuntimeError('boom')
        worker, _ = self.make_worker([], api)
        self.assertIsNone(worker.handle_trigger(frame(car=True)))

    def test_actuation_disabled_reports_not_sent(self):
        api = mock.Mock()
        controller = mock.Mock()
        worker, _ = self.make_worker([], api, controller, allow_actuation=False)
        worker.open_barrier(9)
        controller.send.assert_not_called()
        api.command_result.assert_called_once_with(9, False, 'not_sent_agent_mode')

    def test_burst_stops_at_end_of_source_and_skips_oversized(self):
        worker, api = self.make_worker([frame(car=True)])
        self.assertEqual(len(worker.capture_burst(frame(car=True))), 2)
        worker.config['max_upload_bytes'] = 100
        self.assertEqual(worker.capture_burst(frame(car=True)), [])
        worker.config['max_upload_bytes'] = 2 * 1024 * 1024
        worker.source = FakeSource([])
        api.decide.return_value = {'decision': 'denied', 'reason': 'no_plate', 'actuate': False, 'event_id': 1}
        worker.config['max_upload_bytes'] = 100
        self.assertIsNone(worker.handle_trigger(frame(car=True)))
        api.decide.assert_not_called()

    def test_camera_errors_set_status_and_back_off(self):
        worker, _ = self.make_worker([CameraAuthError('bad password')])
        waits = []
        worker.stop_event.wait = lambda delay: (waits.append(delay), worker.stop())
        worker.run()
        self.assertEqual(worker.camera_status, 'auth_failed')
        self.assertEqual(waits, [1])

    def test_roi_applied(self):
        gate = gate_config()
        gate['cameras'][0]['roi'] = {'x': 0.0, 'y': 0.0, 'w': 0.5, 'h': 0.5}
        worker, _ = self.make_worker([frame()], gate=gate)
        self.assertEqual(worker._grab().size, (160, 90))

    def test_software_auto_close(self):
        api = mock.Mock()
        controller = mock.Mock()
        controller.send.return_value = {'result': 'ok'}
        worker, _ = self.make_worker([], api, controller, gate=gate_config(auto_close='software', auto_close_seconds=0))
        worker.open_barrier(3)
        for _ in range(50):
            if controller.send.call_count == 2:
                break
            threading.Event().wait(0.02)
        self.assertEqual([c.args[0] for c in controller.send.call_args_list], ['open', 'close'])
        controller.send.side_effect = ControllerError('refused')
        worker._software_close()


class SendAndReportTest(unittest.TestCase):
    def test_paths(self):
        api = mock.Mock()
        self.assertFalse(send_and_report(api, None, 'G', 'open', 1))
        api.command_result.assert_called_with(1, False, 'no_controller')
        controller = mock.Mock()
        controller.send.side_effect = ControllerError('Controller refused open: HTTP 429', 429)
        self.assertFalse(send_and_report(api, controller, 'G', 'open', 2))
        api.command_result.assert_called_with(2, False, 'Controller refused open: HTTP 429')
        api.command_result.side_effect = ApiError('down')
        controller.send.side_effect = None
        controller.send.return_value = {'result': 'ok'}
        self.assertTrue(send_and_report(api, controller, 'G', 'stop', 3))

    def test_make_controller(self):
        self.assertIsNone(make_controller({'controller_url': '', 'controller_token': 't'}))
        self.assertIsNone(make_controller({'controller_url': 'http://x/', 'controller_token': None}))
        self.assertEqual(make_controller(gate_config()).base_url, 'http://lpr/api/v1/gate/sim/1/')


class FakeWorker:
    instances = []

    def __init__(self, gate, camera, config, settings, api, controller):
        self.gate, self.camera, self.controller = gate, camera, controller
        self.camera_status = 'streaming'
        self.started = self.stopped = False
        FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class AgentTest(unittest.TestCase):
    def setUp(self):
        FakeWorker.instances = []
        self.api = mock.Mock()
        self.agent = Agent(settings(), self.api, worker_factory=FakeWorker)

    def test_config_sync_starts_restarts_and_removes_workers(self):
        self.api.agent_config.return_value = config(gate_config(1), gate_config(2, camera=False))
        self.assertTrue(self.agent.sync_config())
        self.assertEqual(len(FakeWorker.instances), 1)
        first = FakeWorker.instances[0]
        self.assertTrue(first.started)
        self.assertIn(2, self.agent.controllers)

        # Unchanged gate keeps its worker
        self.api.agent_config.return_value = config(gate_config(1), version='v2')
        self.agent.sync_config()
        self.assertEqual(len(FakeWorker.instances), 1)
        self.assertNotIn(2, self.agent.gates)

        # Camera changed in the admin UI: worker restarted
        changed = gate_config(1)
        changed['cameras'][0]['config_version'] = 2
        self.api.agent_config.return_value = config(changed, version='v3')
        self.agent.sync_config()
        self.assertTrue(first.stopped)
        self.assertEqual(len(FakeWorker.instances), 2)

        self.api.agent_config.return_value = config(version='v4')
        self.agent.sync_config()
        self.assertTrue(FakeWorker.instances[1].stopped)
        self.assertEqual(self.agent.workers, {})

    def test_a_gate_runs_one_worker_per_camera(self):
        gate = gate_config(1, cameras=[camera_config(11, 'in'), camera_config(12, 'out')])
        self.api.agent_config.return_value = config(gate)
        self.agent.sync_config()
        self.assertEqual(len(FakeWorker.instances), 2)
        self.assertEqual({w.camera['direction'] for w in FakeWorker.instances}, {'in', 'out'})
        # One controller for the gate, shared by both workers
        self.assertEqual({id(w.controller) for w in FakeWorker.instances}, {id(self.agent.controllers[1])})
        self.assertEqual(set(self.agent.workers), {(1, 11), (1, 12)})

        # Removing the exit camera stops only its worker
        self.api.agent_config.return_value = config(
            gate_config(1, cameras=[camera_config(11, 'in')]), version='v2')
        self.agent.sync_config()
        self.assertEqual(set(self.agent.workers), {(1, 11)})
        stopped = [w for w in FakeWorker.instances if w.camera['id'] == 12]
        self.assertTrue(stopped[0].stopped)

    def test_unchanged_and_failed_sync(self):
        self.api.agent_config.return_value = None
        self.assertFalse(self.agent.sync_config())
        self.api.agent_config.side_effect = ApiError('down')
        self.assertFalse(self.agent.sync_config())

    def test_version_passed_on_next_sync(self):
        self.api.agent_config.return_value = config(version='abc')
        self.agent.sync_config()
        self.agent.sync_config()
        self.assertEqual(self.api.agent_config.call_args.args, ('abc',))

    def test_config_errors_logged(self):
        gate = gate_config(1, config_errors=['software close refused'], controller_token_error='token_unavailable')
        with self.assertLogs('gate_agent', 'WARNING') as logs:
            self.agent.apply_config(config(gate))
        self.assertTrue(any('software close refused' in line for line in logs.output))
        self.assertTrue(any('token unavailable' in line for line in logs.output))

    def test_manual_commands_executed_and_reported(self):
        self.agent.apply_config(config(gate_config(1)))
        controller = mock.Mock()
        controller.send.return_value = {'result': 'ok'}
        self.agent.controllers[1] = controller
        self.api.agent_commands.return_value = [{'event_id': 5, 'gate_id': 1, 'command': 'stop'},
                                                {'event_id': 6, 'gate_id': 99, 'command': 'open'}]
        self.assertEqual(self.agent.process_commands(), 2)
        controller.send.assert_called_once_with('stop')
        self.api.command_result.assert_any_call(5, True, 'ok')
        self.api.command_result.assert_any_call(6, False, 'no_controller')
        self.api.agent_commands.side_effect = ApiError('down')
        self.assertEqual(self.agent.process_commands(), 0)

    def test_status_report(self):
        self.agent.report_status()
        self.api.agent_status.assert_not_called()
        self.agent.apply_config(config(gate_config(1)))
        self.agent.report_status()
        self.api.agent_status.assert_called_once_with([{'id': 11, 'status': 'streaming'}])

    def test_status_reports_every_camera(self):
        self.agent.apply_config(config(gate_config(1, cameras=[camera_config(11, 'in'), camera_config(12, 'out')])))
        self.agent.report_status()
        reported = self.api.agent_status.call_args.args[0]
        self.assertEqual(sorted(c['id'] for c in reported), [11, 12])
        self.api.agent_status.side_effect = ApiError('down')
        self.agent.report_status()

    def test_run_forever_until_stopped(self):
        self.api.agent_config.return_value = config(gate_config(1))
        self.api.agent_commands.return_value = []
        calls = []

        def wait(delay):
            calls.append(delay)
            if len(calls) >= 2:
                self.agent.stop_event.set()
            return self.agent.stop_event.is_set()
        self.agent.stop_event.wait = wait
        self.agent.run_forever()
        self.assertTrue(FakeWorker.instances[0].stopped)
        self.api.agent_status.assert_called()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.photo = os.path.join(self.dir, 'car.jpg')
        frame(car=True).save(self.photo)
        self.env = mock.patch.dict(os.environ, {'GATE_AGENT_TOKEN': 't', 'LPR_API_URL': 'http://lpr'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, argv, api):
        out = io.StringIO()
        with mock.patch('gate_agent.__main__.LprApi', return_value=api), redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_decide_opens_simulator(self):
        api = mock.Mock()
        api.agent_config.return_value = config(gate_config(1))
        api.decide.return_value = {'decision': 'granted', 'reason': 'whitelist_hit', 'actuate': True, 'event_id': 4}
        with mock.patch('gate_agent.__main__.make_controller') as mc:
            mc.return_value.send.return_value = {'result': 'ok'}
            code, out = self.run_cli(['decide', '--gate', '1', self.photo, self.photo], api)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['command_sent'])
        self.assertEqual(len(api.decide.call_args.args[1]), 2)

    def test_decide_does_not_open_real_controller_without_flag(self):
        api = mock.Mock()
        api.agent_config.return_value = config(gate_config(1, controller_type='esp32'))
        api.decide.return_value = {'decision': 'granted', 'reason': 'whitelist_hit', 'actuate': True, 'event_id': 4}
        with mock.patch('gate_agent.__main__.make_controller') as mc:
            code, out = self.run_cli(['decide', '--gate', '1', '--roi', self.photo], api)
        mc.return_value.send.assert_not_called()
        self.assertFalse(json.loads(out)['command_sent'])
        api.command_result.assert_called_once_with(4, False, 'not_sent_cli_real_controller')

    def test_decide_denied_and_failure_exit_codes(self):
        api = mock.Mock()
        api.agent_config.return_value = config(gate_config(1))
        api.decide.return_value = {'decision': 'denied', 'reason': 'no_plate', 'actuate': False, 'event_id': 4}
        self.assertEqual(self.run_cli(['decide', '--gate', '1', self.photo], api)[0], 1)
        api.decide.side_effect = ApiError('HTTP 403')
        self.assertEqual(self.run_cli(['decide', '--gate', '1', self.photo], api)[0], 2)

    def test_unknown_gate_and_missing_token(self):
        api = mock.Mock()
        api.agent_config.return_value = config(gate_config(1))
        with self.assertRaises(SystemExit):
            self.run_cli(['decide', '--gate', '5', self.photo], api)
        with mock.patch.dict(os.environ, {'GATE_AGENT_TOKEN': ''}):
            with self.assertRaises(SystemExit):
                self.run_cli(['decide', '--gate', '1', self.photo], api)

    def test_replay_directory(self):
        frames_dir = os.path.join(self.dir, 'frames')
        os.mkdir(frames_dir)
        sequence = [frame()] * 4 + [frame(car=True)] * 8
        for i, image in enumerate(sequence):
            image.save(os.path.join(frames_dir, f'{i:03d}.jpg'))
        api = mock.Mock()
        api.agent_config.return_value = config(gate_config(1))
        api.decide.return_value = {'decision': 'granted', 'reason': 'whitelist_hit', 'actuate': True,
                                   'event_id': 4, 'plate': '30A12345', 'confidence': 0.9}
        with mock.patch('gate_agent.__main__.make_controller') as mc:
            mc.return_value.send.return_value = {'result': 'ok'}
            code, out = self.run_cli(['replay', '--gate', '1', '--fps', '2.5', frames_dir], api)
        result = json.loads(out)
        self.assertEqual(result['triggers'], 1)
        self.assertEqual(result['decisions'][0]['plate'], '30A12345')
        mc.return_value.send.assert_called_once_with('open')

    def test_config_load_failures_exit_cleanly(self):
        api = mock.Mock()
        api.agent_config.side_effect = ApiError('HTTP 403: Permission denied', 403)
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(['decide', '--gate', '1', self.photo], api)
        self.assertIn('HTTP 403', str(ctx.exception.code))
        api.agent_config.side_effect = ConnectionError('refused')
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(['replay', '--gate', '1', self.dir], api)
        self.assertIn('Cannot reach', str(ctx.exception.code))

    def test_replay_requires_camera(self):
        api = mock.Mock()
        api.agent_config.return_value = config(gate_config(1, camera=False))
        with self.assertRaises(SystemExit):
            self.run_cli(['replay', '--gate', '1', self.dir], api)

    def test_run_command_wires_agent(self):
        api = mock.Mock()
        with mock.patch('gate_agent.__main__.metrics.serve') as serve, \
                mock.patch('gate_agent.__main__.Agent') as agent_cls, \
                mock.patch('gate_agent.__main__.signal.signal'):
            code, _ = self.run_cli(['run'], api)
        self.assertEqual(code, 0)
        serve.assert_called_once_with(9101)
        agent_cls.return_value.run_forever.assert_called_once()


class ApiClientTest(unittest.TestCase):
    def setUp(self):
        from gate_agent.api import LprApi
        self.session = mock.Mock()
        self.session.headers = {}
        self.api = LprApi('http://lpr/', 'tok', session=self.session)

    def response(self, status=200, body=None, text=''):
        r = mock.Mock(status_code=status, text=text)
        r.json.return_value = body if body is not None else {}
        if body is None and status >= 400:
            r.json.side_effect = ValueError()
        return r

    def test_token_and_endpoints(self):
        self.assertEqual(self.session.headers['Authorization'], 'Bearer tok')
        self.session.get.return_value = self.response(304)
        self.assertIsNone(self.api.agent_config('v1'))
        self.assertEqual(self.session.get.call_args.kwargs['params'], {'version': 'v1'})
        self.session.get.return_value = self.response(200, {'version': 'v2'})
        self.assertEqual(self.api.agent_config()['version'], 'v2')
        self.session.post.return_value = self.response(200, {'decision': 'denied'})
        self.assertEqual(self.api.decide(1, [b'a', b'b'], timeout=5)['decision'], 'denied')
        files = self.session.post.call_args.kwargs['files']
        self.assertEqual([f[0] for f in files], ['frames', 'frames'])
        self.api.command_result(3, True, 'ok' * 100)
        self.assertEqual(len(self.session.post.call_args.kwargs['json']['result']), 100)
        self.session.get.return_value = self.response(200, {'commands': [{'event_id': 1}]})
        self.assertEqual(self.api.agent_commands(), [{'event_id': 1}])
        self.api.agent_status([{'id': 1, 'status': 'streaming'}])

    def test_errors(self):
        self.session.get.return_value = self.response(403, {'error': 'Permission denied'})
        with self.assertRaises(ApiError) as ctx:
            self.api.agent_commands()
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn('Permission denied', str(ctx.exception))
        self.session.post.return_value = self.response(502, None, 'Bad Gateway')
        with self.assertRaises(ApiError) as ctx:
            self.api.agent_status([])
        self.assertIn('Bad Gateway', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
