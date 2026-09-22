import json
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from lpr_app.models import AccessEvent, Camera, GateConfigChange, GateDevice, UploadedImage, Vehicle
from lpr_app.services import gate_service
from lpr_app.services.camera_service import ConnectionTestResult, TestStep

User = get_user_model()
AGENT = {'HTTP_AUTHORIZATION': 'Bearer agent-secret'}
PRIVATE = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']
SETTINGS = dict(
    GATE_AGENT_TOKEN='agent-secret',
    GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    GATE_CAMERA_ALLOWED_CIDRS=PRIVATE,
    GATE_MODE='shadow',
    GATE_AUTO_CLOSE='controller',
)


def _user(username, group=None):
    user = User.objects.create_user(username, password='pw-123456')
    if group:
        user.groups.add(Group.objects.get(name=group))
    return user


class ApiTestBase(TestCase):
    def setUp(self):
        self.admin = _user('adm', 'gate_admin')
        self.operator = _user('op', 'gate_operator')
        self.client = Client()

    def as_admin(self):
        self.client.force_login(self.admin)

    def as_operator(self):
        self.client.force_login(self.operator)

    def send(self, method, url, body=None, **extra):
        return getattr(self.client, method)(
            url, data=json.dumps(body) if body is not None else None,
            content_type='application/json', **extra,
        )


@override_settings(**SETTINGS)
class CameraApiTest(ApiTestBase):
    def _create(self, **overrides):
        body = {'name': 'Gate cam', 'host': '192.168.1.64', 'username': 'admin',
                'password': 'cam-test-pass', 'vendor': 'hikvision'}
        body.update(overrides)
        return self.send('post', '/api/v1/gate/cameras/', body)

    def test_create_with_defaults_and_preset(self):
        self.as_admin()
        response = self._create()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((data['rtsp_port'], data['http_port']), (554, 80))
        self.assertEqual(data['main_stream_path'], '/Streaming/Channels/101')
        self.assertTrue(data['password_set'])
        self.assertNotIn('password', data)
        self.assertNotIn('cam-test-pass', response.content.decode())
        self.assertEqual(data['updated_by'], 'adm')
        self.assertEqual(Camera.objects.get().get_password(), 'cam-test-pass')

    def test_list_and_detail_never_contain_password(self):
        self.as_admin()
        cam_id = self._create().json()['id']
        for url in ('/api/v1/gate/cameras/', f'/api/v1/gate/cameras/{cam_id}/'):
            body = self.client.get(url).content.decode()
            self.assertNotIn('cam-test-pass', body)
            self.assertNotIn(Camera.objects.get().password_encrypted, body)

    def test_patch_keeps_password_and_bumps_version(self):
        self.as_admin()
        cam_id = self._create().json()['id']
        response = self.send('patch', f'/api/v1/gate/cameras/{cam_id}/', {'host': '192.168.1.65', 'password': ''})
        self.assertEqual(response.status_code, 200, response.content)
        cam = Camera.objects.get()
        self.assertEqual(cam.host, '192.168.1.65')
        self.assertEqual(cam.get_password(), 'cam-test-pass')
        self.assertEqual(cam.config_version, 2)
        self.assertEqual(cam.username, 'admin')

    def test_roi_as_object(self):
        self.as_admin()
        cam_id = self._create().json()['id']
        roi = {'x': 0.1, 'y': 0.4, 'w': 0.6, 'h': 0.5}
        data = self.send('put', f'/api/v1/gate/cameras/{cam_id}/', {'roi': roi}).json()
        self.assertEqual(data['roi'], roi)
        data = self.send('patch', f'/api/v1/gate/cameras/{cam_id}/', {'roi': None}).json()
        self.assertIsNone(data['roi'])

    def test_invalid_roi(self):
        self.as_admin()
        cam_id = self._create().json()['id']
        response = self.send('patch', f'/api/v1/gate/cameras/{cam_id}/', {'roi': {'x': 0.8, 'y': 0, 'w': 0.5, 'h': 0.5}})
        self.assertEqual(response.status_code, 400)

    def test_host_outside_allowlist(self):
        self.as_admin()
        response = self._create(host='172.87.80.80')
        self.assertEqual(response.status_code, 400)
        self.assertIn('GATE_CAMERA_ALLOWED_CIDRS', response.json()['errors']['host'][0])

    def test_validation_errors(self):
        self.as_admin()
        response = self._create(host='http://x', rtsp_port=0)
        errors = response.json()['errors']
        self.assertIn('host', errors)
        self.assertIn('rtsp_port', errors)

    def test_invalid_json(self):
        self.as_admin()
        response = self.client.post('/api/v1/gate/cameras/', data='{bad', content_type='application/json')
        self.assertEqual(response.json()['error_code'], 'INVALID_JSON')
        response = self.client.post('/api/v1/gate/cameras/', data='[1]', content_type='application/json')
        self.assertEqual(response.json()['error_code'], 'INVALID_JSON')

    def test_delete_audited(self):
        self.as_admin()
        cam_id = self._create().json()['id']
        self.assertEqual(self.client.delete(f'/api/v1/gate/cameras/{cam_id}/').status_code, 200)
        self.assertFalse(Camera.objects.exists())
        self.assertEqual(GateConfigChange.objects.first().action, 'delete')

    def test_not_found(self):
        self.as_admin()
        self.assertEqual(self.client.get('/api/v1/gate/cameras/999/').status_code, 404)

    def test_presets(self):
        self.as_admin()
        presets = self.client.get('/api/v1/gate/cameras/presets/').json()['presets']
        self.assertEqual(presets['dahua']['snapshot_path'], '/cgi-bin/snapshot.cgi')

    def test_operator_and_anonymous_forbidden(self):
        for login in (None, self.as_operator):
            self.client.logout()
            if login:
                login()
            self.assertEqual(self.client.get('/api/v1/gate/cameras/').status_code, 403)
            self.assertEqual(self._create().status_code, 403)
            self.assertEqual(self.send('post', '/api/v1/gate/cameras/test/', {}).status_code, 403)
            self.assertEqual(self.client.get('/api/v1/gate/config-changes/').status_code, 403)
            self.assertEqual(self.client.get('/api/v1/gate/devices/').status_code, 403)

    def test_config_changes_listing(self):
        self.as_admin()
        cam_id = self._create().json()['id']
        self.send('patch', f'/api/v1/gate/cameras/{cam_id}/', {'password': 'new-secret'})
        data = self.client.get(f'/api/v1/gate/config-changes/?object_type=camera&object_id={cam_id}').json()
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['results'][0]['changes'], {'password': 'changed'})
        self.assertEqual(data['results'][0]['user'], 'adm')
        self.assertNotIn('new-secret', json.dumps(data))
        self.assertEqual(self.client.get('/api/v1/gate/config-changes/?page=x').status_code, 400)


def _result(ok=True, image=b'\xff\xd8img'):
    result = ConnectionTestResult()
    result.steps = [TestStep('host', True, 'ok'), TestStep('rtsp_port', True, 'ok'),
                    TestStep('snapshot', ok, 'ok' if ok else 'Authentication failed')]
    result.image = image if ok else None
    return result


@override_settings(**SETTINGS)
class CameraTestEndpointTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.as_admin()
        self.cam = Camera(name='Gate', host='192.168.1.64', username='admin', vendor='hikvision')
        self.cam.set_password('stored-pw')
        self.cam.save()

    def _test(self, body):
        with mock.patch('lpr_app.views.gate_admin_views.camera_service.test_connection',
                        return_value=_result()) as tc:
            response = self.send('post', '/api/v1/gate/cameras/test/', body)
        return response, tc

    def test_saved_camera_uses_stored_password_and_records(self):
        response, tc = self._test({'camera_id': self.cam.id})
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['image'].startswith('data:image/jpeg;base64,'))
        self.assertTrue(data['recorded'])
        self.assertEqual(tc.call_args.args[1], 'stored-pw')
        self.cam.refresh_from_db()
        self.assertTrue(self.cam.last_test_ok)
        self.assertIsNotNone(self.cam.last_test_at)

    def test_unsaved_values_over_saved_camera_not_recorded(self):
        response, tc = self._test({'camera_id': self.cam.id, 'host': '192.168.1.99'})
        self.assertFalse(response.json()['recorded'])
        self.assertEqual(tc.call_args.args[0].host, '192.168.1.99')
        self.assertEqual(tc.call_args.args[1], 'stored-pw')
        self.cam.refresh_from_db()
        self.assertEqual(self.cam.host, '192.168.1.64')
        self.assertIsNone(self.cam.last_test_at)

    def test_new_values_with_password(self):
        response, tc = self._test({'name': 'New', 'host': '192.168.1.70', 'vendor': 'dahua', 'password': 'typed'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tc.call_args.args[1], 'typed')
        self.assertEqual(tc.call_args.args[0].snapshot_path, '/cgi-bin/snapshot.cgi')
        self.assertEqual(Camera.objects.count(), 1)

    def test_failed_result(self):
        with mock.patch('lpr_app.views.gate_admin_views.camera_service.test_connection',
                        return_value=_result(ok=False)):
            data = self.send('post', '/api/v1/gate/cameras/test/', {'camera_id': self.cam.id}).json()
        self.assertFalse(data['ok'])
        self.assertIsNone(data['image'])
        self.cam.refresh_from_db()
        self.assertFalse(self.cam.last_test_ok)
        self.assertIn('Authentication failed', self.cam.last_test_error)

    def test_host_outside_allowlist_rejected_without_connecting(self):
        response, tc = self._test({'name': 'X', 'host': '172.87.80.80'})
        self.assertEqual(response.status_code, 400)
        tc.assert_not_called()

    def test_unknown_camera(self):
        response, _ = self._test({'camera_id': 999})
        self.assertEqual(response.status_code, 404)

    def test_stored_password_unreadable(self):
        with override_settings(GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            response, tc = self._test({'camera_id': self.cam.id})
        self.assertEqual(response.json()['error_code'], 'PASSWORD_UNAVAILABLE')
        tc.assert_not_called()

    def test_requires_csrf_for_session_clients(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post('/api/v1/gate/cameras/test/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)


@override_settings(**SETTINGS)
class GateDeviceApiTest(ApiTestBase):
    def test_crud(self):
        self.as_admin()
        cam = Camera.objects.create(name='Cam', host='192.168.1.64')
        response = self.send('post', '/api/v1/gate/devices/', {
            'name': 'Main', 'camera': cam.id, 'controller_url': 'http://192.168.1.50',
            'controller_token': 'tok',
        })
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertTrue(data['controller_token_set'])
        self.assertTrue(data['is_enabled'])
        self.assertEqual(data['camera']['id'], cam.id)
        self.assertNotIn('tok', json.dumps({k: v for k, v in data.items() if k != 'controller_token_set'}))
        gate_id = data['id']

        data = self.send('patch', f'/api/v1/gate/devices/{gate_id}/', {'has_safety_input': True}).json()
        self.assertTrue(data['has_safety_input'])
        self.assertEqual(GateDevice.objects.get().get_controller_token(), 'tok')
        self.assertEqual(self.client.get('/api/v1/gate/devices/').json()['results'][0]['name'], 'Main')
        self.assertEqual(self.client.get(f'/api/v1/gate/devices/{gate_id}/').status_code, 200)
        self.assertEqual(self.send('patch', f'/api/v1/gate/devices/{gate_id}/', {'direction': 'sideways'}).status_code, 400)
        self.assertEqual(self.client.delete(f'/api/v1/gate/devices/{gate_id}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/gate/devices/{gate_id}/').status_code, 404)

    def test_duplicate_name(self):
        self.as_admin()
        GateDevice.objects.create(name='Main')
        self.assertEqual(self.send('post', '/api/v1/gate/devices/', {'name': 'Main'}).status_code, 400)


@override_settings(**SETTINGS)
class VehicleApiTest(ApiTestBase):
    def test_operator_crud(self):
        self.as_operator()
        response = self.send('post', '/api/v1/vehicles/', {
            'plate_display': '30A-123.45', 'owner_name': 'Nguyen Van A', 'department': 'IT',
        })
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['plate_normalized'], '30A12345')
        self.assertTrue(data['is_active'])
        self.assertTrue(data['allowed_now'])
        vid = data['id']

        dup = self.send('post', '/api/v1/vehicles/', {'plate_display': '30a 12345', 'owner_name': 'B'})
        self.assertEqual(dup.status_code, 400)
        self.assertIn('plate_display', dup.json()['errors'])

        until = (timezone.now() - timedelta(days=1)).isoformat()
        data = self.send('patch', f'/api/v1/vehicles/{vid}/', {'valid_until': until}).json()
        self.assertEqual(data['access_status'], 'expired')
        self.assertEqual(data['owner_name'], 'Nguyen Van A')

        data = self.client.delete(f'/api/v1/vehicles/{vid}/').json()
        self.assertFalse(data['is_active'])
        self.assertTrue(Vehicle.objects.filter(pk=vid).exists())
        self.assertEqual(self.client.get(f'/api/v1/vehicles/{vid}/').json()['access_status'], 'inactive')
        self.assertEqual(self.client.get('/api/v1/vehicles/999/').status_code, 404)

    def test_search_and_filter(self):
        self.as_operator()
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='Tran Thi B', department='HR')
        Vehicle.objects.create(plate_display='29X1-234.56', owner_name='Le C', is_active=False)
        def count(qs):
            return self.client.get(f'/api/v1/vehicles/{qs}').json()['count']
        self.assertEqual(count(''), 2)
        self.assertEqual(count('?q=30A-123'), 1)
        self.assertEqual(count('?q=3OA-123.45'), 1)
        self.assertEqual(count('?q=tran'), 1)
        self.assertEqual(count('?q=HR'), 1)
        self.assertEqual(count('?is_active=false'), 1)
        self.assertEqual(self.client.get('/api/v1/vehicles/?page_size=x').status_code, 400)

    def test_plate_preview(self):
        self.as_operator()
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')
        data = self.client.get('/api/v1/vehicles/plate-preview/?plate=3OA-I23.45').json()
        self.assertEqual(data['normalized'], '30A12345')
        self.assertEqual(data['existing_vehicle']['plate_display'], '30A-123.45')

    def test_anonymous_forbidden(self):
        self.assertEqual(self.client.get('/api/v1/vehicles/').status_code, 403)
        self.assertEqual(self.send('post', '/api/v1/vehicles/', {}).status_code, 403)

    def test_admin_can_manage_vehicles(self):
        self.as_admin()
        self.assertEqual(self.client.get('/api/v1/vehicles/').status_code, 200)


@override_settings(**SETTINGS)
class AccessEventApiTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.gate = GateDevice.objects.create(name='Main')
        self.car = Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')
        AccessEvent.objects.create(gate=self.gate, plate_normalized='30A12345', decision='granted',
                                   reason='whitelist_hit', vehicle=self.car)
        AccessEvent.objects.create(gate=self.gate, plate_normalized='51F99999', decision='denied',
                                   reason='not_registered', near_miss_vehicle=self.car)

    def test_list_and_filters(self):
        self.as_operator()
        def count(qs):
            return self.client.get(f'/api/v1/access-events/{qs}').json()['count']
        self.assertEqual(count(''), 2)
        self.assertEqual(count('?decision=denied'), 1)
        self.assertEqual(count('?reason=whitelist_hit'), 1)
        self.assertEqual(count(f'?gate={self.gate.id}'), 2)
        self.assertEqual(count('?plate=51f-999'), 1)
        today = timezone.now().date().isoformat()
        self.assertEqual(count(f'?date_from={today}&date_to={today}'), 2)
        self.assertEqual(self.client.get('/api/v1/access-events/?date_from=yesterday').status_code, 400)
        first = self.client.get('/api/v1/access-events/').json()['results'][0]
        self.assertEqual(first['decision'], 'denied')
        self.assertEqual(first['near_miss_vehicle']['id'], self.car.id)

    def test_detail_and_read_only(self):
        self.as_operator()
        event = AccessEvent.objects.first()
        self.assertEqual(self.client.get(f'/api/v1/access-events/{event.id}/').json()['id'], event.id)
        self.assertEqual(self.client.get('/api/v1/access-events/999/').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/v1/access-events/{event.id}/').status_code, 405)
        self.assertEqual(self.send('post', '/api/v1/access-events/', {}).status_code, 405)

    def test_event_image_requires_operator(self):
        with override_settings(MEDIA_ROOT=self.media):
            image = UploadedImage.objects.create(
                original_image=SimpleUploadedFile('f.jpg', b'\xff\xd8data', content_type='image/jpeg'),
                source='gate',
            )
            event = AccessEvent.objects.create(gate=self.gate, decision='denied', reason='no_plate', uploaded_image=image)
            url = f'/api/v1/gate/events/{event.id}/image/original/'
            self.assertEqual(self.client.get(url).status_code, 403)
            self.as_operator()
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'\xff\xd8data')
            self.assertEqual(self.client.get(f'/api/v1/gate/events/{event.id}/image/processed/').status_code, 404)
            self.assertEqual(self.client.get(f'/api/v1/gate/events/{event.id}/image/other/').status_code, 404)
            self.assertEqual(self.client.get('/api/v1/gate/events/999/image/original/').status_code, 404)
            detail = self.client.get(f'/api/v1/access-events/{event.id}/').json()
            self.assertTrue(detail['has_image'])


@override_settings(**SETTINGS)
class OverrideAndCommandQueueTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.gate = GateDevice.objects.create(name='Main')

    def _override(self, command, gate_id=None):
        return self.send('post', '/api/v1/gate/override/', {'gate_id': gate_id or self.gate.id, 'command': command})

    def test_shadow_mode_override_recorded_not_dispatched(self):
        self.as_operator()
        data = self._override('open').json()
        self.assertEqual(data['event']['decision'], 'manual')
        self.assertEqual(data['event']['operator'], 'op')
        self.assertEqual(data['event']['command_result'], 'not_sent_shadow_mode')
        commands = self.client.get('/api/v1/gate/agent-commands/', **AGENT).json()['commands']
        self.assertEqual(commands, [])

    @override_settings(GATE_MODE='live')
    def test_live_override_dispatched_once_stop_first(self):
        self.as_operator()
        open_id = self._override('open').json()['event']['id']
        stop_id = self._override('stop').json()['event']['id']
        commands = self.client.get('/api/v1/gate/agent-commands/', **AGENT).json()['commands']
        self.assertEqual([c['event_id'] for c in commands], [stop_id, open_id])
        self.assertEqual(self.client.get('/api/v1/gate/agent-commands/', **AGENT).json()['commands'], [])

        response = self.send('post', f'/api/v1/gate/events/{open_id}/command-result/', {'sent': True, 'result': 'ok'}, **AGENT)
        self.assertEqual(response.status_code, 200)
        event = AccessEvent.objects.get(pk=open_id)
        self.assertTrue(event.command_sent)
        self.assertEqual(event.command_result, 'ok')
        self.gate.refresh_from_db()
        self.assertEqual(self.gate.last_command_result, 'open: ok')

    @override_settings(GATE_MODE='live', GATE_COMMAND_TTL_SECONDS=15)
    def test_stale_command_expires(self):
        self.as_operator()
        event_id = self._override('open').json()['event']['id']
        AccessEvent.objects.filter(pk=event_id).update(timestamp=timezone.now() - timedelta(seconds=30))
        self.assertEqual(self.client.get('/api/v1/gate/agent-commands/', **AGENT).json()['commands'], [])
        self.assertEqual(AccessEvent.objects.get(pk=event_id).command_result, 'expired')

    def test_validation(self):
        self.as_operator()
        self.assertEqual(self._override('explode').json()['error_code'], 'INVALID_COMMAND')
        self.assertEqual(self._override('open', gate_id=999).status_code, 404)
        with self.assertRaises(ValueError):
            gate_service.create_override(self.gate, 'explode', self.operator)

    def test_override_requires_operator_and_csrf(self):
        self.assertEqual(self._override('open').status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        response = client.post('/api/v1/gate/override/', data=json.dumps({'gate_id': self.gate.id, 'command': 'open'}),
                               content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AccessEvent.objects.count(), 0)

    def test_command_result_errors(self):
        event = AccessEvent.objects.create(gate=self.gate, decision='denied', reason='no_plate')
        response = self.send('post', f'/api/v1/gate/events/{event.id}/command-result/', {'sent': True}, **AGENT)
        self.assertEqual(response.status_code, 404)
        response = self.client.post(f'/api/v1/gate/events/{event.id}/command-result/', data='x',
                                    content_type='application/json', **AGENT)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.send('post', f'/api/v1/gate/events/{event.id}/command-result/', {}).status_code, 403)

    def test_status(self):
        self.as_operator()
        cam = Camera.objects.create(name='Cam', host='192.168.1.64', agent_status='streaming')
        self.gate.camera = cam
        self.gate.last_seen = timezone.now()
        self.gate.save()
        AccessEvent.objects.create(gate=self.gate, decision='denied', reason='no_plate')
        data = self.client.get('/api/v1/gate/status/').json()
        self.assertEqual(data['mode'], 'shadow')
        gate = data['gates'][0]
        self.assertTrue(gate['online'])
        self.assertEqual(gate['camera_status'], 'streaming')
        self.assertEqual(gate['last_event']['reason'], 'no_plate')
        self.client.logout()
        self.assertEqual(self.client.get('/api/v1/gate/status/').status_code, 403)


@override_settings(**SETTINGS)
class AgentConfigTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.cam = Camera(name='Cam', host='192.168.1.64', username='admin', vendor='hikvision',
                          roi_x=0.1, roi_y=0.2, roi_w=0.5, roi_h=0.5)
        self.cam.set_password('cam-test-pass')
        self.cam.save()
        self.gate = GateDevice(name='Main', camera=self.cam, controller_url='http://192.168.1.50')
        self.gate.set_controller_token('dev-tok')
        self.gate.save()

    def test_config_contains_credentials_for_agent_only(self):
        response = self.client.get('/api/v1/gate/agent-config/', **AGENT)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        gate = data['gates'][0]
        self.assertEqual(gate['controller_token'], 'dev-tok')
        self.assertEqual(gate['camera']['password'], 'cam-test-pass')
        self.assertEqual(gate['camera']['main_stream_path'], '/Streaming/Channels/101')
        self.assertEqual(gate['camera']['roi'], {'x': 0.1, 'y': 0.2, 'w': 0.5, 'h': 0.5})
        self.assertEqual(data['mode'], 'shadow')
        self.assertEqual(response['ETag'], f'"{data["version"]}"')

        self.as_admin()
        self.assertEqual(self.client.get('/api/v1/gate/agent-config/').status_code, 403)

    def test_304_when_unchanged_and_new_version_after_change(self):
        version = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()['version']
        response = self.client.get(f'/api/v1/gate/agent-config/?version={version}', **AGENT)
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.content, b'')
        response = self.client.get('/api/v1/gate/agent-config/', HTTP_IF_NONE_MATCH=f'"{version}"', **AGENT)
        self.assertEqual(response.status_code, 304)

        self.as_admin()
        self.send('patch', f'/api/v1/gate/cameras/{self.cam.id}/', {'host': '192.168.1.65'})
        response = self.client.get(f'/api/v1/gate/agent-config/?version={version}', **AGENT)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['version'], version)
        self.assertEqual(response.json()['gates'][0]['camera']['host'], '192.168.1.65')

    def test_disabled_gate_and_camera_omitted(self):
        self.cam.is_enabled = False
        self.cam.save()
        gate = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()['gates'][0]
        self.assertIsNone(gate['camera'])
        GateDevice.objects.update(is_enabled=False)
        self.assertEqual(self.client.get('/api/v1/gate/agent-config/', **AGENT).json()['gates'], [])

    def test_undecryptable_secrets_reported(self):
        with override_settings(GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            gate = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()['gates'][0]
        self.assertIsNone(gate['controller_token'])
        self.assertEqual(gate['controller_token_error'], 'token_unavailable')
        self.assertEqual(gate['camera']['credential_error'], 'password_unavailable')

    @override_settings(GATE_AUTO_CLOSE='software')
    def test_software_close_refused_without_safety_input(self):
        gate = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()['gates'][0]
        self.assertEqual(gate['auto_close'], 'controller')
        self.assertTrue(gate['config_errors'])
        GateDevice.objects.update(has_safety_input=True)
        gate = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()['gates'][0]
        self.assertEqual(gate['auto_close'], 'software')
        self.assertEqual(gate['config_errors'], [])

    def test_agent_status(self):
        response = self.send('post', '/api/v1/gate/agent-status/',
                             {'cameras': [{'id': self.cam.id, 'status': 'auth_failed'}]}, **AGENT)
        self.assertEqual(response.json()['updated'], 1)
        self.cam.refresh_from_db()
        self.assertEqual(self.cam.agent_status, 'auth_failed')
        bad = self.send('post', '/api/v1/gate/agent-status/', {'cameras': [{'id': self.cam.id, 'status': 'melted'}]}, **AGENT)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.send('post', '/api/v1/gate/agent-status/', {'cameras': []}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/gate/agent-status/', data='{', content_type='application/json', **AGENT).status_code, 400)


@override_settings(**SETTINGS)
class HeartbeatTest(TestCase):
    def setUp(self):
        self.gate = GateDevice(name='Main')
        self.gate.set_controller_token('dev-tok')
        self.gate.save()

    def _beat(self, token='dev-tok', **body):
        payload = {'gate_id': self.gate.id, 'firmware_version': '1.0.0', 'uptime_s': 42}
        payload.update(body)
        return Client(enforce_csrf_checks=True).post(
            '/api/v1/gate/heartbeat/', data=json.dumps(payload), content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )

    def test_valid_heartbeat(self):
        response = self._beat(last_command_result='open: ok')
        self.assertEqual(response.status_code, 200)
        self.gate.refresh_from_db()
        self.assertTrue(self.gate.is_online())
        self.assertEqual(self.gate.firmware_version, '1.0.0')
        self.assertEqual(self.gate.last_command_result, 'open: ok')

    def test_wrong_token_and_unknown_gate_look_the_same(self):
        wrong = self._beat(token='nope')
        unknown = self._beat(gate_id=999)
        self.assertEqual((wrong.status_code, unknown.status_code), (403, 403))
        self.assertEqual(wrong.json(), unknown.json())
        self.gate.refresh_from_db()
        self.assertIsNone(self.gate.last_seen)

    def test_agent_token_is_not_a_device_token(self):
        self.assertEqual(self._beat(token='agent-secret').status_code, 403)

    def test_bad_body(self):
        response = Client().post('/api/v1/gate/heartbeat/', data='nope', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._beat(gate_id='abc').status_code, 403)
