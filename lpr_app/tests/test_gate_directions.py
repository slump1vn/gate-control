"""A gate watches both directions: one camera for vehicles arriving, one for those leaving."""

from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from ..models import AccessEvent, Camera, GateCamera, GateDevice, Vehicle
from ..services import gate_service
from ..utils import secrets as gate_secrets
from .test_gate_decision import MediaDirMixin, SyncExecutor, det, fake_pipeline, jpeg

SETTINGS = {
    'GATE_CONFIG_ENCRYPTION_KEY': gate_secrets.generate_encryption_key(),
    'GATE_CAMERA_ALLOWED_CIDRS': ['192.168.0.0/16'],
    'GATE_MODE': 'live',
    'GATE_CONSENSUS_MIN': 1,
    'GATE_BURST_FRAMES': 1,
    'GATE_MIN_CONFIDENCE': 0.8,
}


def camera(name):
    return Camera.objects.create(name=name, host='192.168.1.64', snapshot_path='/snap.jpg')


@override_settings(**SETTINGS)
class GateCameraLinkTest(TestCase):
    def setUp(self):
        self.gate = GateDevice.objects.create(name='Main', controller_type='simulator')

    def test_a_gate_without_two_cameras_is_flagged(self):
        self.assertIn('0 of 2 cameras', self.gate.camera_warning())
        GateCamera.objects.create(gate=self.gate, camera=camera('Entry'), direction='in')
        self.assertIn('1 of 2 cameras', self.gate.camera_warning())
        self.assertFalse(self.gate.watches_both_directions)

    def test_two_cameras_in_the_same_direction_are_flagged(self):
        GateCamera.objects.create(gate=self.gate, camera=camera('Entry'), direction='in')
        GateCamera.objects.create(gate=self.gate, camera=camera('Entry wide'), direction='in')
        self.assertIn('watching the exit', self.gate.camera_warning())

    def test_one_camera_each_way_is_complete(self):
        GateCamera.objects.create(gate=self.gate, camera=camera('Entry'), direction='in')
        GateCamera.objects.create(gate=self.gate, camera=camera('Exit'), direction='out')
        self.assertEqual(self.gate.camera_warning(), '')
        self.assertTrue(self.gate.watches_both_directions)

    def test_a_third_camera_is_allowed(self):
        GateCamera.objects.create(gate=self.gate, camera=camera('Entry'), direction='in')
        GateCamera.objects.create(gate=self.gate, camera=camera('Exit'), direction='out')
        GateCamera.objects.create(gate=self.gate, camera=camera('Motorbike lane'), direction='in')
        self.assertEqual(self.gate.camera_warning(), '')
        self.assertEqual(self.gate.cameras.count(), 3)


@override_settings(**SETTINGS)
class DecisionDirectionTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gate_service, '_executor', SyncExecutor())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gate = GateDevice.objects.create(name='Main', controller_type='simulator')
        self.entry = camera('Entry')
        self.exit = camera('Exit')
        GateCamera.objects.create(gate=self.gate, camera=self.entry, direction='in')
        GateCamera.objects.create(gate=self.gate, camera=self.exit, direction='out')
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')

    def decide(self, camera_obj, plate):
        with fake_pipeline([[det(plate)]]):
            return gate_service.decide(
                self.gate.id, [jpeg()], camera_id=camera_obj.id if camera_obj else None,
            )

    def test_the_event_records_the_camera_and_the_direction(self):
        event = self.decide(self.entry, '30A-123.45').event
        self.assertEqual(event.camera_id, self.entry.id)
        self.assertEqual(event.direction, 'in')
        self.assertEqual(event.decision, 'granted')

    def test_an_unknown_camera_leaves_the_direction_empty(self):
        event = self.decide(None, '30A-123.45').event
        self.assertEqual(event.direction, '')
        self.assertIsNone(event.camera_id)

    def test_leaving_follows_the_registry_by_default(self):
        self.assertEqual(self.gate.exit_policy, 'registered')
        event = self.decide(self.exit, '51G-888.88').event
        self.assertEqual(event.decision, 'denied')
        self.assertEqual(event.reason, 'not_registered')
        self.assertEqual(event.direction, 'out')

    def test_an_open_exit_lets_an_unregistered_vehicle_out_and_still_logs_the_plate(self):
        GateDevice.objects.filter(pk=self.gate.pk).update(exit_policy='any')
        decision = self.decide(self.exit, '51G-888.88')
        event = decision.event
        self.assertEqual(event.decision, 'granted')
        self.assertEqual(event.reason, 'exit_free')
        self.assertEqual(event.plate_normalized, '51G88888')
        self.assertEqual(event.command, 'open')
        self.assertTrue(decision.actuate)

    def test_an_open_exit_still_credits_a_registered_vehicle(self):
        GateDevice.objects.filter(pk=self.gate.pk).update(exit_policy='any')
        event = self.decide(self.exit, '30A-123.45').event
        self.assertEqual(event.reason, 'whitelist_hit')
        self.assertIsNotNone(event.vehicle)

    def test_an_open_exit_does_not_change_the_entry(self):
        GateDevice.objects.filter(pk=self.gate.pk).update(exit_policy='any')
        event = self.decide(self.entry, '51G-888.88').event
        self.assertEqual(event.decision, 'denied')
        self.assertEqual(event.reason, 'not_registered')


@override_settings(**SETTINGS)
class DirectionInTheEventLogTest(TestCase):
    def setUp(self):
        self.gate = GateDevice.objects.create(name='Main', controller_type='simulator')
        self.entry = camera('Entry')
        self.exit = camera('Exit')
        GateCamera.objects.create(gate=self.gate, camera=self.entry, direction='in')
        GateCamera.objects.create(gate=self.gate, camera=self.exit, direction='out')
        AccessEvent.objects.create(gate=self.gate, camera=self.entry, direction='in',
                                   decision='granted', reason='whitelist_hit')
        AccessEvent.objects.create(gate=self.gate, camera=self.exit, direction='out',
                                   decision='granted', reason='exit_free')

        operator = User.objects.create_user('guard', password='pw')
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(operator)

    def test_events_carry_the_camera_and_direction(self):
        results = self.client.get('/api/v1/access-events/').json()['results']
        self.assertEqual({e['direction'] for e in results}, {'in', 'out'})
        self.assertEqual({e['camera']['name'] for e in results}, {'Entry', 'Exit'})

    def test_events_can_be_filtered_by_direction(self):
        results = self.client.get('/api/v1/access-events/?direction=out').json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['reason'], 'exit_free')
