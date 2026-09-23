import json
import shutil
import tempfile
import time
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from lpr_app.models import AccessEvent, GateDevice, SimulatedBarrier, Vehicle
from lpr_app.services import barrier_simulator as sim_service
from lpr_app.services import config_audit, gate_service
from lpr_app.services.barrier_simulator import CommandRejected

User = get_user_model()
AGENT = {'HTTP_AUTHORIZATION': 'Bearer agent-secret'}
SETTINGS = dict(
    GATE_AGENT_TOKEN='agent-secret',
    GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    GATE_MODE='shadow',
    GATE_BURST_FRAMES=3,
    GATE_CONSENSUS_MIN=2,
    GATE_MIN_CONFIDENCE=0.8,
)


def make_sim_gate(name='Sim gate', travel=2.0, auto_close=10):
    gate = GateDevice(name=name, controller_type='simulator')
    config_audit.save_gate_device(gate, None)
    SimulatedBarrier.objects.filter(gate=gate).update(travel_seconds=travel, auto_close_seconds=auto_close)
    return gate


@override_settings(**SETTINGS)
class SimulatorStateMachineTest(TestCase):
    def setUp(self):
        self.gate = make_sim_gate()
        self.t0 = timezone.now()

    def run_cmd(self, command, at, nonce=None):
        nonce = nonce or int(at.timestamp() * 1000)
        return sim_service.execute(self.gate, command, nonce, at.timestamp(), now=at)

    def state_at(self, at):
        return sim_service.refresh(self.gate, at)

    def test_open_travel_auto_close(self):
        body = self.run_cmd('open', self.t0)
        self.assertEqual((body['result'], body['arm_state']), ('ok', 'moving'))
        mid = self.state_at(self.t0 + timedelta(seconds=1))
        self.assertEqual(mid['arm_state'], 'moving')
        self.assertAlmostEqual(mid['position'], 0.5, places=2)
        self.assertEqual(self.state_at(self.t0 + timedelta(seconds=2.5))['arm_state'], 'up')
        # up at t0+2, auto-close at t0+12, down at t0+14
        self.assertEqual(self.state_at(self.t0 + timedelta(seconds=13))['arm_state'], 'moving')
        final = self.state_at(self.t0 + timedelta(seconds=15))
        self.assertEqual(final['arm_state'], 'down')
        events = [h['event'] for h in final['history']]
        self.assertEqual(events, ['open', 'reached_up', 'auto_close', 'reached_down'])
        self.gate.refresh_from_db()
        self.assertEqual(self.gate.arm_state, 'down')

    def test_auto_close_disabled(self):
        SimulatedBarrier.objects.filter(gate=self.gate).update(auto_close_seconds=0)
        self.run_cmd('open', self.t0)
        self.assertEqual(self.state_at(self.t0 + timedelta(minutes=10))['arm_state'], 'up')

    def test_stop_midway_then_close(self):
        self.run_cmd('open', self.t0)
        stopped = self.run_cmd('stop', self.t0 + timedelta(seconds=1))
        self.assertEqual(stopped['arm_state'], 'stopped')
        self.assertAlmostEqual(stopped['position'], 0.5, places=2)
        self.assertEqual(self.state_at(self.t0 + timedelta(minutes=5))['arm_state'], 'stopped')
        closing = self.run_cmd('close', self.t0 + timedelta(seconds=5))
        self.assertEqual(closing['arm_state'], 'moving')
        # Half way up closes in half the travel time
        self.assertEqual(self.state_at(self.t0 + timedelta(seconds=6.1))['arm_state'], 'down')

    def test_idempotent_results(self):
        self.assertEqual(self.run_cmd('close', self.t0)['result'], 'already_down')
        self.assertEqual(self.run_cmd('stop', self.t0 + timedelta(seconds=4))['result'], 'not_moving')
        self.run_cmd('open', self.t0 + timedelta(seconds=8))
        self.assertEqual(self.run_cmd('open', self.t0 + timedelta(seconds=12))['result'], 'already_up')

    def test_replayed_nonce(self):
        self.run_cmd('open', self.t0, nonce=100)
        with self.assertRaises(CommandRejected) as ctx:
            self.run_cmd('stop', self.t0 + timedelta(seconds=1), nonce=100)
        self.assertEqual(ctx.exception.status, 409)

    def test_stale_timestamp(self):
        with self.assertRaises(CommandRejected) as ctx:
            sim_service.execute(self.gate, 'open', 1, (self.t0 - timedelta(seconds=60)).timestamp(), now=self.t0)
        self.assertEqual(ctx.exception.status, 401)

    def test_interlock_and_rate_limit(self):
        self.run_cmd('open', self.t0)
        with self.assertRaises(CommandRejected) as ctx:
            self.run_cmd('close', self.t0 + timedelta(seconds=0.5))
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(CommandRejected) as ctx:
            self.run_cmd('close', self.t0 + timedelta(seconds=2))
        self.assertEqual(ctx.exception.status, 429)
        # STOP is never rate limited
        self.assertEqual(self.run_cmd('stop', self.t0 + timedelta(seconds=0.6))['command'], 'stop')

    def test_bad_input(self):
        with self.assertRaises(CommandRejected) as ctx:
            sim_service.execute(self.gate, 'explode', 1, time.time())
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(CommandRejected) as ctx:
            sim_service.execute(self.gate, 'open', 'x', None)
        self.assertEqual(ctx.exception.status, 400)

    def test_rejected_command_leaves_state_unchanged(self):
        self.run_cmd('open', self.t0, nonce=5)
        with self.assertRaises(CommandRejected):
            self.run_cmd('open', self.t0 + timedelta(seconds=1), nonce=5)
        sim = SimulatedBarrier.objects.get(gate=self.gate)
        self.assertEqual(sim.last_nonce, 5)
        self.assertEqual(sim.phase, 'moving_up')

    def test_history_is_bounded(self):
        for i in range(60):
            at = self.t0 + timedelta(seconds=i * 4)
            self.run_cmd('open' if i % 2 == 0 else 'close', at)
        self.assertEqual(len(SimulatedBarrier.objects.get(gate=self.gate).history), sim_service.HISTORY_LIMIT)

    def test_next_nonce_increases(self):
        sim = sim_service.get_simulator(self.gate)
        sim.last_nonce = 10 ** 15
        self.assertEqual(sim_service.next_nonce(sim), 10 ** 15 + 1)


@override_settings(**SETTINGS)
class SimulatorGateSetupTest(TestCase):
    def test_simulated_gate_gets_token_and_state_and_is_online(self):
        gate = make_sim_gate()
        self.assertTrue(gate.controller_token_set)
        self.assertTrue(SimulatedBarrier.objects.filter(gate=gate).exists())
        self.assertTrue(gate.is_online())
        self.assertTrue(gate.is_simulated)

    def test_explicit_token_kept(self):
        gate = GateDevice(name='G', controller_type='simulator')
        config_audit.save_gate_device(gate, None, token='mine')
        self.assertEqual(gate.get_controller_token(), 'mine')

    def test_can_actuate(self):
        real = GateDevice.objects.create(name='Real')
        sim = make_sim_gate()
        self.assertFalse(gate_service.can_actuate(real, 'shadow'))
        self.assertTrue(gate_service.can_actuate(real, 'live'))
        self.assertTrue(gate_service.can_actuate(sim, 'shadow'))
        self.assertFalse(gate_service.can_actuate(None, 'live'))


@override_settings(**SETTINGS)
class SimulatorDeviceEndpointTest(TestCase):
    """The ESP32 contract served by Django, as the agent will call it."""

    def setUp(self):
        self.gate = make_sim_gate()
        self.token = self.gate.get_controller_token()
        self.client = Client(enforce_csrf_checks=True)

    def _post(self, command, nonce=None, ts=None, token=None):
        return self.client.post(
            f'/api/v1/gate/sim/{self.gate.id}/{command}',
            data=json.dumps({'nonce': nonce or int(time.time() * 1000), 'ts': ts or time.time()}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token or self.token}',
        )

    def test_open_and_status(self):
        response = self._post('open')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['arm_state'], 'moving')
        status = self.client.get(f'/api/v1/gate/sim/{self.gate.id}/status', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(status.json()['firmware_version'], 'simulator-1')

    def test_auth(self):
        self.assertEqual(self._post('open', token='wrong').status_code, 401)
        real = GateDevice(name='Real')
        real.set_controller_token('t')
        real.save()
        response = self.client.post(f'/api/v1/gate/sim/{real.id}/open', data='{}', content_type='application/json',
                                    HTTP_AUTHORIZATION='Bearer t')
        self.assertEqual(response.status_code, 401)

    def test_errors_map_to_firmware_status_codes(self):
        self.assertEqual(self._post('open', nonce=5).status_code, 200)
        self.assertEqual(self._post('stop', nonce=5).status_code, 409)
        self.assertEqual(self._post('open', ts=time.time() - 120).status_code, 401)
        self.assertEqual(self._post('explode').status_code, 404)
        get_open = self.client.get(f'/api/v1/gate/sim/{self.gate.id}/open', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(get_open.status_code, 405)
        post_status = self._post('status')
        self.assertEqual(post_status.status_code, 405)
        bad = self.client.post(f'/api/v1/gate/sim/{self.gate.id}/open', data='nope', content_type='application/json',
                               HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(bad.status_code, 400)

    def test_agent_config_points_at_simulator(self):
        data = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()
        gate = data['gates'][0]
        self.assertEqual(gate['controller_type'], 'simulator')
        self.assertEqual(gate['controller_url'], f'http://testserver/api/v1/gate/sim/{self.gate.id}/')
        self.assertEqual(gate['controller_token'], self.token)


def _user(name, group):
    user = User.objects.create_user(name, password='pw-123456')
    user.groups.add(Group.objects.get(name=group))
    return user


@override_settings(**SETTINGS)
class DispatchRuleTest(TestCase):
    def test_shadow_override_on_simulator_is_queued_and_real_is_not(self):
        sim = make_sim_gate()
        real = GateDevice.objects.create(name='Real')
        op = _user('op', 'gate_operator')
        self.assertEqual(gate_service.create_override(sim, 'open', op).command_result, '')
        self.assertEqual(gate_service.create_override(real, 'open', op).command_result, 'not_sent_shadow_mode')
        claimed = gate_service.claim_pending_commands()
        self.assertEqual([e.gate for e in claimed], [sim])

    def test_run_on_simulator(self):
        sim = make_sim_gate()
        event = gate_service.create_override(sim, 'open', None, is_test=True)
        body = gate_service.run_on_simulator(event)
        self.assertEqual(body['result'], 'ok')
        event.refresh_from_db()
        self.assertEqual((event.command_sent, event.command_result), (True, 'ok'))
        # Already claimed: a second run does nothing
        self.assertIsNone(gate_service.run_on_simulator(event))

    def test_run_on_simulator_rejection_recorded(self):
        sim = make_sim_gate()
        gate_service.run_on_simulator(gate_service.create_override(sim, 'open', None, is_test=True))
        second = gate_service.create_override(sim, 'close', None, is_test=True)
        self.assertIsNone(gate_service.run_on_simulator(second))
        second.refresh_from_db()
        self.assertFalse(second.command_sent)
        self.assertIn('rejected', second.command_result)

    def test_run_on_simulator_requires_simulated_gate(self):
        real = GateDevice.objects.create(name='Real')
        event = AccessEvent.objects.create(gate=real, decision='manual', reason='manual_override', command='open')
        with self.assertRaises(ValueError):
            gate_service.run_on_simulator(event)


def fake_reads(plates):
    """Patch frame reading so each frame yields the given plate text."""
    from lpr_app.services.gate_service import FrameRead
    from lpr_app.utils.plates import normalize_plate

    def read_frames(image_ids, deadline):
        reads = [FrameRead(image_id=i, ok=True, plate_raw=p, plate=normalize_plate(p), confidence=0.95)
                 for i, p in zip(image_ids, plates)]
        return reads, 0
    return mock.patch('lpr_app.services.gate_service.read_frames', side_effect=read_frames)


def jpeg(name):
    return SimpleUploadedFile(name, b'\xff\xd8' + b'\x00' * 256, content_type='image/jpeg')


@override_settings(**SETTINGS)
class TestDecideApiTest(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.admin = _user('adm', 'gate_admin')
        self.client.force_login(self.admin)
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')
        self.sim = make_sim_gate()

    def _post(self, gate, plates):
        with fake_reads(plates):
            return self.client.post(f'/api/v1/gate/devices/{gate.id}/test-decide/',
                                    {'frames': [jpeg(f'f{i}.jpg') for i in range(len(plates))]})

    def test_grant_opens_simulator_in_shadow_mode(self):
        data = self._post(self.sim, ['30A12345', '30A-123.45']).json()
        self.assertEqual(data['event']['decision'], 'granted')
        self.assertTrue(data['event']['is_test'])
        self.assertEqual(data['event']['command_result'], 'ok')
        self.assertEqual(data['simulator']['arm_state'], 'moving')

    def test_denied_does_not_move_barrier(self):
        data = self._post(self.sim, ['51F99999', '51F99999']).json()
        self.assertEqual(data['event']['reason'], 'not_registered')
        self.assertEqual(data['simulator']['arm_state'], 'down')

    @override_settings(GATE_MODE='live')
    def test_real_controller_never_actuated_from_test(self):
        real = GateDevice.objects.create(name='Real')
        data = self._post(real, ['30A12345', '30A12345']).json()
        self.assertEqual(data['event']['decision'], 'granted')
        self.assertEqual(data['event']['command_result'], 'not_sent_test')
        self.assertIsNone(data['simulator'])
        self.assertIn('real controller was not actuated', data['note'])
        self.assertEqual(gate_service.claim_pending_commands(), [])

    def test_test_events_excluded_from_metrics_and_filterable(self):
        from lpr_app import metrics
        labels = dict(gate='Sim gate', decision='granted', reason='whitelist_hit')
        before = metrics.REGISTRY.get_sample_value('lpr_gate_decisions_total', labels) or 0
        self._post(self.sim, ['30A12345', '30A12345'])
        self.assertEqual(metrics.REGISTRY.get_sample_value('lpr_gate_decisions_total', labels) or 0, before)
        self.assertEqual(self.client.get('/api/v1/access-events/?is_test=true').json()['count'], 1)
        self.assertEqual(self.client.get('/api/v1/access-events/?is_test=false').json()['count'], 0)

    def test_validation_and_permissions(self):
        self.assertEqual(self.client.post(f'/api/v1/gate/devices/{self.sim.id}/test-decide/', {}).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/gate/devices/999/test-decide/', {}).status_code, 404)
        self.client.force_login(_user('op', 'gate_operator'))
        self.assertEqual(self.client.post(f'/api/v1/gate/devices/{self.sim.id}/test-decide/', {}).status_code, 403)

    def test_simulator_endpoint(self):
        state = self.client.get(f'/api/v1/gate/devices/{self.sim.id}/simulator/').json()
        self.assertEqual(state['arm_state'], 'down')
        data = self.client.post(f'/api/v1/gate/devices/{self.sim.id}/simulator/', data=json.dumps({'command': 'open'}),
                                content_type='application/json').json()
        self.assertEqual(data['simulator']['arm_state'], 'moving')
        self.assertEqual(data['event']['decision'], 'manual')
        bad = self.client.post(f'/api/v1/gate/devices/{self.sim.id}/simulator/', data=json.dumps({'command': 'x'}),
                               content_type='application/json')
        self.assertEqual(bad.status_code, 400)
        bad_json = self.client.post(f'/api/v1/gate/devices/{self.sim.id}/simulator/', data='{', content_type='application/json')
        self.assertEqual(bad_json.status_code, 400)
        real = GateDevice.objects.create(name='Real')
        self.assertEqual(self.client.get(f'/api/v1/gate/devices/{real.id}/simulator/').status_code, 404)

    def test_simulator_endpoint_roles(self):
        self.client.force_login(_user('op', 'gate_operator'))
        self.assertEqual(self.client.get(f'/api/v1/gate/devices/{self.sim.id}/simulator/').status_code, 200)
        post = self.client.post(f'/api/v1/gate/devices/{self.sim.id}/simulator/', data=json.dumps({'command': 'open'}),
                                content_type='application/json')
        self.assertEqual(post.status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(f'/api/v1/gate/devices/{self.sim.id}/simulator/').status_code, 403)

    def test_status_includes_simulator_and_heartbeat_arm_state(self):
        data = self.client.get('/api/v1/gate/status/').json()
        gate = data['gates'][0]
        self.assertEqual(gate['controller_type'], 'simulator')
        self.assertEqual(gate['simulator']['arm_state'], 'down')
        self.assertTrue(gate['online'])

        real = GateDevice(name='Real')
        real.set_controller_token('dev')
        real.save()
        self.client.post('/api/v1/gate/heartbeat/', data=json.dumps({'gate_id': real.id, 'arm_state': 'up'}),
                         content_type='application/json', HTTP_AUTHORIZATION='Bearer dev')
        real.refresh_from_db()
        self.assertEqual(real.arm_state, 'up')
        self.client.post('/api/v1/gate/heartbeat/', data=json.dumps({'gate_id': real.id, 'arm_state': 'flying'}),
                         content_type='application/json', HTTP_AUTHORIZATION='Bearer dev')
        real.refresh_from_db()
        self.assertEqual(real.arm_state, 'up')

    def test_arm_gauge(self):
        from lpr_app import metrics
        gate_service.run_on_simulator(gate_service.create_override(self.sim, 'open', None, is_test=True))
        metrics.update_gate_metrics(timezone.now() + timedelta(seconds=5))
        self.assertEqual(metrics.REGISTRY.get_sample_value('lpr_gate_arm_up', {'gate': 'Sim gate'}), 1)


@override_settings(**SETTINGS)
class AdminTestPageTest(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.root = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.force_login(self.root)
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')
        self.sim = make_sim_gate()
        self.url = f'/admin/lpr_app/gatedevice/{self.sim.id}/test/'

    def test_page_renders_with_barrier(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Simulated barrier')
        self.assertContains(page, 'Run recognition')
        self.assertContains(page, f'/api/v1/gate/devices/{self.sim.id}/simulator/')

    def test_upload_runs_decision_and_opens(self):
        with fake_reads(['30A12345', '30A12345']):
            page = self.client.post(self.url, {'frames': [jpeg('a.jpg'), jpeg('b.jpg')]})
        self.assertContains(page, 'granted')
        self.assertContains(page, 'Nguyen Van A')
        self.assertEqual(AccessEvent.objects.get(is_test=True).command_result, 'ok')

    def test_upload_errors_shown(self):
        page = self.client.post(self.url, {}, follow=True)
        self.assertContains(page, 'MISSING_FRAMES')

    def test_buttons_drive_simulator(self):
        response = self.client.post(self.url, {'command': 'open'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SimulatedBarrier.objects.get(gate=self.sim).phase, 'moving_up')

    def test_buttons_rejected_for_real_gate(self):
        real = GateDevice.objects.create(name='Real')
        page = self.client.post(f'/admin/lpr_app/gatedevice/{real.id}/test/', {'command': 'open'}, follow=True)
        self.assertContains(page, 'only be sent to a simulated barrier')
        self.assertFalse(AccessEvent.objects.exists())
        self.assertContains(self.client.get(f'/admin/lpr_app/gatedevice/{real.id}/test/'), 'uses a real controller')

    @override_settings(GATE_MODE='live')
    def test_real_gate_test_warns(self):
        real = GateDevice.objects.create(name='Real')
        with fake_reads(['30A12345', '30A12345']):
            page = self.client.post(f'/admin/lpr_app/gatedevice/{real.id}/test/',
                                    {'frames': [jpeg('a.jpg'), jpeg('b.jpg')]})
        self.assertContains(page, 'real controller was not actuated')

    def test_operator_forbidden(self):
        op = _user('op', 'gate_operator')
        op.is_staff = True
        op.save()
        self.client.force_login(op)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_change_list_link_and_inline_settings(self):
        page = self.client.get('/admin/lpr_app/gatedevice/')
        self.assertContains(page, 'Test recognition')
        change = f'/admin/lpr_app/gatedevice/{self.sim.id}/change/'
        sim = SimulatedBarrier.objects.get(gate=self.sim)
        response = self.client.post(change, {
            'name': 'Sim gate', 'location': '',
            'controller_type': 'simulator', 'controller_url': '', 'controller_token': '', 'exit_policy': 'registered',
            'is_enabled': 'on',
            'gate_cameras-TOTAL_FORMS': '0', 'gate_cameras-INITIAL_FORMS': '0',
            'gate_cameras-MIN_NUM_FORMS': '0', 'gate_cameras-MAX_NUM_FORMS': '1000',
            'simulator-TOTAL_FORMS': '1', 'simulator-INITIAL_FORMS': '1',
            'simulator-MIN_NUM_FORMS': '0', 'simulator-MAX_NUM_FORMS': '1',
            'simulator-0-id': sim.id, 'simulator-0-gate': self.sim.id,
            'simulator-0-travel_seconds': '5', 'simulator-0-auto_close_seconds': '0',
        })
        self.assertEqual(response.status_code, 302, response.content[:2000])
        sim.refresh_from_db()
        self.assertEqual((sim.travel_seconds, sim.auto_close_seconds), (5.0, 0))

    def test_new_simulated_gate_via_admin_with_inline(self):
        response = self.client.post('/admin/lpr_app/gatedevice/add/', {
            'name': 'New sim', 'location': '',
            'controller_type': 'simulator', 'controller_url': '', 'controller_token': '', 'exit_policy': 'registered',
            'is_enabled': 'on',
            'gate_cameras-TOTAL_FORMS': '0', 'gate_cameras-INITIAL_FORMS': '0',
            'gate_cameras-MIN_NUM_FORMS': '0', 'gate_cameras-MAX_NUM_FORMS': '1000',
            'simulator-TOTAL_FORMS': '1', 'simulator-INITIAL_FORMS': '0',
            'simulator-MIN_NUM_FORMS': '0', 'simulator-MAX_NUM_FORMS': '1',
            'simulator-0-travel_seconds': '4', 'simulator-0-auto_close_seconds': '20',
        })
        self.assertEqual(response.status_code, 302, response.content[:2000])
        gate = GateDevice.objects.get(name='New sim')
        self.assertEqual(SimulatedBarrier.objects.filter(gate=gate).count(), 1)
        self.assertEqual(gate.simulator.travel_seconds, 4.0)
        self.assertTrue(gate.controller_token_set)
