"""Live camera frames for the monitoring page."""

import threading
import time
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from ..models import Camera, GateCamera, GateDevice
from ..services import camera_service
from ..utils import secrets as gate_secrets

KEY = gate_secrets.generate_encryption_key()
# The service never asks a camera more often than this, even at a 0 setting
MIN_INTERVAL_FLOOR = 0.06
JPEG = b'\xff\xd8\xff\xe0 fake jpeg bytes'


class FakeResponse:
    """Enough of requests' streaming response for _read_snapshot."""

    def __init__(self, status_code=200, content=JPEG, content_type='image/jpeg', challenge='Digest realm="cam"'):
        self.status_code = status_code
        self.headers = {'Content-Type': content_type}
        if status_code == 401:
            self.headers['WWW-Authenticate'] = challenge
        self._content = content

    def iter_content(self, chunk_size=0):
        yield self._content

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_camera(responses):
    """Patch the live fetcher's HTTP calls; returns the mock for assertions."""
    return patch.object(camera_service.requests.Session, 'get', side_effect=responses)


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_CAMERA_ALLOWED_CIDRS=['10.0.0.0/8'],
                   GATE_SNAPSHOT_CACHE_SECONDS=30)
class CameraSnapshotEndpointTest(TestCase):
    def setUp(self):
        camera_service._fetchers.clear()
        self.camera = Camera.objects.create(
            name='Lane', host='10.0.0.5', snapshot_path='/snap.jpg', vendor='generic', username='admin',
        )
        self.camera.set_password('cam-test-pass')
        self.camera.save()

        self.operator = User.objects.create_user('guard', password='pw')
        self.operator.groups.add(Group.objects.get(name='gate_operator'))
        self.outsider = User.objects.create_user('nobody', password='pw')

    def url(self, camera_id=None):
        return f'/api/v1/gate/cameras/{camera_id or self.camera.pk}/snapshot/'

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_user_without_a_gate_role_is_refused(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_operator_gets_the_frame(self):
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response.content, JPEG)

    def test_authentication_is_negotiated_once_and_then_reused(self):
        # A fresh Digest handshake for every frame would double the camera's load.
        self.client.force_login(self.operator)
        with override_settings(GATE_SNAPSHOT_CACHE_SECONDS=0):
            with fake_camera([FakeResponse(401), FakeResponse(), FakeResponse()]) as get:
                for _ in range(2):
                    self.assertEqual(self.client.get(self.url()).status_code, 200)
                    time.sleep(MIN_INTERVAL_FLOOR)
        self.assertEqual(get.call_count, 3)  # 401 + retry, then one request per frame
        self.assertIsNotNone(get.call_args_list[2].kwargs['auth'])

    def test_viewers_share_one_fetch_within_the_interval(self):
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]) as get:
            for _ in range(5):
                self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(get.call_count, 1)

    def test_concurrent_viewers_do_not_stampede_the_camera(self):
        results = []
        # Loaded here: the threads must not touch the test's database connection.
        camera = Camera.objects.get(pk=self.camera.pk)
        with fake_camera([FakeResponse()]) as get:
            def watch():
                image, _ = camera_service.live_snapshot(camera)
                results.append(image)

            threads = [threading.Thread(target=watch) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(results, [JPEG] * 6)

    def test_the_live_view_prefers_the_cheaper_snapshot_path(self):
        Camera.objects.filter(pk=self.camera.pk).update(live_snapshot_path='/sub.jpg')
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]) as get:
            self.client.get(self.url())
        self.assertEqual(get.call_args.args[0], 'http://10.0.0.5:80/sub.jpg')

    def test_it_falls_back_to_the_recognition_snapshot_path(self):
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]) as get:
            self.client.get(self.url())
        self.assertEqual(get.call_args.args[0], 'http://10.0.0.5:80/snap.jpg')

    def test_a_missing_sub_stream_path_falls_back_to_the_main_one(self):
        # Vendor presets guess the sub-stream path; some cameras answer 404.
        Camera.objects.filter(pk=self.camera.pk).update(live_snapshot_path='/sub.jpg')
        self.client.force_login(self.operator)
        with override_settings(GATE_SNAPSHOT_CACHE_SECONDS=0):
            with fake_camera([FakeResponse(404)]) as get:
                self.assertEqual(self.client.get(self.url()).status_code, 503)
            self.assertEqual(get.call_args.args[0], 'http://10.0.0.5:80/sub.jpg')
            time.sleep(MIN_INTERVAL_FLOOR)
            with fake_camera([FakeResponse()]) as get:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(get.call_args.args[0], 'http://10.0.0.5:80/snap.jpg')

    def test_camera_failure_is_reported(self):
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse(401), FakeResponse(401)]):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error_code'], 'SNAPSHOT_FAILED')
        self.assertIn('Authentication failed', response.json()['error'])

    def test_a_failing_camera_is_not_asked_again_within_the_interval(self):
        # Some cameras answer HTTP 500 when snapshots are requested too quickly;
        # retrying on every refresh would keep them there.
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse(500)]) as get:
            for _ in range(5):
                response = self.client.get(self.url())
                self.assertEqual(response.status_code, 503)
                self.assertIn('HTTP 500', response.json()['error'])
        self.assertEqual(get.call_count, 1)

    def test_credentials_are_renegotiated_after_a_rejection(self):
        self.client.force_login(self.operator)
        with override_settings(GATE_SNAPSHOT_CACHE_SECONDS=0):
            with fake_camera([FakeResponse(401), FakeResponse(401)]):
                self.assertEqual(self.client.get(self.url()).status_code, 503)
            time.sleep(MIN_INTERVAL_FLOOR)
            with fake_camera([FakeResponse(401), FakeResponse()]) as get:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(get.call_count, 2)

    def test_changing_the_camera_starts_a_new_session(self):
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]):
            self.client.get(self.url())
        first = camera_service._fetchers[self.camera.pk]

        camera = Camera.objects.get(pk=self.camera.pk)
        camera.host = '10.0.0.9'
        camera.config_version += 1
        camera.save()
        with fake_camera([FakeResponse()]) as get:
            self.client.get(self.url())
        self.assertIsNot(camera_service._fetchers[self.camera.pk], first)
        self.assertEqual(get.call_args.args[0], 'http://10.0.0.9:80/snap.jpg')

    def test_host_outside_the_allowlist_is_refused_without_connecting(self):
        Camera.objects.filter(pk=self.camera.pk).update(host='8.8.8.8')
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]) as get:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertIn('outside the allowed camera networks', response.json()['error'])
        get.assert_not_called()

    def test_camera_without_any_snapshot_path(self):
        Camera.objects.filter(pk=self.camera.pk).update(snapshot_path='')
        self.client.force_login(self.operator)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertIn('No snapshot path', response.json()['error'])

    def test_unknown_camera(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.url(9999)).status_code, 404)

    def test_deleting_a_camera_drops_its_session(self):
        self.client.force_login(self.operator)
        with fake_camera([FakeResponse()]):
            self.client.get(self.url())
        self.assertIn(self.camera.pk, camera_service._fetchers)

        from ..services import config_audit
        config_audit.delete_with_audit(Camera.objects.get(pk=self.camera.pk), self.operator)
        self.assertNotIn(self.camera.pk, camera_service._fetchers)


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_CAMERA_ALLOWED_CIDRS=['10.0.0.0/8'])
class GateStatusCameraFieldsTest(TestCase):
    def test_status_carries_the_read_zone_for_the_live_view(self):
        camera = Camera.objects.create(
            name='Lane', host='10.0.0.5', snapshot_path='/snap.jpg',
            roi_x=0.1, roi_y=0.2, roi_w=0.3, roi_h=0.4,
        )
        gate = GateDevice.objects.create(name='Main', controller_type='simulator')
        GateCamera.objects.create(gate=gate, camera=camera, direction='in')

        operator = User.objects.create_user('guard2', password='pw')
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(operator)

        gate = self.client.get('/api/v1/gate/status/').json()['gates'][0]
        self.assertEqual(gate['cameras'][0]['roi'], {'x': 0.1, 'y': 0.2, 'w': 0.3, 'h': 0.4})
        self.assertEqual(gate['cameras'][0]['direction'], 'in')
        self.assertTrue(gate['cameras'][0]['is_enabled'])


class VendorPresetTest(TestCase):
    def test_presets_offer_a_cheap_path_for_the_live_view(self):
        camera = Camera(name='Lane', host='10.0.0.5', vendor='hikvision')
        camera_service.apply_preset(camera)
        self.assertEqual(camera.snapshot_path, '/ISAPI/Streaming/channels/101/picture')
        self.assertEqual(camera.live_snapshot_path, '/ISAPI/Streaming/channels/102/picture')


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_AGENT_TOKEN='agent-token')
class AgentTriggerReadoutTest(TestCase):
    """The agent reports what its trigger sees, so 'nothing happened' is visible."""

    AGENT = {'HTTP_AUTHORIZATION': 'Bearer agent-token'}

    def setUp(self):
        self.camera = Camera.objects.create(name='Lane', host='10.0.0.5', snapshot_path='/snap.jpg')
        operator = User.objects.create_user('guard3', password='pw')
        operator.groups.add(Group.objects.get(name='gate_admin'))
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.operator = operator

    def report(self, **extra):
        body = {'cameras': [dict({'id': self.camera.pk, 'status': 'streaming'}, **extra)]}
        return self.client.post('/api/v1/gate/agent-status/', body,
                                content_type='application/json', **self.AGENT)

    def test_scores_are_stored_and_served(self):
        response = self.report(trigger_state='occupied', fps=5.02, motion=0.0041,
                               presence=0.1837, motion_threshold=0.02, presence_threshold=0.06)
        self.assertEqual(response.status_code, 200)

        self.client.force_login(self.operator)
        data = self.client.get(f'/api/v1/gate/cameras/{self.camera.pk}/').json()
        self.assertEqual(data['agent_trigger']['state'], 'occupied')
        self.assertEqual(data['agent_trigger']['presence'], 0.1837)
        self.assertEqual(data['agent_trigger']['fps'], 5.02)
        self.assertIn('at', data['agent_trigger'])

    def test_junk_is_dropped(self):
        self.report(trigger_state='sideways', motion='lots', presence=None, fps=True)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.agent_trigger, {})

    def test_a_status_without_scores_still_works(self):
        self.assertEqual(self.report().status_code, 200)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.agent_status, 'streaming')
        self.assertEqual(self.camera.agent_trigger, {})
