"""Telegram messages for plates on the alert list."""

import json
from unittest import mock

import requests
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from lpr_app.models import AccessEvent, AlertDelivery, Camera, GateCamera, GateConfigChange, GateDevice, PlateAlert
from lpr_app.services import gate_service, plate_alerts

from .test_gate_decision import AGENT, GATE_SETTINGS, MediaDirMixin, SyncExecutor, det, fake_pipeline, jpeg

TELEGRAM = dict(TELEGRAM_BOT_TOKEN='123:secret-token', TELEGRAM_CHAT_IDS='-1001,555',
                TELEGRAM_API_URL='https://telegram.test', TELEGRAM_ALERT_COOLDOWN_SECONDS=300,
                TELEGRAM_SEND_PHOTO=True)


def ok(*args, **kwargs):
    response = mock.Mock(status_code=200)
    response.json.return_value = {'ok': True}
    return response


def refused(description='Bad Request: chat not found', status=400):
    response = mock.Mock(status_code=status)
    response.json.return_value = {'ok': False, 'description': description}
    return response


class PlateAlertModelTest(TestCase):
    def test_plate_is_normalised_and_unique(self):
        alert = PlateAlert.objects.create(plate_display='30a-123.45')
        self.assertEqual(alert.plate_normalized, '30A12345')
        with self.assertRaises(ValidationError):
            PlateAlert(plate_display='30A 12345').full_clean()

    def test_chat_ids_are_checked(self):
        PlateAlert(plate_display='51F11111', chat_ids='-1001234, @gate_team').full_clean()
        with self.assertRaises(ValidationError):
            PlateAlert(plate_display='51F22222', chat_ids='not a chat').full_clean()
        self.assertEqual(PlateAlert(chat_ids=' 1, ,@abcde ').chat_id_list(), ['1', '@abcde'])


@override_settings(**GATE_SETTINGS, **TELEGRAM)
class PlateAlertDecisionTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        for target, attr in ((gate_service, '_executor'), (plate_alerts, '_pool')):
            patcher = mock.patch.object(target, attr, SyncExecutor())
            patcher.start()
            self.addCleanup(patcher.stop)
        self.post = mock.patch.object(plate_alerts.requests, 'post', side_effect=ok).start()
        self.addCleanup(mock.patch.stopall)
        self.gate = GateDevice.objects.create(name='West')
        self.entry = Camera.objects.create(name='Entry', host='10.0.0.5')
        self.exit = Camera.objects.create(name='Exit', host='10.0.0.6')
        GateCamera.objects.create(gate=self.gate, camera=self.entry, direction='in')
        GateCamera.objects.create(gate=self.gate, camera=self.exit, direction='out')
        self.alert = PlateAlert.objects.create(plate_display='30A-123.45', label='Giám đốc')

    def decide(self, camera, plates):
        data = {'gate_id': self.gate.id, 'camera_id': camera.id,
                'frames': [jpeg(f'f{i}.jpg') for i in range(len(plates))]}
        with self.captureOnCommitCallbacks(execute=True):
            with fake_pipeline([[det(p)] for p in plates]):
                return self.client.post('/api/v1/gate/decide/', data, **AGENT).json()

    def test_a_listed_plate_is_announced_to_every_recipient(self):
        data = self.decide(self.entry, ['30A12345', '30A12345'])
        deliveries = AlertDelivery.objects.filter(event_id=data['event_id'])
        self.assertEqual(sorted(d.chat_id for d in deliveries), ['-1001', '555'])
        self.assertTrue(all(d.status == 'sent' for d in deliveries))
        self.assertEqual(self.post.call_count, 2)
        url = self.post.call_args.args[0]
        self.assertEqual(url, 'https://telegram.test/bot123:secret-token/sendPhoto')
        caption = self.post.call_args.kwargs['data']['caption']
        self.assertIn('30A12345', caption)
        self.assertIn('Giám đốc', caption)
        self.assertIn('West', caption)
        self.assertIn('chiều vào', caption)
        self.alert.refresh_from_db()
        self.assertIsNotNone(self.alert.last_notified_at)

    def test_other_plates_tests_and_disabled_alerts_are_silent(self):
        self.decide(self.entry, ['51F99999', '51F99999'])
        self.alert.is_active = False
        self.alert.save()
        self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertFalse(AlertDelivery.objects.exists())
        self.post.assert_not_called()

    def test_direction_filter(self):
        self.alert.directions = 'out'
        self.alert.save()
        self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertFalse(AlertDelivery.objects.exists())
        self.decide(self.exit, ['30A12345', '30A12345'])
        self.assertEqual(AlertDelivery.objects.count(), 2)

    def test_the_same_vehicle_is_announced_once_per_cooldown(self):
        self.decide(self.entry, ['30A12345', '30A12345'])
        self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertEqual(AlertDelivery.objects.count(), 2)
        with override_settings(TELEGRAM_ALERT_COOLDOWN_SECONDS=0):
            self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertEqual(AlertDelivery.objects.count(), 4)

    def test_alert_recipients_override_the_default(self):
        self.alert.chat_ids = '777'
        self.alert.save()
        self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertEqual(list(AlertDelivery.objects.values_list('chat_id', flat=True)), ['777'])

    def test_a_refused_photo_falls_back_to_text(self):
        self.post.side_effect = [refused('Bad Request: wrong file'), ok(), ok(), ok()]
        self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertEqual(set(AlertDelivery.objects.values_list('status', flat=True)), {'sent'})
        self.assertIn('/sendMessage', self.post.call_args_list[1].args[0])

    def test_failures_are_recorded_without_the_token(self):
        self.post.side_effect = requests.ConnectionError('https://telegram.test/bot123:secret-token/sendPhoto')
        data = self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertEqual(data['decision'], 'denied')  # not registered: the alert does not change the decision
        for delivery in AlertDelivery.objects.all():
            self.assertEqual(delivery.status, 'failed')
            self.assertNotIn('secret-token', delivery.error)

    def test_nothing_is_sent_without_a_bot_token(self):
        with override_settings(TELEGRAM_BOT_TOKEN=''):
            self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertFalse(AlertDelivery.objects.exists())

    def test_a_broken_alert_lookup_never_breaks_the_decision(self):
        with mock.patch.object(plate_alerts, 'matching_alert', side_effect=RuntimeError('boom')):
            data = self.decide(self.entry, ['30A12345', '30A12345'])
        self.assertEqual(data['plate'], '30A12345')
        self.assertTrue(AccessEvent.objects.filter(pk=data['event_id']).exists())

    def test_message_text_escapes_html_and_notes_shadow_mode(self):
        self.alert.label = '<b>VIP</b>'
        event = AccessEvent(gate=self.gate, direction='out', plate_raw='30A-123.45', plate_normalized='30A12345',
                            decision='granted', reason='whitelist_hit', mode='shadow')
        text = plate_alerts.message_text(event, self.alert)
        self.assertIn('&lt;b&gt;VIP&lt;/b&gt;', text)
        self.assertIn('chế độ thử', text)
        self.assertIn('chiều ra', text)


@override_settings(**TELEGRAM)
class PlateAlertApiTest(TestCase):
    def setUp(self):
        admin = User.objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get(name='gate_admin'))
        self.client.force_login(admin)

    def api(self, method, path, body=None):
        url = f'/api/v1/gate/plate-alerts/{path}'
        if method == 'get':
            return self.client.get(url)
        return getattr(self.client, method)(url, data=json.dumps(body or {}), content_type='application/json')

    def test_crud_with_audit(self):
        created = self.api('post', '', {'plate_display': '30A-123.45', 'label': 'Giám đốc', 'directions': 'in',
                                        'chat_ids': '', 'is_active': True})
        self.assertEqual(created.status_code, 201, created.content)
        alert_id = created.json()['id']
        listing = self.api('get', '').json()
        self.assertEqual(len(listing['results']), 1)
        self.assertEqual(listing['telegram'], {'configured': True, 'default_recipients': 2, 'cooldown_seconds': 300})
        duplicate = self.api('post', '', {'plate_display': '30A12345', 'directions': 'both', 'is_active': True})
        self.assertEqual(duplicate.status_code, 400)
        patched = self.api('patch', f'{alert_id}/', {'chat_ids': '777', 'is_active': False})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()['chat_ids'], patched.json()['is_active']), ('777', False))
        self.assertEqual(self.api('delete', f'{alert_id}/').status_code, 200)
        self.assertEqual(self.api('get', f'{alert_id}/').status_code, 404)
        self.assertEqual(GateConfigChange.objects.count(), 3)

    def test_operators_cannot_manage_alerts(self):
        operator = User.objects.create_user('op', password='x')
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(operator)
        self.assertEqual(self.api('get', '').status_code, 403)

    def test_send_test_message(self):
        with mock.patch.object(plate_alerts.requests, 'post', side_effect=[ok(), refused()]) as post:
            data = self.api('post', 'test/').json()
        self.assertEqual(data['results'], [{'chat_id': '-1001', 'ok': True, 'error': ''},
                                           {'chat_id': '555', 'ok': False,
                                            'error': 'HTTP 400 Bad Request: chat not found'}])
        self.assertEqual(post.call_args.kwargs['json']['chat_id'], '555')
        with mock.patch.object(plate_alerts.requests, 'post', side_effect=ok):
            one = self.api('post', 'test/', {'chat_id': '42'}).json()
        self.assertEqual([r['chat_id'] for r in one['results']], ['42'])
        with override_settings(TELEGRAM_BOT_TOKEN=''):
            self.assertEqual(self.api('post', 'test/').status_code, 400)
        with override_settings(TELEGRAM_CHAT_IDS=''):
            self.assertEqual(self.api('post', 'test/').status_code, 400)
