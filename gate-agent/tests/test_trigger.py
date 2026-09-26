import random
import unittest

from PIL import Image, ImageDraw, ImageFilter

from gate_agent.trigger import (
    IDLE, MOTION, OCCUPIED, PresenceTrigger, difference, prepare, texture_difference,
)


def lane(car_x=None, car_color=40, person_x=None):
    """A 320x180 grey lane; optionally a car block and/or a narrow 'person'."""
    image = Image.new('RGB', (320, 180), (150, 150, 150))
    draw = ImageDraw.Draw(image)
    if car_x is not None:
        draw.rectangle([car_x, 40, car_x + 200, 170], fill=(car_color, car_color, car_color))
    if person_x is not None:
        draw.rectangle([person_x, 60, person_x + 12, 170], fill=(30, 30, 30))
    return prepare(image)


class Clock:
    def __init__(self):
        self.t = 0.0

    def tick(self, dt=0.4):
        self.t += dt
        return self.t


def feed(trigger, clock, frames, dt=0.4):
    fired = []
    for frame in frames:
        if trigger.update(frame, clock.tick(dt)):
            fired.append(clock.t)
    return fired


class TriggerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.trigger = PresenceTrigger(motion_threshold=0.02, settle_ms=800, cooldown_s=5, max_attempts=2)
        feed(self.trigger, self.clock, [lane()] * 5)

    def arrive(self):
        # Car drives in from the left and stops at x=60
        return feed(self.trigger, self.clock, [lane(car_x=x) for x in (-150, -80, -10, 60)] + [lane(car_x=60)] * 4)

    def test_empty_lane_never_fires(self):
        self.assertEqual(feed(self.trigger, self.clock, [lane()] * 50), [])
        self.assertEqual(self.trigger.state, IDLE)

    def test_vehicle_arrives_and_settles_fires_once(self):
        fired = self.arrive()
        self.assertEqual(len(fired), 1)
        self.assertEqual(self.trigger.state, OCCUPIED)
        self.assertTrue(self.trigger.awaiting_decision)
        # While awaiting the decision nothing else fires
        self.assertEqual(feed(self.trigger, self.clock, [lane(car_x=60)] * 10), [])

    def test_does_not_fire_while_still_moving(self):
        fired = feed(self.trigger, self.clock, [lane(car_x=x) for x in range(-160, 160, 40)])
        self.assertEqual(fired, [])
        self.assertEqual(self.trigger.state, MOTION)

    def test_slow_creep_counts_as_settled(self):
        # A car creeping a few pixels per frame looks stopped; reading it then is fine
        fired = feed(self.trigger, self.clock, [lane(car_x=x) for x in range(-150, 60, 5)])
        self.assertEqual(len(fired), 1)

    def test_pedestrian_passing_does_not_fire(self):
        frames = [lane(person_x=x) for x in range(0, 320, 40)] + [lane()] * 6
        self.assertEqual(feed(self.trigger, self.clock, frames), [])
        self.assertEqual(self.trigger.state, IDLE)

    def test_granted_vehicle_does_not_refire_then_lane_clears(self):
        self.arrive()
        self.trigger.decided(True, self.clock.t)
        self.assertEqual(feed(self.trigger, self.clock, [lane(car_x=60)] * 30), [])
        feed(self.trigger, self.clock, [lane(car_x=x) for x in (120, 200, 300)] + [lane()] * 5)
        self.assertEqual(self.trigger.state, IDLE)
        self.assertEqual(len(self.arrive()), 1)

    def test_denied_vehicle_retried_after_cooldown_up_to_max(self):
        self.arrive()
        self.trigger.decided(False, self.clock.t)
        # Before the cooldown: nothing
        self.assertEqual(feed(self.trigger, self.clock, [lane(car_x=60)] * 5), [])
        # After the cooldown: one retry (attempt 2 of 2)
        fired = feed(self.trigger, self.clock, [lane(car_x=60)] * 10)
        self.assertEqual(len(fired), 1)
        self.trigger.decided(False, self.clock.t)
        self.assertEqual(feed(self.trigger, self.clock, [lane(car_x=60)] * 40), [])

    def test_different_vehicle_replacing_the_first_fires(self):
        self.arrive()
        self.trigger.decided(True, self.clock.t)
        # First car leaves right while the next pulls in, never clearing the lane
        frames = [lane(car_x=90), lane(car_x=120, car_color=200)] + [lane(car_x=30, car_color=210)] * 5
        fired = feed(self.trigger, self.clock, frames)
        self.assertEqual(len(fired), 1)
        self.assertEqual(self.trigger.attempts, 1)

    def test_parked_object_becomes_background(self):
        trigger = PresenceTrigger(motion_threshold=0.02, settle_ms=800, cooldown_s=5,
                                  max_attempts=1, max_occupied_seconds=10)
        clock = Clock()
        feed(trigger, clock, [lane()] * 3)
        feed(trigger, clock, [lane(car_x=60)] * 4)
        trigger.decided(False, clock.t)
        feed(trigger, clock, [lane(car_x=60)] * 40)
        self.assertEqual(trigger.state, IDLE)
        self.assertLess(difference(trigger.background, lane(car_x=60)), 0.01)

    def test_background_adapts_to_slow_light_change(self):
        for level in range(150, 170):
            image = Image.new('RGB', (320, 180), (level, level, level))
            self.trigger.update(prepare(image), self.clock.tick())
            self.trigger.update(prepare(image), self.clock.tick())
        self.assertEqual(self.trigger.state, IDLE)

    def test_presence_threshold_floor(self):
        self.assertEqual(PresenceTrigger(motion_threshold=0.001).presence_threshold, 0.04)
        self.assertAlmostEqual(PresenceTrigger(motion_threshold=0.05).presence_threshold, 0.15)

    def test_prepare_keeps_aspect(self):
        thumb = prepare(Image.new('RGB', (1920, 1080)))
        self.assertEqual(thumb.size, (64, 36))
        self.assertEqual(thumb.mode, 'L')
        black, white = prepare(Image.new('RGB', (640, 360))), prepare(Image.new('RGB', (640, 360), 'white'))
        self.assertAlmostEqual(difference(black, white), 1.0)
        self.assertEqual(difference(black, black), 0.0)
        self.assertEqual(difference(black, prepare(Image.new('RGB', (100, 100)))), 1.0)


def road(seed=1):
    """A 320x180 asphalt lane: grey with coarse grain, like a real road surface."""
    rng = random.Random(seed)
    image = Image.new('L', (320, 180))
    image.putdata([max(0, min(255, int(rng.gauss(140, 18)))) for _ in range(320 * 180)])
    # Grain a few pixels across, so it survives prepare()'s downscale
    return image.filter(ImageFilter.BoxBlur(2)).convert('RGB')


def with_shadow(image, left, right, strength=0.45):
    """The lane with a soft-edged shadow across columns left..right."""
    mask = Image.new('L', image.size, 0)
    ImageDraw.Draw(mask).rectangle([left, 0, right, image.size[1]], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(6))
    return Image.composite(image.point(lambda v: int(v * (1 - strength))), image, mask)


def with_car(image, x):
    """The lane with the back of a car at x: body, rear window, lights, bumper and plate."""
    car = image.copy()
    draw = ImageDraw.Draw(car)
    draw.rectangle([x, 50, x + 150, 170], fill=(55, 60, 70))              # body
    draw.rectangle([x + 20, 58, x + 130, 95], fill=(20, 25, 30))          # rear window
    draw.rectangle([x + 5, 105, x + 30, 120], fill=(200, 30, 30))         # lights
    draw.rectangle([x + 120, 105, x + 145, 120], fill=(200, 30, 30))
    draw.rectangle([x + 55, 125, x + 95, 145], fill=(235, 235, 235))      # plate
    draw.rectangle([x, 150, x + 150, 165], fill=(15, 15, 15))             # bumper
    return car


class ShadowFilterTest(unittest.TestCase):
    """A shadow darkens the lane without bringing anything into it."""

    def trigger(self, shadow_filter=True):
        return PresenceTrigger(motion_threshold=0.02, settle_ms=800, cooldown_s=5, max_attempts=2,
                               shadow_filter=shadow_filter)

    def setUp(self):
        self.clock = Clock()
        self.lane = road()

    def settle(self, trigger, frames):
        feed(trigger, self.clock, [prepare(self.lane)] * 5)
        return feed(trigger, self.clock, [prepare(f) for f in frames])

    def sweep(self):
        # A shadow slides in from the left and stays, as a cloud or a building's does
        return [with_shadow(self.lane, 0, right) for right in (40, 90, 140, 190)] + \
            [with_shadow(self.lane, 0, 190)] * 6

    def test_texture_ignores_a_change_of_light(self):
        lane = prepare(self.lane)
        width = lane.size[0]
        darker = prepare(self.lane.point(lambda v: int(v * 0.6)))
        self.assertLess(texture_difference(darker, lane, width), 0.01)
        self.assertLess(texture_difference(prepare(with_shadow(self.lane, 0, 190)), lane, width), 0.025)
        self.assertGreater(texture_difference(prepare(with_car(self.lane, 80)), lane, width), 0.025)
        # Frames of different sizes cannot be compared
        self.assertEqual(texture_difference(lane, prepare(Image.new('RGB', (100, 100))), width), 1.0)

    def test_a_shadow_is_taken_for_a_vehicle_without_the_filter(self):
        self.assertEqual(len(self.settle(self.trigger(shadow_filter=False), self.sweep())), 1)

    def test_a_shadow_does_not_fire_with_the_filter(self):
        trigger = self.trigger()
        self.assertEqual(self.settle(trigger, self.sweep()), [])
        # Once it has stopped moving the lane is idle again, and the shadow is learned
        self.assertEqual(trigger.state, IDLE)

    def test_a_vehicle_still_fires_with_the_filter(self):
        frames = [with_car(self.lane, x) for x in (-120, -60, 0, 60)] + [with_car(self.lane, 60)] * 5
        self.assertEqual(len(self.settle(self.trigger(), frames)), 1)

    def test_a_vehicle_in_the_shade_still_fires(self):
        shaded = with_shadow(self.lane, 0, 320)
        frames = [with_car(shaded, x) for x in (-120, -60, 0, 60)] + [with_car(shaded, 60)] * 5
        self.assertEqual(len(self.settle(self.trigger(), frames)), 1)

    def test_a_shadow_moving_over_a_waiting_vehicle_is_not_a_new_vehicle(self):
        trigger = self.trigger()
        car = with_car(self.lane, 60)
        self.settle(trigger, [with_car(self.lane, x) for x in (-60, 0, 60)] + [car] * 5)
        trigger.decided(True, self.clock.t)
        frames = [with_shadow(car, 0, right) for right in (60, 120, 180)] + [with_shadow(car, 0, 180)] * 6
        self.assertEqual(feed(trigger, self.clock, [prepare(f) for f in frames]), [])
        self.assertEqual(trigger.attempts, 1)


class ShadowFilterSettingsTest(unittest.TestCase):
    def test_on_by_default_and_switchable(self):
        from unittest import mock

        from gate_agent.config import AgentSettings

        with mock.patch.dict('os.environ', {}, clear=True):
            settings = AgentSettings.from_env()
        self.assertTrue(settings.shadow_filter)
        self.assertEqual(settings.shadow_texture_threshold, 0.025)
        self.assertEqual(settings.moving_read_seconds, 1.0)
        for value, expected in (('false', False), ('0', False), ('off', False), ('true', True), ('', True)):
            with mock.patch.dict('os.environ', {'AGENT_SHADOW_FILTER': value,
                                                'AGENT_SHADOW_TEXTURE_THRESHOLD': '0.03'}, clear=True):
                settings = AgentSettings.from_env()
            self.assertEqual(settings.shadow_filter, expected, value)
            self.assertEqual(settings.shadow_texture_threshold, 0.03)


if __name__ == '__main__':
    unittest.main()
