import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from lpr_app.models import AccessEvent, UploadedImage
from lpr_app.services.gate_service import FRAME_SOURCE, discard_frame

logger = logging.getLogger(__name__)

# Gate frames not linked to any event after this long were abandoned
ORPHAN_FRAME_AGE = timedelta(hours=1)


class Command(BaseCommand):
    help = (
        'Delete access events older than GATE_EVENT_RETENTION_DAYS together with '
        'their captured frames, and remove orphaned gate frames'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None,
                            help='Override GATE_EVENT_RETENTION_DAYS for this run')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be deleted without deleting')

    def handle(self, *args, **options):
        days = options['days'] if options['days'] is not None else settings.GATE_EVENT_RETENTION_DAYS
        dry_run = options['dry_run']
        now = timezone.now()

        expired = AccessEvent.objects.filter(timestamp__lt=now - timedelta(days=days))
        image_ids = list(expired.exclude(uploaded_image=None).values_list('uploaded_image_id', flat=True))
        event_count = expired.count()

        orphans = list(
            UploadedImage.objects.filter(source=FRAME_SOURCE, upload_timestamp__lt=now - ORPHAN_FRAME_AGE)
            .filter(access_events=None)
            .values_list('id', flat=True)
        )

        if not dry_run:
            expired.delete()
            for image_id in image_ids + orphans:
                discard_frame(image_id)

        verb = 'Would delete' if dry_run else 'Deleted'
        message = (
            f'{verb} {event_count} access events older than {days} days, '
            f'{len(image_ids)} event frames, {len(orphans)} orphaned gate frames'
        )
        logger.info(message)
        self.stdout.write(self.style.SUCCESS(message))
