import logging
import os
import sys

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


def _should_run_scheduler():
    if not getattr(settings, 'RETRY_SCHEDULER_ENABLED', True):
        return False
    if os.environ.get('RUN_SCHEDULER', '').lower() in ('1', 'true', 'yes'):
        return True
    return 'gunicorn' in os.path.basename(sys.argv[0]).lower()


class LprAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lpr_app'
    verbose_name = 'License Plate Recognition'

    def ready(self):
        if not _should_run_scheduler():
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
            from django_apscheduler.jobstores import DjangoJobStore

            # Without an explicit timezone APScheduler uses the container's clock (UTC),
            # so a 03:00 job would not run at 03:00 local time.
            scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
            scheduler.add_jobstore(DjangoJobStore(), 'default')

            scheduler.add_job(
                'lpr_app.scheduler:run_retry_stuck_images',
                trigger=IntervalTrigger(minutes=settings.RETRY_INTERVAL_MINUTES),
                id='retry_stuck_images',
                replace_existing=True,
            )

            scheduler.add_job(
                'lpr_app.scheduler:run_purge_access_events',
                trigger=CronTrigger(hour=3, minute=0),
                id='purge_access_events',
                replace_existing=True,
            )

            scheduler.start()
            logger.info(
                'APScheduler started: retry_stuck_images every %d minutes, '
                'purge_access_events daily at 03:00',
                settings.RETRY_INTERVAL_MINUTES,
            )
        except Exception:
            logger.exception('Failed to start APScheduler')