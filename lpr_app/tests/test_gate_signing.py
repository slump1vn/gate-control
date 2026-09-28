"""Controller contract v2: signed requests to the simulator and signed heartbeats."""

import json
import time

from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings

from lpr_app import metrics
from lpr_app.models import GateDevice
from lpr_app.utils import gate_signing

from .test_gate_simulator import make_sim_gate

SETTINGS = dict(GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), GATE_MODE='shadow')


def signed(token, method, path, body=b'', nonce=None, ts=None, signature=None):
    nonce = str(nonce if nonce is not None else int(time.time() * 1000))
    ts = str(ts if ts is not None else int(time.time()))
    return {
        'HTTP_X_GATE_NONCE': nonce,
        'HTTP_X_GATE_TS': ts,
        'HTTP_X_GATE_SIG': signature or gate_signing.sign(token, method, path, nonce, ts, body),
    }


class SigningVectorTest(TestCase):
    def test_shared_test_vector(self):
        # gate-agent/gate_agent/signing.py and the firmware check the same vector
        v = gate_signing.TEST_VECTOR
        self.assertEqual(gate_signing.sign(v['secret'], v['method'], v['path'], v['nonce'], v['ts'], v['body']),
                         v['signature'])
        self.assertTrue(gate_signing.verify(v['secret'], v['method'], v['path'], v['nonce'], v['ts'], v['body'],
                                            v['signature'].upper()))
        self.assertFalse(gate_signing.verify('', v['method'], v['path'], v['nonce'], v['ts'], v['body'],
                                             v['signature']))


@override_settings(**SETTINGS)
class SignedSimulatorTest(TestCase):
    def setUp(self):
        self.gate = make_sim_gate()
        self.token = self.gate.get_controller_token()
        self.client = Client(enforce_csrf_checks=True)

    def path(self, command):
        return f'/api/v1/gate/sim/{self.gate.id}/{command}'

    def post(self, command, body=b'{}', **kw):
        return self.client.post(self.path(command), data=body, content_type='application/json',
                                **signed(self.token, 'POST', self.path(command), body, **kw))

    def test_signed_open_and_status(self):
        response = self.post('open')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['result'], 'ok')
        status = self.client.get(self.path('status'), **signed(self.token, 'GET', self.path('status')))
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()['arm_state'], 'moving')

    def test_bad_signature_or_signed_for_something_else(self):
        self.assertEqual(self.post('open', signature='0' * 64).status_code, 401)
        # Signed for close, sent to open
        headers = signed(self.token, 'POST', self.path('close'), b'{}')
        response = self.client.post(self.path('open'), data=b'{}', content_type='application/json', **headers)
        self.assertEqual(response.status_code, 401)
        # Signed with the wrong token
        headers = signed('not-the-token', 'POST', self.path('open'), b'{}')
        response = self.client.post(self.path('open'), data=b'{}', content_type='application/json', **headers)
        self.assertEqual(response.status_code, 401)

    def test_malformed_nonce_is_unauthorized(self):
        self.assertEqual(self.post('open', nonce='abc').status_code, 401)

    def test_replayed_nonce_and_stale_timestamp(self):
        self.assertEqual(self.post('stop', nonce=5_000_000_000_000).status_code, 200)
        self.assertEqual(self.post('stop', nonce=5_000_000_000_000).status_code, 409)
        self.assertEqual(self.post('stop', nonce=5_000_000_000_001, ts=int(time.time()) - 120).status_code, 401)

    def test_bearer_v1_still_accepted_for_now(self):
        response = self.client.post(
            self.path('open'), data=json.dumps({'nonce': int(time.time() * 1000), 'ts': time.time()}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        self.assertEqual(response.status_code, 200)


@override_settings(**SETTINGS)
class SignedHeartbeatTest(TestCase):
    PATH = '/api/v1/gate/heartbeat/'

    def setUp(self):
        self.gate = GateDevice(name='West', controller_type='esp32_rf')
        self.gate.set_controller_token('dev-tok')
        self.gate.save()

    def beat(self, token='dev-tok', ts=None, **fields):
        body = json.dumps({'gate_id': self.gate.id, 'firmware_version': 'rf-0.1.0', **fields}).encode()
        return Client(enforce_csrf_checks=True).post(
            self.PATH, data=body, content_type='application/json', **signed(token, 'POST', self.PATH, body, ts=ts))

    def test_signed_heartbeat_with_health(self):
        response = self.beat(transport='rf433', wifi_rssi=-67, clock_synced=True, dry_run=True,
                             rf_tx_count=12, uptime_s=3600, arm_state='unknown')
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.json()['server_ts'], time.time(), delta=5)
        self.gate.refresh_from_db()
        self.assertTrue(self.gate.is_online())
        self.assertEqual(self.gate.controller_health, {
            'transport': 'rf433', 'wifi_rssi': -67, 'clock_synced': True, 'dry_run': True,
            'rf_tx_count': 12, 'uptime_s': 3600,
        })

    def test_health_fields_are_type_checked(self):
        self.beat(transport='carrier-pigeon', wifi_rssi='strong', clock_synced='yes', rf_tx_count=-1,
                  dry_run=1, uptime_s=True, extra='ignored')
        self.gate.refresh_from_db()
        self.assertEqual(self.gate.controller_health, {})
        self.beat(wifi_rssi=True)
        self.gate.refresh_from_db()
        self.assertEqual(self.gate.controller_health, {})

    def test_wrong_signature_and_stale_heartbeat_are_refused(self):
        wrong = self.beat(token='nope')
        self.assertEqual(wrong.status_code, 403)
        self.assertNotIn('server_ts', wrong.json())
        # A device that just booted without NTP signs with a 1970 clock: refused,
        # but told the time so its next heartbeat and its commands can work
        stale = self.beat(ts=5)
        self.assertEqual(stale.status_code, 403)
        self.assertEqual(stale.json()['error_code'], 'CLOCK_SKEW')
        self.assertAlmostEqual(stale.json()['server_ts'], time.time(), delta=5)
        self.gate.refresh_from_db()
        self.assertIsNone(self.gate.last_seen)

    def test_status_and_metrics_carry_the_health(self):
        self.beat(transport='rf433', wifi_rssi=-82, dry_run=True)
        self.gate.refresh_from_db()
        from lpr_app.utils.gate_serializers import serialize_gate
        self.assertEqual(serialize_gate(self.gate)['controller_health']['wifi_rssi'], -82)
        with override_settings(GATE_MODE='live'):
            metrics.update_gate_metrics()
        self.assertEqual(metrics.GATE_CONTROLLER_RSSI.labels(gate='West')._value.get(), -82)
        self.assertEqual(metrics.GATE_CONTROLLER_DRY_RUN.labels(gate='West')._value.get(), 1)
        self.assertEqual(metrics.GATE_LIVE_MODE._value.get(), 1)
