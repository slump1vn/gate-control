"""Live video through go2rtc: the gateway's authorization and stream registration."""

from unittest.mock import patch

import requests
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from ..models import Camera, GateCamera, GateDevice
from ..services import live_stream
from ..utils import secrets as gate_secrets

KEY = gate_secrets.generate_encryption_key()
API = 'http://go2rtc:1984'
URL = '/api/v1/gate/live/authorize/'


class FakeGo2rtc:
    """go2rtc's /api/streams: PUT creates (then 400s without a config file), GET finds, DELETE drops."""

    def __init__(self, accept=True):
        self.streams = {}
        self.calls = []
        self.accept = accept

    def __call__(self, method, url, params=None, timeout=None):
        self.calls.append((method, dict(params or {})))
        response = requests.Response()
        response.status_code = 200
        if method == 'PUT':
            if self.accept:
                self.streams[params['name']] = params['src']
            response.status_code = 400
        elif method == 'GET':
            response.status_code = 200 if params['src'] in self.streams else 404
        elif method == 'DELETE':
            self.streams.pop(params['src'], None)
        return response


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_CAMERA_ALLOWED_CIDRS=['10.0.0.0/8'],
                   GATE_LIVE_STREAM_API=API, GATE_LIVE_STREAM_PATH='/live/')
class LiveAuthorizeTest(TestCase):
    def setUp(self):
        live_stream._registered.clear()
        self.camera = Camera.objects.create(
            name='Lane', host='10.0.0.5', vendor='dahua', username='admin',
            sub_stream_path='/cam/realmonitor?channel=1&subtype=1',
        )
        self.camera.set_password('cam-test-pass')
        self.camera.save()
        self.operator = User.objects.create_user('guard', password='pw')
        self.operator.groups.add(Group.objects.get(name='gate_operator'))
        self.outsider = User.objects.create_user('nobody', password='pw')
        self.go2rtc = FakeGo2rtc()
        patcher = patch.object(live_stream.requests, 'request', side_effect=self.go2rtc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def authorize(self, src=None, uri=None):
        uri = uri if uri is not None else f'/live/api/ws?src={src or f"camera-{self.camera.pk}"}'
        return self.client.get(URL, HTTP_X_ORIGINAL_URI=uri)

    def test_anonymous_and_users_without_a_role_are_refused(self):
        self.assertEqual(self.authorize().status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.authorize().status_code, 403)
        self.assertEqual(self.go2rtc.calls, [])

    def test_operator_is_let_through_and_the_stream_registered(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.authorize().status_code, 204)
        src = self.go2rtc.streams[f'camera-{self.camera.pk}']
        self.assertTrue(src.startswith('rtsp://admin:cam-test-pass@10.0.0.5:554/cam/realmonitor'))
        self.assertIn('subtype=1', src)

    def test_registered_once_while_the_camera_is_unchanged(self):
        self.client.force_login(self.operator)
        self.authorize()
        self.authorize()
        self.assertEqual([m for m, _ in self.go2rtc.calls].count('PUT'), 1)

    def test_changed_camera_is_registered_again(self):
        self.client.force_login(self.operator)
        self.authorize()
        self.camera.sub_stream_path = '/cam/realmonitor?channel=1&subtype=2'
        self.camera.config_version += 1
        self.camera.save()
        self.authorize()
        self.assertIn('subtype=2', self.go2rtc.streams[f'camera-{self.camera.pk}'])

    def test_stream_lost_by_go2rtc_is_registered_again(self):
        # go2rtc restarted and forgot everything registered with it
        self.client.force_login(self.operator)
        self.authorize()
        self.go2rtc.streams.clear()
        self.assertEqual(self.authorize().status_code, 204)
        self.assertIn(f'camera-{self.camera.pk}', self.go2rtc.streams)

    def test_unknown_or_malformed_stream_is_refused(self):
        self.client.force_login(self.operator)
        for uri in ('/live/api/ws?src=camera-999', '/live/api/ws?src=../api/streams',
                    '/live/api/ws', '/live/api/ws?src=camera-1&src=camera-2', ''):
            self.assertEqual(self.authorize(uri=uri).status_code, 404, uri)
        self.assertEqual(self.go2rtc.calls, [])

    def test_go2rtc_refusing_or_down_is_503(self):
        self.client.force_login(self.operator)
        self.go2rtc.accept = False
        self.assertEqual(self.authorize().status_code, 503)
        with patch.object(live_stream.requests, 'request', side_effect=requests.ConnectionError()):
            response = self.authorize()
        self.assertEqual(response.status_code, 503)
        self.assertIn('go2rtc unreachable', response.json()['error'])

    def test_camera_outside_the_allowed_networks_is_503(self):
        self.client.force_login(self.operator)
        Camera.objects.filter(pk=self.camera.pk).update(host='192.168.1.5')
        self.assertEqual(self.authorize().status_code, 503)
        self.assertEqual(self.go2rtc.streams, {})

    @override_settings(GATE_LIVE_STREAM_API='')
    def test_off_without_go2rtc(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.authorize().status_code, 404)


@override_settings(GATE_LIVE_STREAM_API=API)
class PlayerUrlTest(TestCase):
    def setUp(self):
        self.camera = Camera.objects.create(name='Lane', host='10.0.0.5', sub_stream_path='/sub')
        self.gate = GateDevice.objects.create(name='West')
        GateCamera.objects.create(gate=self.gate, camera=self.camera, direction='in')
        self.operator = User.objects.create_user('guard', password='pw')
        self.operator.groups.add(Group.objects.get(name='gate_operator'))

    def test_player_url(self):
        self.assertEqual(live_stream.player_url(self.camera), f'/live/api/ws?src=camera-{self.camera.pk}')
        with override_settings(GATE_LIVE_STREAM_PATH='wss://cams.example.org/live'):
            self.assertEqual(live_stream.player_url(self.camera),
                             f'wss://cams.example.org/live/api/ws?src=camera-{self.camera.pk}')
        with override_settings(GATE_LIVE_STREAM_API=''):
            self.assertIsNone(live_stream.player_url(self.camera))
        self.camera.sub_stream_path = ''
        self.assertIsNone(live_stream.player_url(self.camera))

    def test_camera_id_from_uri(self):
        self.assertEqual(live_stream.camera_id_from_uri('/live/api/ws?src=camera-12'), 12)
        self.assertIsNone(live_stream.camera_id_from_uri('/live/api/ws?src=camera-12x'))
        self.assertIsNone(live_stream.camera_id_from_uri(None))

    def test_gate_status_carries_the_player_url(self):
        self.client.force_login(self.operator)
        response = self.client.get('/api/v1/gate/status/')
        self.assertEqual(response.status_code, 200)
        cameras = [c for g in response.json()['gates'] for c in g['cameras']]
        self.assertEqual(cameras[0]['live_stream_url'], f'/live/api/ws?src=camera-{self.camera.pk}')
