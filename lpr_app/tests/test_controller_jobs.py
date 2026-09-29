"""Remote capture from the admin UI: jobs, the agent's part, heartbeats and the simulator."""

import json
import time
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from lpr_app.models import ControllerJob, GateConfigChange, GateDevice
from lpr_app.services import barrier_simulator, controller_jobs

from .test_gate_signing import signed
from .test_gate_simulator import make_sim_gate

User = get_user_model()
AGENT = {'HTTP_AUTHORIZATION': 'Bearer agent-secret'}
SETTINGS = dict(GATE_AGENT_TOKEN='agent-secret', GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                GATE_MODE='shadow')


def jobs_url(gate):
    return f'/api/v1/gate/devices/{gate.id}/controller-jobs/'


@override_settings(**SETTINGS)
class ControllerJobApiTest(TestCase):
    def setUp(self):
        self.gate = make_sim_gate()
        self.admin = User.objects.create_user('installer', password='pw')
        self.admin.groups.add(Group.objects.get(name='gate_admin'))
        self.operator = User.objects.create_user('guard', password='pw')
        self.operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(self.admin)

    def post(self, gate=None, **body):
        return self.client.post(jobs_url(gate or self.gate), data=json.dumps(body), content_type='application/json')

    def test_admin_queues_a_capture(self):
        response = self.post(kind='capture', button='up', seconds=8)
        self.assertEqual(response.status_code, 201, response.content)
        job = response.json()
        self.assertEqual((job['kind'], job['button'], job['seconds'], job['state']), ('capture', 'up', 8, 'queued'))
        self.assertEqual(job['created_by'], 'installer')
        listing = self.client.get(jobs_url(self.gate)).json()
        self.assertTrue(listing['supported'])
        self.assertEqual(listing['jobs'][0]['id'], job['id'])

    def test_operators_and_anonymous_are_refused(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.post(kind='capture', button='up').status_code, 403)
        self.assertEqual(self.client.get(jobs_url(self.gate)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(kind='capture', button='up').status_code, 403)

    def test_bad_requests(self):
        for body in ({'kind': 'capture', 'button': 'left'}, {'kind': 'erase', 'button': 'up'},
                     {'kind': 'capture', 'button': 'up', 'seconds': 60},
                     {'kind': 'capture', 'button': 'up', 'seconds': 'x'}):
            self.assertEqual(self.post(**body).status_code, 400, body)
        relay = GateDevice.objects.create(name='Relay gate', controller_type='esp32')
        response = self.post(gate=relay, kind='capture', button='up')
        self.assertEqual((response.status_code, response.json()['error_code']), (400, 'NOT_SUPPORTED'))
        self.assertFalse(self.client.get(jobs_url(relay)).json()['supported'])
        self.assertEqual(self.client.get('/api/v1/gate/devices/999/controller-jobs/').status_code, 404)

    def test_one_job_at_a_time_per_controller(self):
        self.assertEqual(self.post(kind='capture', button='up').status_code, 201)
        response = self.post(kind='capture', button='down')
        self.assertEqual((response.status_code, response.json()['error_code']), (409, 'BUSY'))


@override_settings(**SETTINGS)
class AgentJobQueueTest(TestCase):
    def setUp(self):
        self.gate = make_sim_gate()
        self.client = Client(enforce_csrf_checks=True)

    def test_claimed_once_and_needs_the_agent_token(self):
        job = controller_jobs.create(self.gate, 'capture', 'up', None)
        self.assertEqual(self.client.get('/api/v1/gate/agent-jobs/').status_code, 403)
        jobs = self.client.get('/api/v1/gate/agent-jobs/', **AGENT).json()['jobs']
        self.assertEqual(jobs, [{'id': job.id, 'gate_id': self.gate.id, 'kind': 'capture', 'button': 'up', 'seconds': 6}])
        self.assertEqual(self.client.get('/api/v1/gate/agent-jobs/', **AGENT).json()['jobs'], [])
        job.refresh_from_db()
        self.assertEqual(job.state, 'dispatched')

    def test_unclaimed_job_expires_and_silent_capture_fails(self):
        late = controller_jobs.create(self.gate, 'capture', 'up', None)
        ControllerJob.objects.filter(pk=late.pk).update(created_at=timezone.now() - timedelta(seconds=31))
        self.assertEqual(controller_jobs.claim(), [])
        late.refresh_from_db()
        self.assertEqual(late.state, 'expired')

        silent = controller_jobs.create(self.gate, 'capture', 'down', None)
        controller_jobs.claim()
        controller_jobs.record_agent_result(silent, True, 'capturing')
        silent.refresh_from_db()
        self.assertEqual(silent.state, 'running')
        with mock.patch.object(controller_jobs.timezone, 'now', return_value=timezone.now() + timedelta(seconds=40)):
            controller_jobs.expire_stale()
        silent.refresh_from_db()
        self.assertEqual((silent.state, silent.result), ('failed', 'no result from the controller'))

    def test_agent_reports_a_refusal(self):
        job = controller_jobs.create(self.gate, 'save_code', 'up', None)
        response = self.client.post(f'/api/v1/gate/controller-jobs/{job.id}/result/',
                                    data=json.dumps({'sent': False, 'result': 'HTTP 409 no_capture'}),
                                    content_type='application/json', **AGENT)
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual((job.state, job.result), ('failed', 'HTTP 409 no_capture'))
        self.assertEqual(self.client.post('/api/v1/gate/controller-jobs/999/result/', data='{}',
                                          content_type='application/json', **AGENT).status_code, 404)


@override_settings(**SETTINGS)
class SimulatedCaptureFlowTest(TestCase):
    """The whole path, with the agent's part done by hand as it would do it."""

    def setUp(self):
        self.gate = make_sim_gate()
        self.token = self.gate.get_controller_token()
        self.admin = User.objects.create_user('installer', password='pw')
        self.admin.groups.add(Group.objects.get(name='gate_admin'))
        self.agent = Client(enforce_csrf_checks=True)
        self.nonce = int(time.time() * 1000)

    def device(self, path, body):
        """What the agent's ControllerClient sends to the controller."""
        self.nonce += 1
        url = f'/api/v1/gate/sim/{self.gate.id}/{path}'
        data = json.dumps(body).encode()
        return self.agent.post(url, data=data, content_type='application/json',
                               **signed(self.token, 'POST', url, data, nonce=self.nonce))

    def run_job(self, kind, button):
        self.client.force_login(self.admin)
        self.client.post(jobs_url(self.gate), data=json.dumps({'kind': kind, 'button': button}),
                         content_type='application/json')
        [job] = self.agent.get('/api/v1/gate/agent-jobs/', **AGENT).json()['jobs']
        if kind == 'capture':
            reply = self.device('capture', {'button': button, 'seconds': 6, 'job': job['id']})
            detail = None
        else:
            reply = self.device('capture/save', {'button': button})
            detail = reply.json().get('buttons', {}).get(button) if reply.status_code == 200 else None
        body = reply.json()
        self.agent.post(f"/api/v1/gate/controller-jobs/{job['id']}/result/",
                        data=json.dumps({'sent': reply.status_code < 400, 'result': body.get('result') or body.get('error'),
                                         'detail': detail}),
                        content_type='application/json', **AGENT)
        return reply, ControllerJob.objects.get(pk=job['id'])

    def test_capture_then_save(self):
        reply, capture = self.run_job('capture', 'up')
        self.assertEqual(reply.status_code, 202)
        # Finished by the controller's report, and the agent's later 'capturing' does not undo it
        self.assertEqual((capture.state, capture.result), ('done', 'captured'))
        fingerprint = capture.detail['fingerprint']
        self.assertRegex(fingerprint, r'^[0-9a-f]{8}$')
        self.assertEqual(capture.detail['bits'], 24)

        listing = self.client.get(jobs_url(self.gate)).json()
        self.assertEqual(listing['capture']['state'], 'captured')
        self.assertFalse(listing['buttons']['up']['set'])

        reply, save = self.run_job('save_code', 'up')
        self.assertEqual(reply.status_code, 200)
        self.assertEqual((save.state, save.result), ('done', 'saved'))
        self.assertEqual(save.detail['fingerprint'], fingerprint)
        self.assertIsNone(save.detail['previous_fingerprint'])
        listing = self.client.get(jobs_url(self.gate)).json()
        self.assertEqual(listing['buttons']['up'], {'set': True, 'fingerprint': fingerprint, 'bits': 24, 'pulse_us': 350})
        change = GateConfigChange.objects.filter(object_type='gatedevice', object_id=self.gate.id).first()
        self.assertEqual(change.user, self.admin)
        self.assertEqual(change.changes, {'remote_code_up': {'old': None, 'new': fingerprint}})

        # A second capture replaces it; the history shows old and new
        self.run_job('capture', 'up')
        _, again = self.run_job('save_code', 'up')
        change = GateConfigChange.objects.filter(object_type='gatedevice', object_id=self.gate.id).first()
        self.assertEqual(change.changes['remote_code_up'], {'old': fingerprint, 'new': again.detail['fingerprint']})

    def test_save_without_or_for_another_capture_is_refused(self):
        reply, save = self.run_job('save_code', 'up')
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(save.state, 'failed')
        self.run_job('capture', 'down')
        reply, save = self.run_job('save_code', 'up')
        self.assertEqual(reply.json()['error'], 'captured_for_another_button')

    def test_capture_needs_a_signed_fresh_request(self):
        url = f'/api/v1/gate/sim/{self.gate.id}/capture'
        bearer = self.agent.post(url, data=json.dumps({'button': 'up'}), content_type='application/json',
                                 HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(bearer.status_code, 401)
        self.assertEqual(self.device('capture', {'button': 'up'}).status_code, 202)
        # The same nonce again
        self.nonce -= 1
        self.assertEqual(self.device('capture', {'button': 'up'}).status_code, 409)
        self.assertEqual(self.device('capture', {'button': 'sideways'}).status_code, 400)

    def test_fingerprint_matches_the_firmware(self):
        # gate-controller/test/test_core: code_fingerprint(0x12345A, 24) == "525403a6"
        self.assertEqual(barrier_simulator.code_fingerprint(0x12345A, 24), '525403a6')


@override_settings(**SETTINGS)
class HeartbeatCaptureReportTest(TestCase):
    PATH = '/api/v1/gate/heartbeat/'

    def setUp(self):
        self.gate = GateDevice(name='West', controller_type='esp32_rf')
        self.gate.set_controller_token('dev-tok')
        self.gate.save()

    def beat(self, **fields):
        body = json.dumps({'gate_id': self.gate.id, **fields}).encode()
        return Client(enforce_csrf_checks=True).post(
            self.PATH, data=body, content_type='application/json', **signed('dev-tok', 'POST', self.PATH, body))

    def running_capture(self, button='up'):
        job = controller_jobs.create(self.gate, 'capture', button, None)
        controller_jobs.claim()
        controller_jobs.record_agent_result(job, True, 'capturing')
        return job

    def test_heartbeat_finishes_the_capture(self):
        job = self.running_capture()
        self.beat(capture={'state': 'captured', 'button': 'up', 'job': job.id, 'fingerprint': '0123abcd',
                           'bits': 24, 'pulse_us': 348, 'frames': 6, 'edges': 312, 'age_s': 1},
                  buttons={'up': {'set': False}, 'down': {'set': True, 'fingerprint': 'ffff0000', 'bits': 24,
                                                          'pulse_us': 350}})
        job.refresh_from_db()
        self.assertEqual((job.state, job.result), ('done', 'captured'))
        self.assertEqual(job.detail, {'fingerprint': '0123abcd', 'bits': 24, 'pulse_us': 348, 'frames': 6, 'edges': 312})
        self.gate.refresh_from_db()
        self.assertEqual(self.gate.controller_health['buttons']['down']['fingerprint'], 'ffff0000')

    def test_nothing_heard_fails_the_job(self):
        job = self.running_capture()
        self.beat(capture={'state': 'nothing', 'button': 'up', 'job': job.id, 'frames': 0, 'edges': 40})
        job.refresh_from_db()
        self.assertEqual((job.state, job.result), ('failed', 'nothing'))

    def test_garbage_is_dropped(self):
        self.beat(capture={'state': 'hacked', 'job': 1}, buttons={'up': {'set': 'yes'}, 'left': {'set': True}})
        self.gate.refresh_from_db()
        self.assertNotIn('capture', self.gate.controller_health)
        self.assertNotIn('buttons', self.gate.controller_health)
        self.beat(capture={'state': 'captured', 'fingerprint': 'NOT-HEX!', 'bits': 'many', 'code': 0x12345A},
                  buttons={'up': {'set': True, 'code': '12345A'}})
        self.gate.refresh_from_db()
        # A code sent by mistake is never stored, only the fields we know
        self.assertEqual(self.gate.controller_health['capture'], {'state': 'captured'})
        self.assertEqual(self.gate.controller_health['buttons'], {'up': {'set': True}})

    def test_report_for_another_gates_job_is_ignored(self):
        other = make_sim_gate(name='Other')
        job = controller_jobs.create(other, 'capture', 'up', None)
        controller_jobs.claim()
        self.beat(capture={'state': 'captured', 'job': job.id, 'fingerprint': '0123abcd'})
        job.refresh_from_db()
        self.assertEqual(job.state, 'dispatched')
