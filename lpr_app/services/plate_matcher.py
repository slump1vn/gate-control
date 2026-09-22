"""
Registry matching for gate decisions.

Matching is exact on the normalised plate. A near miss (one edit away from
exactly one allowed vehicle) is reported for the operator but never matched.
"""

from dataclasses import dataclass
from typing import Optional

from django.utils import timezone

from ..models import Vehicle
from ..utils.plates import normalize_plate, within_one_edit


@dataclass
class MatchResult:
    plate: str
    allowed: bool
    reason: str
    vehicle: Optional[Vehicle] = None
    near_miss: Optional[Vehicle] = None


def match(plate, at=None):
    """Match a plate (raw or normalised) against the registry."""
    at = at or timezone.now()
    normalized = normalize_plate(plate)
    if not normalized:
        return MatchResult(plate='', allowed=False, reason='no_plate')

    vehicle = Vehicle.objects.filter(plate_normalized=normalized).first()
    if vehicle is not None:
        allowed, reason = vehicle.access_status(at)
        return MatchResult(plate=normalized, allowed=allowed, reason=reason, vehicle=vehicle)

    return MatchResult(
        plate=normalized,
        allowed=False,
        reason='not_registered',
        near_miss=find_near_miss(normalized, at),
    )


def find_near_miss(normalized, at=None):
    """Return the single allowed vehicle one edit away, or None if zero or several."""
    at = at or timezone.now()
    candidates = [
        v for v in Vehicle.objects.filter(is_active=True)
        if within_one_edit(normalized, v.plate_normalized) and v.access_status(at)[0]
    ]
    return candidates[0] if len(candidates) == 1 else None
