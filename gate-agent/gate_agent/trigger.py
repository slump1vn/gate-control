"""
Presence trigger: decides when a vehicle has arrived and stopped in the
region of interest, so recognition runs a few times per vehicle rather than
on every frame.

Two scores per frame, both mean absolute pixel differences (0-1) on a small
greyscale image:
- motion: against the previous frame (is something moving?)
- presence: against a slowly learned background of the empty lane (is
  something there?)

A read is triggered when motion stops (for settle_ms) and something is
present. A pedestrian walking through leaves no presence once they are gone,
so nothing is triggered. After a decision, a denied vehicle is retried after
the cooldown up to max_attempts; the lane must clear, or a different vehicle
must replace the first, before the next trigger.

With shadow_filter on, a change of brightness only counts as presence if it
also changes the texture of the lane (see texture_difference): a shadow
sweeping across the zone darkens it without bringing anything into it.
"""

import math

from PIL import Image

THUMB_WIDTH = 64

IDLE = 'idle'
MOTION = 'motion'
OCCUPIED = 'occupied'


def prepare(image):
    """Small blurred-by-downscale greyscale version of a (cropped) frame."""
    gray = image.convert('L')
    width, height = gray.size
    thumb_height = max(1, round(height * THUMB_WIDTH / max(width, 1)))
    return gray.resize((THUMB_WIDTH, thumb_height), Image.BILINEAR)


def pixels(image):
    # get_flattened_data replaces getdata (deprecated in Pillow 12, removed in 14)
    data = image.get_flattened_data() if hasattr(image, 'get_flattened_data') else image.getdata()
    return [float(v) for v in data]


def difference(a, b):
    """Mean absolute difference (0-1) between two prepared frames or pixel lists."""
    if isinstance(a, Image.Image):
        a = pixels(a)
    if isinstance(b, Image.Image):
        b = pixels(b)
    if len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * max(len(a), 1))


TEXTURE_BLUR_RADIUS = 2
# Keeps log() finite on black pixels and damps noise in the darkest ones
TEXTURE_OFFSET = 8.0


def _box_blur(values, width, radius):
    """Mean over a (2r+1)-square window, clipped at the edges. Box blur is separable."""
    height = len(values) // width
    rows = [0.0] * len(values)
    for y in range(height):
        start = y * width
        for x in range(width):
            lo, hi = max(0, x - radius), min(width, x + radius + 1)
            rows[start + x] = sum(values[start + lo:start + hi]) / (hi - lo)
    out = [0.0] * len(values)
    for x in range(width):
        for y in range(height):
            lo, hi = max(0, y - radius), min(height, y + radius + 1)
            out[y * width + x] = sum(rows[yy * width + x] for yy in range(lo, hi)) / (hi - lo)
    return out


def texture_difference(a, b, width):
    """
    How much of the change from b to a a change of light cannot explain (0 = none).

    A shadow or a passing cloud multiplies the brightness of the lane by a
    roughly constant factor, so log(a / b) is flat underneath it and only
    steps at its soft edge. A vehicle brings bodywork, windows, wheels and
    plates, and log(a / b) is anything but flat. The score is the mean
    distance of log(a / b) from its local average.
    """
    if isinstance(a, Image.Image):
        a = pixels(a)
    if isinstance(b, Image.Image):
        b = pixels(b)
    if len(a) != len(b) or not width:
        return 1.0
    ratio = [math.log(x + TEXTURE_OFFSET) - math.log(y + TEXTURE_OFFSET) for x, y in zip(a, b)]
    local = _box_blur(ratio, width, TEXTURE_BLUR_RADIUS)
    return sum(abs(r - m) for r, m in zip(ratio, local)) / max(len(ratio), 1)


class PresenceTrigger:
    def __init__(self, motion_threshold=0.02, settle_ms=800, cooldown_s=5, presence_factor=3.0,
                 max_attempts=2, max_occupied_seconds=300.0, background_alpha=0.05,
                 moving_read_seconds=0.0, shadow_filter=False, texture_threshold=0.025):
        self.motion_threshold = motion_threshold
        self.presence_threshold = max(motion_threshold * presence_factor, 0.04)
        self.settle = settle_ms / 1000.0
        self.cooldown = cooldown_s
        self.max_attempts = max_attempts
        self.max_occupied = max_occupied_seconds
        self.alpha = background_alpha
        # A vehicle that rolls slowly through the zone never settles; read it
        # anyway once it has been present this long.
        self.moving_read = moving_read_seconds
        # Brightness change without a texture change (a shadow) is not presence
        self.shadow_filter = shadow_filter
        self.texture_threshold = texture_threshold
        self.width = 0

        self.state = IDLE
        self.background = None
        self.previous = None
        self.present_since = None
        self.last_motion_at = None
        self.clear_since = None
        self.occupied_since = None
        self.awaiting_decision = False
        self.decided_at = None
        self.last_granted = False
        self.attempts = 0
        self.decision_frame = None
        self.frame = None
        self.fired_while_moving = False
        self.last_scores = (0.0, 0.0)

    def update(self, frame, now):
        """Feed a prepared frame. Returns True when a recognition burst should start now."""
        # The background is kept in floats: blending 8-bit images rounds away
        # small steps, so a slow light change would never be learned.
        if isinstance(frame, Image.Image):
            self.width = frame.size[0]
        frame = pixels(frame)
        self.frame = frame
        if self.background is None or len(self.background) != len(frame):
            self.background = frame
            self.previous = frame
            return False

        motion = difference(frame, self.previous)
        presence = self._change(frame, self.background)
        self.previous = frame
        self.last_scores = (motion, presence)
        moving = motion > self.motion_threshold
        present = presence > self.presence_threshold

        if present and self.present_since is None:
            self.present_since = now
        elif not present:
            self.present_since = None

        if self.state == IDLE:
            if moving or present:
                self.state = MOTION
                self.last_motion_at = now
            else:
                a = self.alpha
                self.background = [b + a * (f - b) for b, f in zip(self.background, frame)]
            return False

        if self.state == MOTION:
            if moving:
                self.last_motion_at = now
                if (self.moving_read and present and self.present_since is not None
                        and now - self.present_since >= self.moving_read):
                    # Still moving after all this time: read it now rather than
                    # wait for a stop that may never come.
                    return self._fire(now, new_vehicle=True, moving=True)
                return False
            if now - self.last_motion_at < self.settle:
                return False
            if present:
                return self._fire(now, new_vehicle=True)
            self.state = IDLE
            return False

        # OCCUPIED
        if self.awaiting_decision:
            return False
        if moving:
            self.last_motion_at = now
            self.clear_since = None
            return False
        if not present:
            if self.clear_since is None:
                self.clear_since = now
            elif now - self.clear_since >= self.settle:
                self.state = IDLE
            return False
        self.clear_since = None
        if now - self.last_motion_at < self.settle:
            return False
        if self.decision_frame is not None and self._change(frame, self.decision_frame) > self.presence_threshold:
            # A different vehicle replaced the one we decided on
            return self._fire(now, new_vehicle=True)
        if now - self.occupied_since > self.max_occupied:
            # Something is parked in the zone: accept it as the new empty lane
            self.background = frame
            self.state = IDLE
            return False
        if (not self.last_granted and self.attempts < self.max_attempts
                and now - self.decided_at >= self.cooldown):
            return self._fire(now, new_vehicle=False)
        return False

    def _change(self, frame, reference):
        """
        How different frame is from reference, on the presence scale. With the
        shadow filter, the lower of the brightness change and the texture
        change (rescaled so both meet at presence_threshold): something must
        be both darker or lighter and differently shaped to count.
        """
        change = difference(frame, reference)
        if not self.shadow_filter or change <= self.presence_threshold:
            # Below the threshold either way; skip the costlier texture measure
            return change
        texture = texture_difference(frame, reference, self.width)
        return min(change, texture * self.presence_threshold / self.texture_threshold)

    def _fire(self, now, new_vehicle, moving=False):
        if new_vehicle:
            self.attempts = 0
            self.occupied_since = now
        self.state = OCCUPIED
        self.attempts += 1
        self.awaiting_decision = True
        self.clear_since = None
        self.fired_while_moving = moving
        return True

    def decided(self, granted, now):
        """Report the outcome of the burst started by the last trigger."""
        self.awaiting_decision = False
        self.decided_at = now
        self.last_granted = bool(granted)
        self.decision_frame = self.frame
