"""
Tests for the gate decision service and endpoint. The recognition pipeline is
mocked; frames run synchronously through a fake executor unless a test needs
to control completion.
"""

import os
import shutil
import tempfile
import time
from concurrent.futures import Future
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from lpr_app.models import AccessEvent, GateDevice, UploadedImage, Vehicle
from lpr_app.services import gate_service
from lpr_app.services.gate_service import FrameRead, evaluate, primary_plate

AGENT = {'HTTP_AUTHORIZATION': 'Bearer agent-secret'}
GATE_SETTINGS = dict(
    GATE_AGENT_TOKEN='agent-secret',
    GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    GATE_BURST_FRAMES=3,
    GATE_CONSENSUS_MIN=2,
    GATE_MIN_CONFIDENCE=0.8,
    GATE_DECIDE_TIMEOUT=8,
    GATE_MODE='shadow',
)


class MediaDirMixin:
    """Give each test its own MEDIA_ROOT so file assertions see only its files."""

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp(prefix='gate_test_media_')
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def media_files(self):
        return sorted(f for _, _, files in os.walk(self.media) for f in files)


def det(text, conf=0.9, box=(0, 0, 100, 30)):
    x1, y1, x2, y2 = box
    return {
        'plate': {'coordinates': {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2}},
        'ocr': [{'text': text, 'confidence': conf}] if text else [],
    }


def read(plate, conf=0.9, ok=True, image_id=1):
    from lpr_app.utils.plates import normalize_plate
    return FrameRead(image_id=image_id, ok=ok, plate_raw=plate, plate=normalize_plate(plate), confidence=conf)


def jpeg(name='frame.jpg', size=1024):
    return SimpleUploadedFile(name, b'\xff\xd8' + b'\x00' * size, content_type='image/jpeg')


class SyncExecutor:
    """Runs submitted work immediately in the calling thread."""

    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:  # pragma: no cover - read_frame never raises
            future.set_exception(exc)
        return future


class PrimaryPlateTest(SimpleTestCase):
    def test_largest_plate_wins(self):
        response = {'detections': [
            det('51F11111', 0.95, (0, 0, 50, 15)),
            det('30A12345', 0.85, (0, 0, 200, 60)),
        ]}
        self.assertEqual(primary_plate(response), ('30A12345', 0.85))

    def test_detection_without_ocr_ignored(self):
        response = {'detections': [det('', box=(0, 0, 500, 500)), det('30A12345')]}
        self.assertEqual(primary_plate(response)[0], '30A12345')

    def test_empty_and_malformed(self):
        self.assertEqual(primary_plate(None), ('', None))
        self.assertEqual(primary_plate({'detections': []}), ('', None))
        weird = {'detections': {'a': {'plate': {}, 'ocr': [{'text': '30A12345', 'confidence': 'x'}]}}}
        self.assertEqual(primary_plate(weird), ('30A12345', None))


@override_settings(**GATE_SETTINGS)
class EvaluateTest(TestCase):
    def setUp(self):
        self.car = Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')

    def test_granted_with_two_of_three(self):
        outcome = evaluate([read('30A12345', 0.91, image_id=1), read('3OA-123.45', 0.84, image_id=2),
                            read('', None, image_id=3)])
        self.assertTrue(outcome.granted)
        self.assertEqual(outcome.reason, 'whitelist_hit')
        self.assertEqual((outcome.frames_read, outcome.frames_agreed), (3, 2))
        self.assertEqual(outcome.confidence, 0.91)
        self.assertEqual(outcome.evidence.image_id, 1)
        self.assertEqual(outcome.match.vehicle, self.car)

    def test_no_consensus(self):
        outcome = evaluate([read('30A12345'), read('51F99999'), read('29X123456')])
        self.assertEqual(outcome.reason, 'no_consensus')
        self.assertFalse(outcome.granted)

    def test_low_confidence(self):
        outcome = evaluate([read('30A12345', 0.7), read('30A12345', 0.75)])
        self.assertEqual(outcome.reason, 'low_confidence')

    def test_missing_confidence_counts_as_zero(self):
        outcome = evaluate([read('30A12345', None), read('30A12345', None)])
        self.assertEqual(outcome.reason, 'low_confidence')

    def test_no_plate(self):
        outcome = evaluate([read(''), read('')])
        self.assertEqual(outcome.reason, 'no_plate')
        self.assertIsNotNone(outcome.evidence)

    def test_not_registered_with_near_miss(self):
        outcome = evaluate([read('30A12346'), read('30A12346')])
        self.assertEqual(outcome.reason, 'not_registered')
        self.assertEqual(outcome.match.near_miss, self.car)
        self.assertFalse(outcome.granted)

    def test_expired(self):
        self.car.valid_until = timezone.now() - timedelta(days=1)
        self.car.save()
        self.assertEqual(evaluate([read('30A12345'), read('30A12345')]).reason, 'expired')

    def test_all_frames_failed(self):
        self.assertEqual(evaluate([read('', ok=False), read('', ok=False)]).reason, 'processing_error')

    def test_timeout_with_nothing_read(self):
        self.assertEqual(evaluate([], pending=3).reason, 'inference_timeout')

    def test_timeout_when_pending_frames_could_reach_consensus(self):
        self.assertEqual(evaluate([read('30A12345')], pending=2).reason, 'inference_timeout')
        self.assertEqual(evaluate([read('')], pending=2).reason, 'inference_timeout')

    def test_no_consensus_when_pending_cannot_help(self):
        outcome = evaluate([read('30A12345'), read('51F99999')], pending=0)
        self.assertEqual(outcome.reason, 'no_consensus')

    def test_multiple_plates_majority_wins(self):
        outcome = evaluate([read('30A12345', 0.9), read('30A12345', 0.85), read('51F99999', 0.99)])
        self.assertEqual(outcome.plate, '30A12345')
        self.assertTrue(outcome.granted)

    @override_settings(GATE_CONSENSUS_MIN=3)
    def test_consensus_min_setting(self):
        self.assertEqual(evaluate([read('30A12345'), read('30A12345'), read('')]).reason, 'no_consensus')


class ModeTest(SimpleTestCase):
    def test_modes(self):
        for value, expected in (('live', 'live'), ('LIVE', 'live'), ('shadow', 'shadow'), ('', 'shadow'), ('on', 'shadow')):
            with override_settings(GATE_MODE=value):
                self.assertEqual(gate_service.effective_mode(), expected, value)


def fake_pipeline(results):
    """Patch the pipeline so frame N gets results[N] (a detections list, or False to fail)."""
    calls = {'n': 0}

    def process(image, save_image=True):
        idx = calls['n']
        calls['n'] += 1
        outcome = results[idx]
        if outcome is False:
            image.processing_status = 'failed'
            image.save()
            return {'success': False, 'error': 'model down'}
        image.api_response = {'detections': outcome}
        image.processing_status = 'completed'
        image.save()
        # Mimic the pipeline writing a processed image next to the original
        processed = os.path.join(os.path.dirname(image.original_image.path), f'processed_{os.path.basename(image.original_image.name)}')
        with open(processed, 'wb') as f:
            f.write(b'p')
        image.processed_image.name = os.path.relpath(processed, settings.MEDIA_ROOT)
        image.save()
        return {'success': True}

    return mock.patch(
        'lpr_app.services.gate_service.ImageProcessingService.process_uploaded_image',
        side_effect=process,
    )


@override_settings(**GATE_SETTINGS)
class DecideEndpointTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gate_service, '_executor', SyncExecutor())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gate = GateDevice.objects.create(name='Main gate')
        self.car = Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')

    def _post(self, n=3, gate_id=None, headers=AGENT, **extra):
        data = {'gate_id': gate_id if gate_id is not None else self.gate.id,
                'frames': [jpeg(f'f{i}.jpg') for i in range(n)]}
        data.update(extra)
        return self.client.post('/api/v1/gate/decide/', data, **headers)

    def test_granted_in_shadow_mode_does_not_actuate(self):
        with fake_pipeline([[det('30A12345', 0.93)], [det('30A-123.45', 0.88)], [det('')]]):
            response = self._post()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['decision'], 'granted')
        self.assertEqual(data['reason'], 'whitelist_hit')
        self.assertEqual(data['mode'], 'shadow')
        self.assertFalse(data['actuate'])
        self.assertIsNone(data['command'])
        self.assertEqual(data['vehicle']['id'], self.car.id)
        event = AccessEvent.objects.get(pk=data['event_id'])
        self.assertEqual(event.frames_agreed, 2)
        self.assertFalse(event.command_sent)

    @override_settings(GATE_MODE='live')
    def test_granted_in_live_mode_actuates(self):
        with fake_pipeline([[det('30A12345')], [det('30A12345')], [det('30A12345')]]):
            data = self._post().json()
        self.assertTrue(data['actuate'])
        self.assertEqual(data['command'], 'open')
        self.assertEqual(data['mode'], 'live')

    @override_settings(GATE_MODE='live')
    def test_denied_in_live_mode_does_not_actuate(self):
        with fake_pipeline([[det('51F99999')], [det('51F99999')], [det('51F99999')]]):
            data = self._post().json()
        self.assertEqual((data['decision'], data['reason']), ('denied', 'not_registered'))
        self.assertFalse(data['actuate'])

    def test_all_agree(self):
        with fake_pipeline([[det('30A12345')], [det('30A12345')], [det('30A12345')]]):
            data = self._post().json()
        self.assertEqual(data['decision'], 'granted')
        self.assertEqual(data['frames_agreed'], 3)

    def test_only_evidence_frame_is_kept(self):
        with fake_pipeline([[det('30A12345', 0.8)], [det('30A12345', 0.95)]]):
            data = self._post(n=2).json()
        event = AccessEvent.objects.get(pk=data['event_id'])
        images = UploadedImage.objects.all()
        self.assertEqual(images.count(), 1)
        kept = images.get()
        self.assertEqual(event.uploaded_image, kept)
        self.assertEqual(kept.source, 'gate')
        self.assertEqual(event.confidence, 0.95)
        self.assertTrue(os.path.exists(kept.original_image.path))
        self.assertTrue(os.path.exists(kept.processed_image.path))

    def test_discarded_frame_files_removed(self):
        with fake_pipeline([[det('30A12345', 0.8)], [det('30A12345', 0.95)]]):
            self._post(n=2)
        kept = os.path.basename(UploadedImage.objects.get().original_image.name)
        self.assertEqual(self.media_files(), sorted([kept, f'processed_{kept}']))

    def test_no_plate_keeps_one_frame_as_evidence(self):
        with fake_pipeline([[det('')], [det('')]]):
            data = self._post(n=2).json()
        self.assertEqual(data['reason'], 'no_plate')
        self.assertEqual(UploadedImage.objects.count(), 1)

    def test_all_frames_fail(self):
        with fake_pipeline([False, False]):
            data = self._post(n=2).json()
        self.assertEqual(data['reason'], 'processing_error')
        self.assertEqual(UploadedImage.objects.count(), 0)
        self.assertEqual(AccessEvent.objects.get().decision, 'denied')

    def test_disabled_gate(self):
        self.gate.is_enabled = False
        self.gate.save()
        with fake_pipeline([]) as process:
            data = self._post().json()
        self.assertEqual(data['reason'], 'device_disabled')
        process.assert_not_called()
        self.assertEqual(AccessEvent.objects.get().gate, self.gate)

    def test_unknown_gate_recorded(self):
        data = self._post(gate_id=9999).json()
        self.assertEqual(data['reason'], 'device_disabled')
        self.assertIsNone(AccessEvent.objects.get().gate)

    def test_requires_agent_token(self):
        self.assertEqual(self._post(headers={}).status_code, 403)
        self.assertEqual(self._post(headers={'HTTP_AUTHORIZATION': 'Bearer wrong'}).status_code, 403)
        self.assertEqual(AccessEvent.objects.count(), 0)

    def test_too_many_frames_rejected_before_processing(self):
        with fake_pipeline([]) as process:
            response = self._post(n=4)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_code'], 'TOO_MANY_FRAMES')
        process.assert_not_called()
        self.assertEqual(UploadedImage.objects.count(), 0)

    def test_no_frames(self):
        response = self._post(n=0)
        self.assertEqual(response.json()['error_code'], 'MISSING_FRAMES')

    def test_missing_gate_id(self):
        response = self.client.post('/api/v1/gate/decide/', {'frames': [jpeg()]}, **AGENT)
        self.assertEqual(response.json()['error_code'], 'MISSING_GATE')

    @override_settings(UPLOAD_FILE_MAX_SIZE=2048)
    def test_oversized_frame_rejects_request(self):
        data = {'gate_id': self.gate.id, 'frames': [jpeg('a.jpg', 100), jpeg('b.jpg', 4096)]}
        response = self.client.post('/api/v1/gate/decide/', data, **AGENT)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_code'], 'FILE_TOO_LARGE')

    def test_burst_of_large_frames_within_per_file_limit(self):
        # 3 frames of 1.5MB each are fine: the 2MB limit is per file
        frames = [jpeg(f'f{i}.jpg', int(1.5 * 1024 * 1024)) for i in range(3)]
        with fake_pipeline([[det('30A12345')], [det('30A12345')], [det('30A12345')]]):
            response = self.client.post('/api/v1/gate/decide/', {'gate_id': self.gate.id, 'frames': frames}, **AGENT)
        self.assertEqual(response.status_code, 200)

    def test_wrong_file_type(self):
        bad = SimpleUploadedFile('x.gif', b'GIF89a', content_type='image/gif')
        response = self.client.post('/api/v1/gate/decide/', {'gate_id': self.gate.id, 'frames': [bad]}, **AGENT)
        self.assertEqual(response.json()['error_code'], 'INVALID_FILE_TYPE')

    def test_not_rate_limited(self):
        with override_settings(RATE_LIMIT_ENABLE=True, RATE_LIMIT_RATE='1/min'):
            from lpr_app.middleware import rate_limit  # noqa: F401  (middleware reads settings per request)
            codes = []
            for _ in range(4):
                with fake_pipeline([[det('')], [det('')]]):
                    codes.append(self._post(n=2).status_code)
        self.assertEqual(codes, [200, 200, 200, 200])

    def test_gate_frames_hidden_from_public_endpoints(self):
        with fake_pipeline([[det('30A12345')], [det('30A12345')]]):
            self._post(n=2)
        image = UploadedImage.objects.get()
        self.assertEqual(self.client.get('/api/v1/images/').json()['count'], 0)
        self.assertEqual(self.client.get(f'/api/v1/images/{image.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/download/{image.id}/original/').status_code, 404)
        self.assertEqual(self.client.get(f'/download/{image.id}/original/').status_code, 404)


@override_settings(**GATE_SETTINGS)
class DeadlineTest(MediaDirMixin, TestCase):
    """Frames that do not finish in time: controlled futures instead of threads."""

    def setUp(self):
        super().setUp()
        self.gate = GateDevice.objects.create(name='Main gate')
        Vehicle.objects.create(plate_display='30A12345', owner_name='A')

    def _images(self, n):
        return [gate_service.create_frame_record(jpeg(f'd{i}.jpg')) for i in range(n)]

    def test_unstarted_frames_cancelled_and_discarded(self):
        images = self._images(2)
        futures = [Future(), Future()]
        executor = mock.Mock()
        executor.submit.side_effect = futures
        with mock.patch.object(gate_service, '_executor', executor):
            reads, pending = gate_service.read_frames([i.pk for i in images], deadline=time.monotonic() + 0.05)
        self.assertEqual((reads, pending), ([], 2))
        self.assertTrue(all(f.cancelled() for f in futures))
        self.assertEqual(UploadedImage.objects.count(), 0)

    def test_stops_waiting_once_consensus_is_reached(self):
        images = self._images(3)
        done1, done2, running = Future(), Future(), Future()
        done1.set_result(read('30A12345', image_id=images[0].pk))
        done2.set_result(read('30A12345', image_id=images[1].pk))
        running.set_running_or_notify_cancel()
        executor = mock.Mock()
        executor.submit.side_effect = [done1, done2, running]
        started = time.monotonic()
        with mock.patch.object(gate_service, '_executor', executor):
            reads, pending = gate_service.read_frames([i.pk for i in images], deadline=started + 5)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual((len(reads), pending), (2, 1))
        running.set_result(FrameRead(image_id=images[2].pk, ok=True))
        self.assertFalse(UploadedImage.objects.filter(pk=images[2].pk).exists())

    def test_running_frame_discarded_when_it_finishes(self):
        images = self._images(1)
        future = Future()
        future.set_running_or_notify_cancel()
        executor = mock.Mock()
        executor.submit.return_value = future
        with mock.patch.object(gate_service, '_executor', executor):
            reads, pending = gate_service.read_frames([images[0].pk], deadline=time.monotonic() + 0.05)
        self.assertEqual(pending, 1)
        self.assertEqual(UploadedImage.objects.count(), 1)
        future.set_result(FrameRead(image_id=images[0].pk, ok=True))
        self.assertEqual(UploadedImage.objects.count(), 0)

    def test_decide_times_out(self):
        future = Future()
        future.set_running_or_notify_cancel()
        executor = mock.Mock()
        executor.submit.return_value = future
        with mock.patch.object(gate_service, '_executor', executor), \
                override_settings(GATE_DECIDE_TIMEOUT=0):
            decision = gate_service.decide(self.gate.id, [jpeg()])
        self.assertEqual(decision.event.reason, 'inference_timeout')
        self.assertEqual(decision.event.decision, 'denied')
        self.assertFalse(decision.actuate)

    def test_read_frame_handles_missing_record(self):
        result = gate_service.read_frame(999999)
        self.assertFalse(result.ok)

    def test_discard_missing_record_is_noop(self):
        gate_service.discard_frame(999999)


@override_settings(**GATE_SETTINGS)
class RetryJobSkipsGateFramesTest(MediaDirMixin, TestCase):
    def test_retry_ignores_gate_frames(self):
        from django.core.management import call_command
        image = gate_service.create_frame_record(jpeg())
        UploadedImage.objects.filter(pk=image.pk).update(
            processing_status='processing', upload_timestamp=timezone.now() - timedelta(hours=1),
        )
        with mock.patch('lpr_app.management.commands.retry_stuck_images.ImageProcessingService.process_uploaded_image') as process:
            call_command('retry_stuck_images', stdout=open(os.devnull, 'w'))
        process.assert_not_called()
