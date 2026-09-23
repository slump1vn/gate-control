"""Live camera frames for the monitoring page."""

from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase, override_settings

from ..models import Camera
from ..services import camera_service
from ..utils import secrets as gate_secrets

KEY = gate_secrets.generate_encryption_key()
JPEG = b'\xff\xd8\xff\xe0 fake jpeg bytes'


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_CAMERA_ALLOWED_CIDRS=['10.0.0.0/8'])
class CameraSnapshotEndpointTest(TestCase):
    def setUp(self):
        cache.clear()
        self.camera = Camera.objects.create(
            name='Lane', host='10.0.0.5', snapshot_path='/snap.jpg', vendor='generic',
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
        with patch.object(camera_service, '_fetch_snapshot',
                          return_value=(camera_service.TestStep('snapshot', True, 'ok'), JPEG)) as fetch:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response.content, JPEG)
        self.assertEqual(fetch.call_count, 1)

    def test_frames_are_cached_so_viewers_do_not_hammer_the_camera(self):
        self.client.force_login(self.operator)
        with patch.object(camera_service, '_fetch_snapshot',
                          return_value=(camera_service.TestStep('snapshot', True, 'ok'), JPEG)) as fetch:
            for _ in range(5):
                self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(fetch.call_count, 1)

    def test_camera_failure_is_reported(self):
        self.client.force_login(self.operator)
        step = camera_service.TestStep('snapshot', False, 'Authentication failed: wrong username or password')
        with patch.object(camera_service, '_fetch_snapshot', return_value=(step, None)):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error_code'], 'SNAPSHOT_FAILED')
        self.assertIn('Authentication failed', response.json()['error'])

    def test_a_failing_camera_is_not_asked_again_within_the_interval(self):
        # Some cameras answer HTTP 500 when snapshots are requested too quickly;
        # retrying on every refresh would keep them there.
        self.client.force_login(self.operator)
        step = camera_service.TestStep('snapshot', False, 'Camera returned HTTP 500')
        with patch.object(camera_service, '_fetch_snapshot', return_value=(step, None)) as fetch:
            for _ in range(5):
                response = self.client.get(self.url())
                self.assertEqual(response.status_code, 503)
                self.assertIn('HTTP 500', response.json()['error'])
        self.assertEqual(fetch.call_count, 1)

    def test_the_camera_recovers_once_the_cached_failure_expires(self):
        self.client.force_login(self.operator)
        step = camera_service.TestStep('snapshot', False, 'Camera returned HTTP 500')
        with patch.object(camera_service, '_fetch_snapshot', return_value=(step, None)):
            self.assertEqual(self.client.get(self.url()).status_code, 503)
        cache.clear()
        with patch.object(camera_service, '_fetch_snapshot',
                          return_value=(camera_service.TestStep('snapshot', True, 'ok'), JPEG)) as fetch:
            self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(fetch.call_count, 1)

    def test_host_outside_the_allowlist_is_refused_without_connecting(self):
        Camera.objects.filter(pk=self.camera.pk).update(host='8.8.8.8')
        self.client.force_login(self.operator)
        with patch.object(camera_service, '_fetch_snapshot') as fetch:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertIn('outside the allowed camera networks', response.json()['error'])
        fetch.assert_not_called()

    def test_camera_without_snapshot_path(self):
        Camera.objects.filter(pk=self.camera.pk).update(snapshot_path='')
        self.client.force_login(self.operator)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertIn('No snapshot path', response.json()['error'])

    def test_unknown_camera(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.url(9999)).status_code, 404)


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_CAMERA_ALLOWED_CIDRS=['10.0.0.0/8'])
class GateStatusCameraFieldsTest(TestCase):
    def test_status_carries_the_read_zone_for_the_live_view(self):
        camera = Camera.objects.create(
            name='Lane', host='10.0.0.5', snapshot_path='/snap.jpg',
            roi_x=0.1, roi_y=0.2, roi_w=0.3, roi_h=0.4,
        )
        from ..models import GateDevice
        GateDevice.objects.create(name='Main', camera=camera, controller_type='simulator')

        operator = User.objects.create_user('guard2', password='pw')
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(operator)

        gate = self.client.get('/api/v1/gate/status/').json()['gates'][0]
        self.assertEqual(gate['camera_roi'], {'x': 0.1, 'y': 0.2, 'w': 0.3, 'h': 0.4})
        self.assertTrue(gate['camera_enabled'])
