import unittest

from PIL import Image, ImageDraw

from gate_agent.trigger import IDLE, MOTION, OCCUPIED, PresenceTrigger, difference, prepare


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


if __name__ == '__main__':
    unittest.main()
