"""
Daily time windows such as "06:30-08:00, 16:30-18:00", in the server's
TIME_ZONE. A window may cross midnight ("22:00-06:00"). An empty string
means all day.
"""

import re

from django.core.exceptions import ValidationError

_WINDOW = re.compile(r'^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$')


def _minutes(hours, minutes):
    h, m = int(hours), int(minutes)
    if h > 23 or m > 59:
        raise ValueError
    return h * 60 + m


def parse(text):
    """[(start_minute, end_minute), ...]; [] for all day. Raises ValueError on a bad window."""
    windows = []
    for part in (text or '').split(','):
        if not part.strip():
            continue
        match = _WINDOW.match(part)
        if not match:
            raise ValueError(part.strip())
        start = _minutes(match.group(1), match.group(2))
        end = _minutes(match.group(3), match.group(4))
        if start == end:
            raise ValueError(part.strip())
        windows.append((start, end))
    return windows


def validate_time_windows(value):
    try:
        parse(value)
    except ValueError as exc:
        raise ValidationError(
            f'"{exc}" is not a time window: use HH:MM-HH:MM, several separated by commas',
            code='invalid_time_window',
        )


def contains(text, at):
    """Whether the local time `at` (an aware datetime) falls in one of the windows."""
    windows = parse(text)
    if not windows:
        return True
    minute = at.hour * 60 + at.minute
    for start, end in windows:
        if start < end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:  # crosses midnight
            return True
    return False
