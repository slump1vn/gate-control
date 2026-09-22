import os
import shutil
import sys
import tempfile
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from lpr_app import metrics
from lpr_app.models import AccessEvent, Camera, GateDevice, UploadedImage


def sample(metric, **labels):
    return metrics.REGISTRY.get_sample_value(metric, labels) or 0


class GateMetricsTest(TestCase):
    def setUp(self):
        self.gate = GateDevice.objects.create(name='metrics-gate')

    def test_decision_counter_and_latency(self):
        labels = dict(gate='metrics-gate', decision='denied', reason='no_plate')
        before = sample('lpr_gate_decisions_total', **labels)
        before_count = sample('lpr_gate_decision_duration_seconds_count', gate='metrics-gate')
        event = AccessEvent.objects.create(gate=self.gate, decision='denied', reason='no_plate', decision_latency_ms=2500)
        metrics.record_gate_decision(event)
        self.assertEqual(sample('lpr_gate_decisions_total', **labels), before + 1)
        self.assertEqual(sample('lpr_gate_decision_duration_seconds_count', gate='metrics-gate'), before_count + 1)

    def test_decision_without_gate(self):
        event = AccessEvent.objects.create(decision='denied', reason='device_disabled')
        before = sample('lpr_gate_decisions_total', gate='unknown', decision='denied', reason='device_disabled')
        metrics.record_gate_decision(event)
        self.assertEqual(sample('lpr_gate_decisions_total', gate='unknown', decision='denied', reason='device_disabled'), before + 1)

    def test_command_counter(self):
        event = AccessEvent.objects.create(gate=self.gate, decision='manual', reason='manual_override', command='open')
        before = sample('lpr_gate_commands_total', gate='metrics-gate', command='open', result='ok')
        metrics.record_gate_command(event, 'ok')
        self.assertEqual(sample('lpr_gate_commands_total', gate='metrics-gate', command='open', result='ok'), before + 1)

    def test_liveness_gauges(self):
        now = timezone.now()
        cam = Camera.objects.create(name='metrics-cam', host='192.168.1.64',
                                    agent_status='streaming', agent_status_at=now)
        self.gate.last_seen = now
        self.gate.save()
        metrics.update_gate_metrics(now)
        self.assertEqual(sample('lpr_gate_controller_up', gate='metrics-gate'), 1)
        self.assertEqual(sample('lpr_gate_camera_streaming', camera='metrics-cam'), 1)

        later = now + timedelta(minutes=5)
        metrics.update_gate_metrics(later)
        self.assertEqual(sample('lpr_gate_controller_up', gate='metrics-gate'), 0)
        self.assertEqual(sample('lpr_gate_camera_streaming', camera='metrics-cam'), 0)

        Camera.objects.filter(pk=cam.pk).update(agent_status='auth_failed', agent_status_at=later)
        metrics.update_gate_metrics(later)
        self.assertEqual(sample('lpr_gate_camera_streaming', camera='metrics-cam'), 0)

    def test_metrics_endpoint_exposes_gate_series(self):
        event = AccessEvent.objects.create(gate=self.gate, decision='granted', reason='whitelist_hit')
        metrics.record_gate_decision(event)
        body = self.client.get('/metrics/').content.decode()
        self.assertIn('lpr_gate_decisions_total', body)
        self.assertIn('lpr_gate_controller_up{gate="metrics-gate"}', body)

    def test_metric_failures_never_raise(self):
        metrics.record_gate_decision(None)
        metrics.record_gate_command(None, 'ok')
        with mock.patch('lpr_app.models.GateDevice.objects') as objects:
            objects.all.side_effect = RuntimeError('db down')
            metrics.update_gate_metrics()


class PurgeAccessEventsTest(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media, GATE_EVENT_RETENTION_DAYS=90)
        override.enable()
        self.addCleanup(override.disable)
        self.gate = GateDevice.objects.create(name='Main')

    def _frame(self, age=timedelta(0)):
        image = UploadedImage.objects.create(
            original_image=SimpleUploadedFile('f.jpg', b'\xff\xd8', content_type='image/jpeg'),
            source='gate',
        )
        UploadedImage.objects.filter(pk=image.pk).update(upload_timestamp=timezone.now() - age)
        return image

    def _event(self, age, image=None):
        event = AccessEvent.objects.create(gate=self.gate, decision='denied', reason='no_plate', uploaded_image=image)
        AccessEvent.objects.filter(pk=event.pk).update(timestamp=timezone.now() - age)
        return event

    def _run(self, *args):
        out = StringIO()
        call_command('purge_access_events', *args, stdout=out)
        return out.getvalue()

    def test_deletes_old_events_and_their_frames(self):
        old_frame = self._frame(timedelta(days=100))
        old_path = old_frame.original_image.path
        old = self._event(timedelta(days=100), old_frame)
        recent_frame = self._frame()
        recent = self._event(timedelta(days=10), recent_frame)

        output = self._run()
        self.assertIn('Deleted 1 access events', output)
        self.assertFalse(AccessEvent.objects.filter(pk=old.pk).exists())
        self.assertFalse(UploadedImage.objects.filter(pk=old_frame.pk).exists())
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(AccessEvent.objects.filter(pk=recent.pk).exists())
        self.assertTrue(os.path.exists(recent_frame.original_image.path))

    def test_orphaned_gate_frames_removed(self):
        orphan = self._frame(timedelta(hours=2))
        fresh = self._frame(timedelta(minutes=5))
        upload = UploadedImage.objects.create(
            original_image=SimpleUploadedFile('u.jpg', b'\xff\xd8', content_type='image/jpeg'),
        )
        UploadedImage.objects.filter(pk=upload.pk).update(upload_timestamp=timezone.now() - timedelta(days=5))
        output = self._run()
        self.assertIn('1 orphaned gate frames', output)
        self.assertFalse(UploadedImage.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(UploadedImage.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(UploadedImage.objects.filter(pk=upload.pk).exists())

    def test_dry_run(self):
        self._event(timedelta(days=100), self._frame(timedelta(days=100)))
        output = self._run('--dry-run')
        self.assertIn('Would delete 1 access events', output)
        self.assertEqual(AccessEvent.objects.count(), 1)
        self.assertEqual(UploadedImage.objects.count(), 1)

    def test_days_override(self):
        self._event(timedelta(days=10))
        self._run('--days', '5')
        self.assertEqual(AccessEvent.objects.count(), 0)


class SchedulerRegistrationTest(TestCase):
    def test_purge_job_registered(self):
        from lpr_app.apps import LprAppConfig
        import lpr_app
        scheduler = mock.MagicMock()
        with mock.patch('lpr_app.apps._should_run_scheduler', return_value=True), \
                mock.patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=scheduler), \
                mock.patch('django_apscheduler.jobstores.DjangoJobStore'):
            LprAppConfig('lpr_app', lpr_app).ready()
        job_ids = [c.kwargs['id'] for c in scheduler.add_job.call_args_list]
        self.assertEqual(job_ids, ['retry_stuck_images', 'purge_access_events'])
        scheduler.start.assert_called_once()

    def test_scheduler_entrypoint_calls_command(self):
        from lpr_app import scheduler
        with mock.patch('lpr_app.scheduler.call_command') as cc:
            scheduler.run_purge_access_events()
        cc.assert_called_once_with('purge_access_events')
