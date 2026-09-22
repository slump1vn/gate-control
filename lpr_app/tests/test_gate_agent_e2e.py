"""
End to end: the real gate agent code talks over HTTP to a live server running
this app, and the simulated barrier moves. Only the recognition model is
mocked (every frame "reads" the configured plate).
"""

import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from unittest import mock

from cryptography.fernet import Fernet
from django.test import LiveServerTestCase, override_settings
from PIL import Image, ImageDraw

from lpr_app.models import AccessEvent, Camera, GateDevice, SimulatedBarrier, Vehicle
from lpr_app.services import config_audit, gate_service

AGENT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'gate-agent')
if AGENT_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(AGENT_DIR))

from gate_agent import __main__ as agent_cli  # noqa: E402
from gate_agent.agent import Agent  # noqa: E402
from gate_agent.api import LprApi  # noqa: E402
from gate_agent.config import AgentSettings  # noqa: E402

TOKEN = 'e2e-agent-token'


def fake_recognition(plate_text):
    """Stand-in for the VLM pipeline: every frame reads `plate_text`."""
    def process(image, save_image=True):
        image.api_response = {'detections': [{
            'plate': {'coordinates': {'x1': 10, 'y1': 10, 'x2': 110, 'y2': 40}},
            'ocr': [{'text': plate_text, 'confidence': 0.93}],
        }]}
        image.processing_status = 'completed'
        image.save()
        return {'success': True}
    return mock.patch(
        'lpr_app.services.gate_service.ImageProcessingService.process_uploaded_image', side_effect=process,
    )


def lane(car=False):
    image = Image.new('RGB', (320, 180), (150, 150, 150))
    if car:
        ImageDraw.Draw(image).rectangle([60, 40, 260, 170], fill=(40, 40, 40))
    return image


@override_settings(
    GATE_AGENT_TOKEN=TOKEN,
    GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    GATE_MODE='shadow',
    GATE_BURST_FRAMES=3,
    GATE_CONSENSUS_MIN=2,
    GATE_MIN_CONFIDENCE=0.8,
    GATE_CAMERA_ALLOWED_CIDRS=['192.168.0.0/16'],
)
class AgentAgainstLiveServerTest(LiveServerTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)
        # Frames are processed in the service's worker pool; run them inline
        executor = mock.patch.object(gate_service, '_executor', _InlineExecutor())
        executor.start()
        self.addCleanup(executor.stop)

        Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')
        camera = Camera.objects.create(name='Gate cam', host='192.168.1.64', snapshot_path='/snap.jpg')
        self.gate = GateDevice(name='Main gate', controller_type='simulator', camera=camera)
        config_audit.save_gate_device(self.gate, None)
        SimulatedBarrier.objects.filter(gate=self.gate).update(travel_seconds=1.0, auto_close_seconds=0)

        env = mock.patch.dict(os.environ, {'GATE_AGENT_TOKEN': TOKEN, 'LPR_API_URL': self.live_server_url})
        env.start()
        self.addCleanup(env.stop)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = agent_cli.main(list(argv))
        return code, json.loads(out.getvalue())

    def photo(self, name='car.jpg'):
        path = os.path.join(self.dir, name)
        lane(car=True).save(path)
        return path

    def test_decide_cli_grants_and_opens_simulated_barrier(self):
        with fake_recognition('30A-123.45'):
            code, result = self.cli('decide', '--gate', str(self.gate.id), self.photo('a.jpg'), self.photo('b.jpg'))
        self.assertEqual(code, 0)
        self.assertEqual((result['decision'], result['reason']), ('granted', 'whitelist_hit'))
        self.assertTrue(result['actuate'])
        self.assertTrue(result['command_sent'])

        event = AccessEvent.objects.get(pk=result['event_id'])
        self.assertEqual((event.command_sent, event.command_result), (True, 'ok'))
        sim = SimulatedBarrier.objects.get(gate=self.gate)
        self.assertEqual(sim.phase, 'moving_up')
        self.assertEqual([h['event'] for h in sim.history], ['open'])

    def test_unregistered_plate_leaves_barrier_down(self):
        with fake_recognition('51F-999.99'):
            code, result = self.cli('decide', '--gate', str(self.gate.id), self.photo('a.jpg'), self.photo('b.jpg'))
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'not_registered')
        self.assertEqual(SimulatedBarrier.objects.get(gate=self.gate).phase, 'down')

    def test_replay_triggers_on_arrival_and_opens(self):
        frames = os.path.join(self.dir, 'frames')
        os.mkdir(frames)
        for i, image in enumerate([lane()] * 4 + [lane(car=True)] * 8):
            image.save(os.path.join(frames, f'{i:03d}.jpg'))
        with fake_recognition('30A12345'):
            code, result = self.cli('replay', '--gate', str(self.gate.id), frames)
        self.assertEqual(result['triggers'], 1)
        self.assertEqual(result['decisions'][0]['decision'], 'granted')
        self.assertEqual(SimulatedBarrier.objects.get(gate=self.gate).phase, 'moving_up')

    def test_agent_relays_manual_commands_and_reports_status(self):
        settings = AgentSettings(api_url=self.live_server_url, agent_token=TOKEN)
        agent = Agent(settings, LprApi(self.live_server_url, TOKEN), worker_factory=_NoCameraWorker)
        self.assertTrue(agent.sync_config())
        self.assertFalse(agent.sync_config())  # unchanged -> 304

        event = gate_service.create_override(self.gate, 'open', None)
        self.assertEqual(agent.process_commands(), 1)
        event.refresh_from_db()
        self.assertEqual((event.command_sent, event.command_result), (True, 'ok'))
        self.assertEqual(SimulatedBarrier.objects.get(gate=self.gate).phase, 'moving_up')

        # The simulator enforces the firmware's UP/DOWN interlock on real HTTP calls too
        close = gate_service.create_override(self.gate, 'close', None)
        agent.controllers[self.gate.id].min_interval = 0  # bypass the agent's own limit to reach the device's
        agent.process_commands()
        close.refresh_from_db()
        self.assertFalse(close.command_sent)
        self.assertIn('409 UP/DOWN interlock', close.command_result)

        agent.report_status()
        self.assertEqual(Camera.objects.get().agent_status, 'streaming')

    def test_wrong_agent_token_is_refused(self):
        with mock.patch.dict(os.environ, {'GATE_AGENT_TOKEN': 'wrong'}):
            with self.assertRaises(SystemExit) as ctx:
                self.cli('decide', '--gate', str(self.gate.id), self.photo())
        self.assertIn('HTTP 403', str(ctx.exception.code))


class _InlineExecutor:
    def submit(self, fn, *args):
        from concurrent.futures import Future
        future = Future()
        future.set_result(fn(*args))
        return future


class _NoCameraWorker:
    """Stands in for a camera worker so the test only exercises commands and status."""

    def __init__(self, gate, config, settings, api, controller):
        self.camera = gate['camera']
        self.camera_status = 'streaming'

    def start(self):
        pass

    def stop(self):
        pass
