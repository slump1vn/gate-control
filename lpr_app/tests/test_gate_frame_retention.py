"""Which frames of a burst survive a decision, and who may see them."""

import os
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from ..models import AccessEvent, UploadedImage, Vehicle
from ..models import GateDevice
from ..services import gate_service
from .test_gate_decision import MediaDirMixin, SyncExecutor, det, fake_pipeline, jpeg

SETTINGS = {
    'GATE_MODE': 'live',
    'GATE_CONSENSUS_MIN': 2,
    'GATE_BURST_FRAMES': 3,
    'GATE_MIN_CONFIDENCE': 0.8,
}


@override_settings(**SETTINGS)
class KeepFramesTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gate_service, '_executor', SyncExecutor())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gate = GateDevice.objects.create(name='Main', controller_type='simulator')
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')

    def decide(self, plate):
        with fake_pipeline([[det(plate)]] * 3):
            return gate_service.decide(self.gate.id, [jpeg(f'f{i}.jpg') for i in range(3)])

    def kept(self, event):
        return UploadedImage.objects.filter(source='gate').count(), event.frames.count()

    def test_by_default_only_the_evidence_frame_survives(self):
        event = self.decide('30A-123.45').event
        self.assertEqual(self.kept(event), (1, 1))
        self.assertEqual(event.frames.first().id, event.uploaded_image_id)

    @override_settings(GATE_KEEP_FRAMES='denied')
    def test_a_refusal_keeps_the_whole_burst(self):
        # The frames that were not chosen are what shows why a plate was misread
        event = self.decide('51G-888.88').event
        self.assertEqual(event.decision, 'denied')
        self.assertEqual(self.kept(event), (3, 3))
        self.assertIn(event.uploaded_image_id, [f.id for f in event.frames.all()])

    @override_settings(GATE_KEEP_FRAMES='denied')
    def test_a_grant_still_keeps_only_the_evidence(self):
        event = self.decide('30A-123.45').event
        self.assertEqual(event.decision, 'granted')
        self.assertEqual(self.kept(event), (1, 1))

    @override_settings(GATE_KEEP_FRAMES='all')
    def test_all_keeps_every_frame_of_every_decision(self):
        event = self.decide('30A-123.45').event
        self.assertEqual(event.decision, 'granted')
        self.assertEqual(self.kept(event), (3, 3))

    @override_settings(GATE_KEEP_FRAMES='denied')
    def test_the_kept_frames_are_not_swept_up_as_orphans(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone

        event = self.decide('51G-888.88').event
        UploadedImage.objects.update(upload_timestamp=timezone.now() - timedelta(hours=3))
        call_command('purge_access_events', verbosity=0)
        self.assertEqual(event.frames.count(), 3)

    @override_settings(GATE_KEEP_FRAMES='denied')
    def test_expiring_an_event_deletes_all_of_its_frames(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone

        event = self.decide('51G-888.88').event
        AccessEvent.objects.update(timestamp=timezone.now() - timedelta(days=200))
        call_command('purge_access_events', verbosity=0)
        self.assertEqual(UploadedImage.objects.filter(source='gate').count(), 0)
        self.assertFalse(AccessEvent.objects.filter(pk=event.pk).exists())


@override_settings(**SETTINGS, GATE_KEEP_FRAMES='denied')
class FrameApiTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gate_service, '_executor', SyncExecutor())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gate = GateDevice.objects.create(name='Main', controller_type='simulator')
        with fake_pipeline([[det('51G-888.88')]] * 3):
            self.event = gate_service.decide(self.gate.id, [jpeg(f'f{i}.jpg') for i in range(3)]).event

        operator = User.objects.create_user('guard', password='pw')
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(operator)

    def test_the_event_lists_its_frames_with_the_evidence_first(self):
        data = self.client.get('/api/v1/access-events/').json()['results'][0]
        self.assertEqual(len(data['frames']), 3)
        self.assertTrue(data['frames'][0]['is_evidence'])
        self.assertEqual(sum(f['is_evidence'] for f in data['frames']), 1)

    def test_each_frame_can_be_viewed(self):
        frames = self.client.get('/api/v1/access-events/').json()['results'][0]['frames']
        for frame in frames:
            url = f"/api/v1/gate/events/{self.event.pk}/image/original/?frame={frame['id']}"
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_a_frame_of_another_event_is_not_served(self):
        other = AccessEvent.objects.create(gate=self.gate, decision='denied', reason='no_plate')
        frame_id = self.event.frames.first().id
        response = self.client.get(f'/api/v1/gate/events/{other.pk}/image/original/?frame={frame_id}')
        self.assertEqual(response.status_code, 404)

    def test_a_frame_whose_file_is_gone_is_left_out(self):
        image = self.event.frames.exclude(pk=self.event.uploaded_image_id).first()
        os.remove(image.original_image.path)
        data = self.client.get('/api/v1/access-events/').json()['results'][0]
        self.assertEqual(len(data['frames']), 2)
