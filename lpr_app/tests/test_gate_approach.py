"""Opening on approach: the gate opens as a vehicle comes toward it, and the plate read after fills in the event."""

import json
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from lpr_app.models import AccessEvent, Camera, GateCamera, GateDevice, Vehicle
from lpr_app.services import gate_service
from lpr_app.utils import time_windows

from .test_gate_decision import AGENT, GATE_SETTINGS, MediaDirMixin, SyncExecutor, det, fake_pipeline, jpeg

HCM = ZoneInfo('Asia/Ho_Chi_Minh')


def at(hour, minute=0):
    return datetime(2026, 10, 1, hour, minute, tzinfo=HCM)


class TimeWindowTest(SimpleTestCase):
    def test_parse_and_contains(self):
        self.assertEqual(time_windows.parse(' 06:30-08:00 , 16:30 - 18:00'), [(390, 480), (990, 1080)])
        self.assertTrue(time_windows.contains('', at(3)))
        self.assertTrue(time_windows.contains('06:30-08:00,16:30-18:00', at(7, 59)))
        self.assertFalse(time_windows.contains('06:30-08:00,16:30-18:00', at(8, 0)))
        self.assertTrue(time_windows.contains('06:30-08:00,16:30-18:00', at(16, 30)))
        self.assertFalse(time_windows.contains('06:30-08:00,16:30-18:00', at(12)))

    def test_window_across_midnight(self):
        self.assertTrue(time_windows.contains('22:00-06:00', at(23, 30)))
        self.assertTrue(time_windows.contains('22:00-06:00', at(5, 59)))
        self.assertFalse(time_windows.contains('22:00-06:00', at(6)))
        self.assertFalse(time_windows.contains('22:00-06:00', at(21, 59)))

    def test_bad_windows_are_refused(self):
        for bad in ('7h-8h', '25:00-26:00', '06:60-07:00', '08:00-08:00', '06:30', '06:30-08:00;09:00-10:00'):
            with self.assertRaises(ValidationError, msg=bad):
                time_windows.validate_time_windows(bad)
        time_windows.validate_time_windows('')
        time_windows.validate_time_windows('6:05-7:00')


@override_settings(**GATE_SETTINGS)
class ApproachTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gate_service, '_executor', SyncExecutor())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gate = GateDevice.objects.create(name='West', approach_open='in')
        self.entry = Camera.objects.create(name='Entry', host='10.0.0.5')
        self.exit = Camera.objects.create(name='Exit', host='10.0.0.6')
        GateCamera.objects.create(gate=self.gate, camera=self.entry, direction='in')
        GateCamera.objects.create(gate=self.gate, camera=self.exit, direction='out')
        self.car = Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')

    def approach(self, camera, headers=AGENT, **body):
        payload = {'gate_id': self.gate.id, 'camera_id': camera.id, **body}
        return self.client.post('/api/v1/gate/approach/', data=json.dumps(payload), content_type='application/json',
                                **headers)

    def decide(self, camera, plates, **extra):
        data = {'gate_id': self.gate.id, 'camera_id': camera.id,
                'frames': [jpeg(f'f{i}.jpg') for i in range(len(plates))], **extra}
        with fake_pipeline([[det(p)] for p in plates]):
            return self.client.post('/api/v1/gate/decide/', data, **AGENT).json()

    def test_active_by_direction(self):
        for setting, entry, exit_ in (('off', False, False), ('in', True, False), ('out', False, True),
                                      ('both', True, True)):
            self.gate.approach_open = setting
            self.assertEqual(gate_service.approach_active(self.gate, 'in'), entry, setting)
            self.assertEqual(gate_service.approach_active(self.gate, 'out'), exit_, setting)
        self.assertFalse(gate_service.approach_active(self.gate, ''))
        self.gate.is_enabled = False
        self.assertFalse(gate_service.approach_active(self.gate, 'in'))

    def test_active_only_within_its_hours(self):
        self.gate.approach_open = 'both'
        self.gate.approach_hours = '06:30-08:00'
        self.assertTrue(gate_service.approach_active(self.gate, 'in', at(7)))
        self.assertFalse(gate_service.approach_active(self.gate, 'in', at(9)))

    def test_opens_before_any_plate_is_read(self):
        data = self.approach(self.entry).json()
        self.assertTrue(data['approach'])
        event = AccessEvent.objects.get(pk=data['event_id'])
        self.assertEqual((event.decision, event.reason, event.direction, event.command),
                         ('granted', 'approach_open', 'in', 'open'))
        self.assertEqual(event.plate_normalized, '')
        # A real controller moves only in live mode
        self.assertFalse(data['actuate'])
        with override_settings(GATE_MODE='live'):
            live = self.approach(self.entry).json()
        self.assertTrue(live['actuate'])
        self.assertEqual(live['command'], 'open')

    def test_not_for_other_directions_or_hours(self):
        self.assertEqual(self.approach(self.exit).json(), {'approach': False})
        self.gate.approach_hours = '06:30-06:31'
        self.gate.save()
        with mock.patch.object(gate_service.timezone, 'now', return_value=at(12)):
            self.assertEqual(self.approach(self.entry).json(), {'approach': False})
        self.assertFalse(AccessEvent.objects.exists())

    def test_the_read_fills_in_the_same_event_and_does_not_open_again(self):
        opened = self.approach(self.entry).json()
        with override_settings(GATE_MODE='live'):
            data = self.decide(self.entry, ['30A12345', '30A12345', ''], approach_event_id=opened['event_id'])
        self.assertEqual(data['event_id'], opened['event_id'])
        self.assertEqual((data['decision'], data['reason'], data['plate']), ('granted', 'approach_open', '30A12345'))
        self.assertFalse(data['actuate'])
        event = AccessEvent.objects.get(pk=opened['event_id'])
        self.assertEqual(event.vehicle, self.car)
        self.assertEqual((event.frames_read, event.frames_agreed), (3, 2))
        self.assertEqual(AccessEvent.objects.count(), 1)

    def test_an_unregistered_vehicle_is_still_logged_on_the_opening(self):
        opened = self.approach(self.entry).json()
        data = self.decide(self.entry, ['51F99999', '51F99999'], approach_event_id=opened['event_id'])
        self.assertEqual((data['decision'], data['reason'], data['plate']), ('granted', 'approach_open', '51F99999'))
        self.assertIsNone(data['vehicle'])

    def test_a_stale_or_foreign_opening_gets_a_decision_of_its_own(self):
        opened = self.approach(self.entry).json()
        AccessEvent.objects.filter(pk=opened['event_id']).update(timestamp=timezone.now() - timedelta(minutes=5))
        data = self.decide(self.entry, ['30A12345', '30A12345'], approach_event_id=opened['event_id'])
        self.assertNotEqual(data['event_id'], opened['event_id'])
        self.assertEqual(data['reason'], 'whitelist_hit')
        # An event that is not an approach opening is never rewritten
        data2 = self.decide(self.entry, ['51F99999', '51F99999'], approach_event_id=data['event_id'])
        self.assertNotEqual(data2['event_id'], data['event_id'])
        self.assertEqual(AccessEvent.objects.get(pk=data['event_id']).plate_normalized, '30A12345')

    def test_needs_the_agent_token_and_a_body(self):
        self.assertEqual(self.approach(self.entry, headers={}).status_code, 403)
        response = self.client.post('/api/v1/gate/approach/', data=json.dumps({'gate_id': 'x'}),
                                    content_type='application/json', **AGENT)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/api/v1/gate/approach/', data='{', content_type='application/json',
                                          **AGENT).status_code, 400)

    def test_agent_config_carries_the_setting(self):
        config = self.client.get('/api/v1/gate/agent-config/', **AGENT).json()
        self.assertEqual(config['gates'][0]['approach_open'], 'in')


@override_settings(**GATE_SETTINGS)
class ApproachSettingsApiTest(TestCase):
    def setUp(self):
        self.gate = GateDevice.objects.create(name='West')
        admin = User.objects.create_user('installer', password='pw')
        admin.groups.add(Group.objects.get(name='gate_admin'))
        self.client.force_login(admin)

    def patch(self, **body):
        return self.client.patch(f'/api/v1/gate/devices/{self.gate.id}/', data=json.dumps(body),
                                 content_type='application/json')

    def test_set_and_audited(self):
        response = self.patch(approach_open='both', approach_hours='06:30-08:00, 16:30-18:00')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['approach_open'], response.json()['approach_hours']),
                         ('both', '06:30-08:00, 16:30-18:00'))
        from lpr_app.models import GateConfigChange
        change = GateConfigChange.objects.filter(object_type='gatedevice', object_id=self.gate.id).first()
        self.assertIn('approach_open', change.changes)

    def test_bad_hours_and_choice_are_refused(self):
        self.assertEqual(self.patch(approach_hours='7h-8h').status_code, 400)
        self.assertEqual(self.patch(approach_open='always').status_code, 400)
        self.gate.refresh_from_db()
        self.assertEqual((self.gate.approach_open, self.gate.approach_hours), ('off', ''))
