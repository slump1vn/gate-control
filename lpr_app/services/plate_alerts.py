"""
Telegram messages when a plate on the alert list passes a gate.

The decision never waits for Telegram: deliveries are recorded as pending
when the event is committed and sent from a small background pool. The same
plate at the same gate is announced at most once per
TELEGRAM_ALERT_COOLDOWN_SECONDS, so a retry of the same vehicle, or the
recognition attached to an approach opening, does not send twice.
"""

import html
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .. import metrics
from ..models import AlertDelivery, PlateAlert

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 15
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='telegram')

REASONS_VI = {
    'whitelist_hit': 'được mở (xe đã đăng ký)',
    'exit_free': 'được mở (chiều ra mở cho mọi xe)',
    'approach_open': 'được mở khi xe tới gần',
    'not_registered': 'bị từ chối (chưa đăng ký)',
    'expired': 'bị từ chối (đăng ký hết hạn)',
    'not_yet_valid': 'bị từ chối (đăng ký chưa có hiệu lực)',
    'inactive': 'bị từ chối (đăng ký bị tạm dừng)',
    'no_consensus': 'bị từ chối (các khung đọc không khớp)',
    'low_confidence': 'bị từ chối (độ tin cậy thấp)',
    'device_disabled': 'bị từ chối (cổng đang tắt)',
}
DIRECTIONS_VI = {'in': 'chiều vào', 'out': 'chiều ra'}


def configured():
    return bool(settings.TELEGRAM_BOT_TOKEN)


def default_chat_ids():
    return [c.strip() for c in settings.TELEGRAM_CHAT_IDS.split(',') if c.strip()]


def recipients(alert):
    return alert.chat_id_list() or default_chat_ids()


def matching_alert(event):
    """The active alert for this event's plate and direction, or None."""
    if not event.plate_normalized or event.is_test:
        return None
    alert = PlateAlert.objects.filter(plate_normalized=event.plate_normalized, is_active=True).first()
    if alert is None:
        return None
    if alert.directions != 'both' and event.direction and alert.directions != event.direction:
        return None
    return alert


def _recently_announced(alert, event):
    since = timezone.now() - timedelta(seconds=settings.TELEGRAM_ALERT_COOLDOWN_SECONDS)
    return AlertDelivery.objects.filter(
        alert=alert, event__gate=event.gate, created_at__gte=since, status__in=('pending', 'sent'),
    ).exclude(event=event).exists() or AlertDelivery.objects.filter(alert=alert, event=event).exists()


def notify_for_event(event):
    """
    Announce the event if its plate is on the alert list. Never raises: a
    notification problem must not touch the gate decision.
    """
    try:
        if not configured():
            return []
        alert = matching_alert(event)
        if alert is None:
            return []
        chats = recipients(alert)
        if not chats:
            logger.warning('Plate alert %s matched but has no recipients (TELEGRAM_CHAT_IDS is empty)',
                           alert.plate_normalized)
            return []
        if _recently_announced(alert, event):
            return []
        deliveries = [AlertDelivery.objects.create(alert=alert, event=event, chat_id=chat) for chat in chats]
        PlateAlert.objects.filter(pk=alert.pk).update(last_notified_at=timezone.now())
        ids = [d.pk for d in deliveries]
        transaction.on_commit(lambda: _pool.submit(_send_deliveries, ids))
        return deliveries
    except Exception:
        logger.exception('Plate alert for event %s failed', getattr(event, 'pk', None))
        return []


def message_text(event, alert):
    when = timezone.localtime(event.timestamp or timezone.now())
    plate = event.plate_raw or event.plate_normalized
    lines = [f'🚗 <b>{html.escape(plate)}</b>' + (f' — {html.escape(alert.label)}' if alert.label else '')]
    where = event.gate.name if event.gate else 'Không rõ cổng'
    if event.direction:
        where += f' · {DIRECTIONS_VI.get(event.direction, event.direction)}'
    lines.append(html.escape(where))
    outcome = REASONS_VI.get(event.reason, event.get_reason_display() if hasattr(event, 'get_reason_display') else event.reason)
    if event.mode == 'shadow' and event.decision == 'granted':
        outcome += ' — chế độ thử, barie không chuyển động'
    lines.append(f'Kết quả: {html.escape(outcome)}')
    if event.vehicle_id:
        lines.append(f'Xe đăng ký: {html.escape(event.vehicle.owner_name or event.vehicle.plate_display)}')
    lines.append(when.strftime('%H:%M:%S %d/%m/%Y'))
    return '\n'.join(lines)


def _evidence_path(event):
    image = event.uploaded_image
    if image is None or not image.original_image:
        return None
    try:
        path = image.original_image.path
    except (ValueError, NotImplementedError):
        return None
    return path if os.path.exists(path) else None


def _api(method):
    return f'{settings.TELEGRAM_API_URL}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}'


def _post(chat_id, text, photo_path=None):
    """Send one message; returns '' on success or the reason it failed (never the token)."""
    try:
        if photo_path:
            with open(photo_path, 'rb') as photo:
                response = requests.post(
                    _api('sendPhoto'), data={'chat_id': chat_id, 'caption': text, 'parse_mode': 'HTML'},
                    files={'photo': ('frame.jpg', photo, 'image/jpeg')}, timeout=SEND_TIMEOUT_SECONDS,
                )
        else:
            response = requests.post(
                _api('sendMessage'), json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
                timeout=SEND_TIMEOUT_SECONDS,
            )
    except requests.RequestException as exc:
        return f'Telegram unreachable: {type(exc).__name__}'
    if response.status_code == 200:
        return ''
    try:
        description = response.json().get('description', '')
    except ValueError:
        description = ''
    return f'HTTP {response.status_code} {description}'.strip()[:200]


def _send_deliveries(ids):
    try:
        for delivery in AlertDelivery.objects.filter(pk__in=ids, status='pending').select_related(
                'alert', 'event__gate', 'event__vehicle', 'event__uploaded_image'):
            event, alert = delivery.event, delivery.alert
            if event is None or alert is None:
                delivery.status, delivery.error = 'failed', 'event or alert deleted'
            else:
                photo = _evidence_path(event) if settings.TELEGRAM_SEND_PHOTO else None
                error = _post(delivery.chat_id, message_text(event, alert), photo)
                if error and photo:
                    # A photo Telegram refuses should not cost the message itself
                    error = _post(delivery.chat_id, message_text(event, alert))
                delivery.status, delivery.error = ('failed', error) if error else ('sent', '')
            delivery.save(update_fields=['status', 'error'])
            metrics.record_alert_delivery(delivery.status)
            if delivery.status == 'failed':
                logger.warning('Telegram alert to %s failed: %s', delivery.chat_id, delivery.error)
    except Exception:
        logger.exception('Sending Telegram alerts failed')
    finally:
        close_old_connections()


def send_test(chat_ids=None):
    """Send a test message now. Returns [{chat_id, ok, error}]."""
    chats = chat_ids or default_chat_ids()
    text = '✅ VietinBankSchool LPR: tin thử từ hệ thống cảnh báo biển số.'
    return [{'chat_id': chat, 'ok': not (err := _post(chat, text)), 'error': err} for chat in chats]
