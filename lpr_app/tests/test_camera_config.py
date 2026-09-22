import socket
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from lpr_app.models import Camera, GateConfigChange, GateDevice
from lpr_app.services import camera_service, config_audit
from lpr_app.services.camera_service import CameraHostError
from lpr_app.utils import secrets as gate_secrets

KEY = Fernet.generate_key().decode()
PRIVATE = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']


def _addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 0)) for ip in ips]


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY)
class SecretsTest(SimpleTestCase):
    def test_round_trip(self):
        token = gate_secrets.encrypt_secret('cam-test-pass')
        self.assertNotIn('cam-test-pass', token)
        self.assertEqual(gate_secrets.decrypt_secret(token), 'cam-test-pass')

    def test_empty_decrypts_to_empty(self):
        self.assertEqual(gate_secrets.decrypt_secret(''), '')

    @override_settings(GATE_CONFIG_ENCRYPTION_KEY='')
    def test_missing_key(self):
        with self.assertRaises(gate_secrets.SecretKeyMissing):
            gate_secrets.encrypt_secret('x')

    @override_settings(GATE_CONFIG_ENCRYPTION_KEY='not-a-fernet-key')
    def test_malformed_key(self):
        with self.assertRaises(gate_secrets.SecretKeyMissing):
            gate_secrets.encrypt_secret('x')

    def test_wrong_key(self):
        token = gate_secrets.encrypt_secret('x')
        with override_settings(GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(gate_secrets.SecretDecryptError):
                gate_secrets.decrypt_secret(token)

    def test_tokens_equal(self):
        self.assertTrue(gate_secrets.tokens_equal('abc', 'abc'))
        self.assertFalse(gate_secrets.tokens_equal('abc', 'abd'))
        self.assertFalse(gate_secrets.tokens_equal('', ''))
        self.assertTrue(len(gate_secrets.generate_token()) >= 32)
        Fernet(gate_secrets.generate_encryption_key().encode())


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY)
class CameraModelTest(TestCase):
    def test_password_stored_encrypted(self):
        cam = Camera(name='Gate', host='192.168.1.64')
        cam.set_password('cam-test-pass')
        cam.save()
        raw = Camera.objects.values_list('password_encrypted', flat=True).get(pk=cam.pk)
        self.assertNotIn('cam-test-pass', raw)
        self.assertTrue(cam.password_set)
        self.assertEqual(Camera.objects.get(pk=cam.pk).get_password(), 'cam-test-pass')

    def test_defaults(self):
        cam = Camera.objects.create(name='Gate', host='192.168.1.64')
        self.assertEqual((cam.rtsp_port, cam.http_port), (554, 80))
        self.assertFalse(cam.password_set)
        self.assertIsNone(cam.roi)

    def test_roi_validation(self):
        cam = Camera(name='Gate', host='192.168.1.64', roi_x=0.1, roi_y=0.2, roi_w=0.5, roi_h=0.5)
        cam.full_clean()
        self.assertEqual(cam.roi, {'x': 0.1, 'y': 0.2, 'w': 0.5, 'h': 0.5})
        for bad in (dict(roi_x=0.1), dict(roi_x=0.6, roi_y=0, roi_w=0.5, roi_h=0.5),
                    dict(roi_x=0, roi_y=0, roi_w=0, roi_h=0.5)):
            with self.assertRaises(ValidationError):
                Camera(name='Gate', host='192.168.1.64', **bad).full_clean()

    def test_port_range(self):
        with self.assertRaises(ValidationError):
            Camera(name='Gate', host='192.168.1.64', rtsp_port=70000).full_clean()

    def test_gate_controller_token_and_online(self):
        from django.utils import timezone
        from datetime import timedelta
        gate = GateDevice(name='Main')
        self.assertFalse(gate.is_online())
        gate.set_controller_token('tok')
        gate.save()
        self.assertEqual(GateDevice.objects.get(pk=gate.pk).get_controller_token(), 'tok')
        self.assertTrue(gate.controller_token_set)
        now = timezone.now()
        gate.last_seen = now - timedelta(seconds=10)
        self.assertTrue(gate.is_online(now))
        gate.last_seen = now - timedelta(seconds=60)
        self.assertFalse(gate.is_online(now))


class HostValidationTest(SimpleTestCase):
    def test_valid_hosts(self):
        for host in ('192.168.1.64', 'cam-gate1.local', 'camera'):
            self.assertEqual(camera_service.validate_host_syntax(host), host)

    def test_invalid_hosts(self):
        for host in ('', 'http://192.168.1.64', 'admin:pw@192.168.1.64', '192.168.1.64:554',
                     '192.168.1.300', 'bad_host!', '-bad.local', '192.168.1.64/path'):
            with self.assertRaises(CameraHostError, msg=host):
                camera_service.validate_host_syntax(host)

    @override_settings(GATE_CAMERA_ALLOWED_CIDRS=PRIVATE)
    def test_private_address_allowed(self):
        self.assertEqual(camera_service.resolve_allowed_host('192.168.1.64'), '192.168.1.64')

    @override_settings(GATE_CAMERA_ALLOWED_CIDRS=PRIVATE)
    def test_site_address_rejected_by_default(self):
        with self.assertRaises(CameraHostError) as ctx:
            camera_service.resolve_allowed_host('172.87.80.80')
        self.assertIn('GATE_CAMERA_ALLOWED_CIDRS', str(ctx.exception))

    @override_settings(GATE_CAMERA_ALLOWED_CIDRS=PRIVATE + ['172.87.80.0/24'])
    def test_allowlist_extended_explicitly(self):
        self.assertEqual(camera_service.resolve_allowed_host('172.87.80.80'), '172.87.80.80')

    @override_settings(GATE_CAMERA_ALLOWED_CIDRS=PRIVATE)
    def test_hostname_resolving_outside_allowlist_rejected(self):
        with mock.patch('socket.getaddrinfo', return_value=_addrinfo('8.8.8.8')):
            with self.assertRaises(CameraHostError):
                camera_service.resolve_allowed_host('camera.example')

    @override_settings(GATE_CAMERA_ALLOWED_CIDRS=PRIVATE)
    def test_any_resolved_address_outside_allowlist_rejected(self):
        with mock.patch('socket.getaddrinfo', return_value=_addrinfo('192.168.1.5', '8.8.8.8')):
            with self.assertRaises(CameraHostError):
                camera_service.resolve_allowed_host('camera.example')

    def test_unresolvable(self):
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror):
            with self.assertRaises(CameraHostError):
                camera_service.resolve_allowed_host('nowhere.local')


class UrlBuildingTest(SimpleTestCase):
    def _camera(self, **kw):
        defaults = dict(name='Gate', host='192.168.1.64', username='admin', vendor='hikvision')
        defaults.update(kw)
        cam = Camera(**defaults)
        camera_service.apply_preset(cam)
        return cam

    def test_hikvision_preset(self):
        cam = self._camera()
        self.assertEqual(cam.main_stream_path, '/Streaming/Channels/101')
        self.assertEqual(cam.snapshot_path, '/ISAPI/Streaming/channels/101/picture')

    def test_preset_does_not_overwrite_custom_path(self):
        cam = self._camera(snapshot_path='/custom.jpg')
        self.assertEqual(cam.snapshot_path, '/custom.jpg')

    def test_dahua_preset(self):
        cam = self._camera(vendor='dahua')
        self.assertEqual(cam.sub_stream_path, '/cam/realmonitor?channel=1&subtype=1')

    def test_rtsp_url_quotes_credentials(self):
        cam = self._camera()
        url = camera_service.rtsp_url(cam, 'p@ss:w/rd')
        self.assertEqual(url, 'rtsp://admin:p%40ss%3Aw%2Frd@192.168.1.64:554/Streaming/Channels/101')
        self.assertIn('/Streaming/Channels/102', camera_service.rtsp_url(cam, 'x', stream='sub'))

    def test_rtsp_url_without_credentials(self):
        cam = self._camera(username='')
        self.assertEqual(camera_service.rtsp_url(cam, ''), 'rtsp://192.168.1.64:554/Streaming/Channels/101')

    def test_snapshot_url(self):
        cam = self._camera(http_port=8080, snapshot_path='snap.jpg')
        self.assertEqual(camera_service.snapshot_url(cam), 'http://192.168.1.64:8080/snap.jpg')

    def test_redact(self):
        self.assertEqual(
            camera_service.redact_url('rtsp://admin:cam-test-pass@172.87.80.80:554/x'),
            'rtsp://admin:***@172.87.80.80:554/x',
        )
        self.assertEqual(camera_service.redact_url('rtsp://1.2.3.4/x'), 'rtsp://1.2.3.4/x')


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b'', chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


JPEG = {'Content-Type': 'image/jpeg'}


@override_settings(GATE_CAMERA_ALLOWED_CIDRS=PRIVATE)
class ConnectionTestTest(SimpleTestCase):
    def setUp(self):
        self.cam = Camera(name='Gate', host='192.168.1.64', username='admin', vendor='hikvision')
        camera_service.apply_preset(self.cam)
        patcher = mock.patch('socket.create_connection')
        self.tcp = patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, responses):
        with mock.patch('lpr_app.services.camera_service.requests.get', side_effect=responses) as get:
            result = camera_service.test_connection(self.cam, 'cam-test-pass')
        return result, get

    def test_success_with_digest(self):
        challenge = FakeResponse(401, {'WWW-Authenticate': 'Digest realm="x", nonce="y"'})
        result, get = self._run([challenge, FakeResponse(200, JPEG, b'\xff\xd8jpeg')])
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.image, b'\xff\xd8jpeg')
        auth = get.call_args_list[1].kwargs['auth']
        self.assertEqual(type(auth).__name__, 'HTTPDigestAuth')
        self.assertFalse(get.call_args_list[1].kwargs['allow_redirects'])
        # Request goes to the resolved IP, with the original Host header
        self.assertTrue(get.call_args_list[1].args[0].startswith('http://192.168.1.64:80/'))

    def test_basic_fallback(self):
        challenge = FakeResponse(401, {'WWW-Authenticate': 'Basic realm="x"'})
        _, get = self._run([challenge, FakeResponse(200, JPEG, b'img')])
        self.assertEqual(type(get.call_args_list[1].kwargs['auth']).__name__, 'HTTPBasicAuth')

    def test_no_auth_needed(self):
        result, get = self._run([FakeResponse(200, JPEG, b'img')])
        self.assertTrue(result.ok)
        self.assertEqual(get.call_count, 1)

    def test_wrong_password(self):
        result, _ = self._run([
            FakeResponse(401, {'WWW-Authenticate': 'Digest realm="x"'}),
            FakeResponse(401),
        ])
        self.assertFalse(result.ok)
        self.assertIn('wrong username or password', result.error)

    def test_redirect_not_followed(self):
        result, _ = self._run([FakeResponse(302, {'Location': 'http://8.8.8.8/'})])
        self.assertFalse(result.ok)
        self.assertIn('redirects are not followed', result.error)

    def test_not_found(self):
        result, _ = self._run([FakeResponse(404)])
        self.assertIn('not found', result.error)

    def test_server_error(self):
        result, _ = self._run([FakeResponse(500)])
        self.assertIn('HTTP 500', result.error)

    def test_non_jpeg_rejected(self):
        result, _ = self._run([FakeResponse(200, {'Content-Type': 'text/html'}, b'<html>')])
        self.assertFalse(result.ok)
        self.assertIn('Expected a JPEG', result.error)
        self.assertIsNone(result.image)

    def test_oversize_aborted(self):
        chunk = b'x' * (1024 * 1024)
        result, _ = self._run([FakeResponse(200, JPEG, chunks=[chunk] * 6)])
        self.assertIn('larger than 5 MB', result.error)

    def test_timeout(self):
        import requests
        result, _ = self._run(requests.Timeout())
        self.assertIn('timed out', result.error)

    def test_http_port_unreachable(self):
        import requests
        result, _ = self._run(requests.ConnectionError())
        self.assertIn('cannot connect to HTTP port 80', result.error)

    def test_rtsp_port_refused(self):
        self.tcp.side_effect = ConnectionRefusedError()
        result, _ = self._run([FakeResponse(200, JPEG, b'img')])
        self.assertFalse(result.ok)
        self.assertIn('connection refused', result.error)

    def test_rtsp_port_timeout(self):
        self.tcp.side_effect = socket.timeout()
        result, _ = self._run([FakeResponse(200, JPEG, b'img')])
        self.assertIn('timed out', result.error)

    def test_host_outside_allowlist_makes_no_connection(self):
        self.cam.host = '172.87.80.80'
        result, get = self._run([])
        self.assertFalse(result.ok)
        get.assert_not_called()
        self.tcp.assert_not_called()

    def test_no_snapshot_path_skips(self):
        self.cam.snapshot_path = ''
        result, get = self._run([])
        get.assert_not_called()
        self.assertTrue(result.ok)
        self.assertIsNone(result.steps[-1].ok)


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY)
class ConfigAuditTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('admin1', password='x')

    def test_create_recorded(self):
        cam = Camera(name='Gate', host='192.168.1.64')
        cam.set_password('secret')
        cam.save()
        entry = config_audit.record_change(self.user, cam, None)
        self.assertEqual(entry.action, 'create')
        self.assertEqual(entry.changes['host']['new'], '192.168.1.64')
        self.assertEqual(entry.changes['password'], 'changed')

    def test_update_masks_password_and_bumps_version(self):
        cam = Camera(name='Gate', host='192.168.1.64')
        cam.set_password('first-secret')
        cam.save()
        before = config_audit.snapshot(cam)
        cam.set_password('second-secret')
        cam.host = '192.168.1.65'
        config_audit.bump_version_if_changed(cam, before)
        cam.save()
        entry = config_audit.record_change(self.user, cam, before)
        self.assertEqual(cam.config_version, 2)
        self.assertEqual(entry.changes['password'], 'changed')
        self.assertEqual(entry.changes['host'], {'old': '192.168.1.64', 'new': '192.168.1.65'})
        self.assertNotIn('secret', str(entry.changes))

    def test_no_change_no_entry(self):
        cam = Camera.objects.create(name='Gate', host='192.168.1.64')
        before = config_audit.snapshot(cam)
        config_audit.bump_version_if_changed(cam, before)
        self.assertIsNone(config_audit.record_change(self.user, cam, before))
        self.assertEqual(cam.config_version, 1)

    def test_delete_and_gate_device(self):
        gate = GateDevice.objects.create(name='Main')
        entry = config_audit.record_change(self.user, gate, None, action='delete')
        self.assertEqual((entry.action, entry.object_type), ('delete', 'gatedevice'))

    def test_anonymous_user_stored_as_null(self):
        from django.contrib.auth.models import AnonymousUser
        cam = Camera.objects.create(name='Gate', host='192.168.1.64')
        entry = config_audit.record_change(AnonymousUser(), cam, None)
        self.assertIsNone(entry.user)


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY, GATE_CAMERA_ALLOWED_CIDRS=PRIVATE)
class CameraAdminTest(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.force_login(self.admin)

    def _post(self, url, **overrides):
        data = {
            'name': 'Gate 1', 'is_enabled': 'on', 'host': '192.168.1.64', 'rtsp_port': 554,
            'http_port': 80, 'username': 'admin', 'password': 'cam-test-pass', 'vendor': 'hikvision',
            'main_stream_path': '', 'sub_stream_path': '', 'snapshot_path': '',
            'prefer_snapshot': 'on', 'motion_threshold': 0.02, 'settle_ms': 800, 'cooldown_s': 5,
        }
        data.update(overrides)
        return self.client.post(url, data)

    def test_create_via_admin(self):
        response = self._post('/admin/lpr_app/camera/add/')
        self.assertEqual(response.status_code, 302)
        cam = Camera.objects.get()
        self.assertEqual(cam.get_password(), 'cam-test-pass')
        self.assertEqual(cam.main_stream_path, '/Streaming/Channels/101')
        self.assertEqual(cam.updated_by, self.admin)
        self.assertEqual(GateConfigChange.objects.get().changes['password'], 'changed')

    def test_blank_password_keeps_existing(self):
        self._post('/admin/lpr_app/camera/add/')
        cam = Camera.objects.get()
        self._post(f'/admin/lpr_app/camera/{cam.pk}/change/', password='', host='192.168.1.65')
        cam.refresh_from_db()
        self.assertEqual(cam.get_password(), 'cam-test-pass')
        self.assertEqual(cam.host, '192.168.1.65')
        self.assertEqual(cam.config_version, 2)
        latest = GateConfigChange.objects.first()
        self.assertNotIn('password', latest.changes)

    def test_changelist_shows_password_set_not_password(self):
        self._post('/admin/lpr_app/camera/add/')
        page = self.client.get('/admin/lpr_app/camera/').content.decode()
        self.assertIn('Password set', page)
        self.assertNotIn('cam-test-pass', page)

    def test_admin_test_connection_action(self):
        self._post('/admin/lpr_app/camera/add/')
        cam = Camera.objects.get()
        ok = camera_service.ConnectionTestResult(steps=[camera_service.TestStep('host', True, 'ok'),
                                                        camera_service.TestStep('snapshot', True, 'Received 40 KB')])
        with mock.patch('lpr_app.services.camera_service.test_connection', return_value=ok) as tc:
            page = self.client.post('/admin/lpr_app/camera/', {'action': 'test_connection', '_selected_action': [cam.pk]},
                                    follow=True)
        self.assertEqual(tc.call_args.args[1], 'cam-test-pass')
        self.assertContains(page, 'snapshot: ok (Received 40 KB)')
        cam.refresh_from_db()
        self.assertTrue(cam.last_test_ok)
        failed = camera_service.ConnectionTestResult(steps=[camera_service.TestStep('snapshot', False, 'Authentication failed')])
        with mock.patch('lpr_app.services.camera_service.test_connection', return_value=failed):
            page = self.client.post('/admin/lpr_app/camera/', {'action': 'test_connection', '_selected_action': [cam.pk]},
                                    follow=True)
        self.assertContains(page, 'snapshot: FAILED (Authentication failed)')
        with override_settings(GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            page = self.client.post('/admin/lpr_app/camera/', {'action': 'test_connection', '_selected_action': [cam.pk]},
                                    follow=True)
        self.assertContains(page, 'stored password unreadable')

    def test_change_form_never_renders_password(self):
        self._post('/admin/lpr_app/camera/add/')
        cam = Camera.objects.get()
        page = self.client.get(f'/admin/lpr_app/camera/{cam.pk}/change/').content.decode()
        self.assertNotIn('cam-test-pass', page)
        self.assertNotIn(cam.password_encrypted, page)

    def test_host_outside_allowlist_rejected(self):
        response = self._post('/admin/lpr_app/camera/add/', host='172.87.80.80')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'GATE_CAMERA_ALLOWED_CIDRS')
        self.assertFalse(Camera.objects.exists())

    def test_host_with_scheme_rejected(self):
        response = self._post('/admin/lpr_app/camera/add/', host='http://192.168.1.64')
        self.assertContains(response, 'without scheme')

    @override_settings(GATE_CONFIG_ENCRYPTION_KEY='')
    def test_missing_key_blocks_password_save(self):
        response = self._post('/admin/lpr_app/camera/add/')
        self.assertContains(response, 'GATE_CONFIG_ENCRYPTION_KEY')
        self.assertFalse(Camera.objects.exists())

    def test_delete_audited(self):
        self._post('/admin/lpr_app/camera/add/')
        cam = Camera.objects.get()
        self.client.post(f'/admin/lpr_app/camera/{cam.pk}/delete/', {'post': 'yes'})
        self.assertFalse(Camera.objects.exists())
        self.assertEqual(GateConfigChange.objects.first().action, 'delete')

    def test_gate_device_admin_token(self):
        response = self.client.post('/admin/lpr_app/gatedevice/add/', {
            'name': 'Main', 'location': '', 'direction': 'in', 'camera': '',
            'controller_type': 'esp32', 'controller_url': 'http://192.168.1.50', 'controller_token': 'tok123',
            'is_enabled': 'on',
            'simulator-TOTAL_FORMS': '1', 'simulator-INITIAL_FORMS': '0',
            'simulator-MIN_NUM_FORMS': '0', 'simulator-MAX_NUM_FORMS': '1',
            'simulator-0-travel_seconds': '3', 'simulator-0-auto_close_seconds': '10',
        })
        self.assertEqual(response.status_code, 302)
        gate = GateDevice.objects.get()
        self.assertEqual(gate.get_controller_token(), 'tok123')
        self.assertEqual(GateConfigChange.objects.get().changes['controller_token'], 'changed')
        page = self.client.get(f'/admin/lpr_app/gatedevice/{gate.pk}/change/').content.decode()
        self.assertNotIn('tok123', page)
        self.client.post(f'/admin/lpr_app/gatedevice/{gate.pk}/delete/', {'post': 'yes'})
        self.assertEqual(GateConfigChange.objects.first().action, 'delete')

    def test_audit_models_read_only(self):
        self.assertEqual(self.client.get('/admin/lpr_app/accessevent/add/').status_code, 403)
        self.assertEqual(self.client.get('/admin/lpr_app/gateconfigchange/add/').status_code, 403)
        self.assertEqual(self.client.get('/admin/lpr_app/accessevent/').status_code, 200)
        self.assertEqual(self.client.get('/admin/lpr_app/vehicle/').status_code, 200)
