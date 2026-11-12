"""Authored224E sampling time and real owner pose on the existing sleep host."""
import math
import unittest
from unittest.mock import patch

import test_ashe_sleep as fixtures
from ow174.game import combat
from ow174.game.script import graph


class SleepViewSourceClockTests(unittest.TestCase):
    def sleep(self):
        case = fixtures.AsheSleepTests()
        run, ana, target = case.arena()
        fight = run.match.combat
        case.step(run, ana, target, cast=8)
        for _ in range(60):
            if fight.is_asleep(target):
                break
            case.step(run, ana, target)
        self.assertTrue(fight.is_asleep(target))
        vitals = fight.vitals[target.body]
        return run, ana, target, vitals, vitals.sleep_wake_floor - combat.SLEEP_WAKE_FLOOR_TICKS

    def check_boundary(self, *, early_damage):
        for duration in (.15, .32):
            with self.subTest(duration=duration, early_damage=early_damage):
                timeout = graph.graph(0x224E).node(29).field('m_timeout')
                with patch.dict(timeout, {'m_value': duration}):
                    run, ana, target, vitals, start = self.sleep()
                    fight = run.match.combat
                    if early_damage:
                        fight._damage(ana, fight.player_target(target), 1, False, None, run.frame)
                        self.assertLess(vitals.sleep_until, start + combat.SLEEP_TICKS)
                    deadline = start + math.ceil(duration / combat.TICK)
                    while run.frame < deadline - 1:
                        run.step({target: (-16000, 6000, 0)})
                        self.assertIsNone(vitals.sleep_view)
                    run.step({target: (16000, -6000, 0)})
                    self.assertIsNotNone(vitals.sleep_view)
                    self.assertEqual((target.mover.yaw, target.mover.pitch), (-16000, 6000))
                    expiry = vitals.sleep_until
                    while run.frame < expiry - 1:
                        run.step({target: (16000, -6000, 0)})
                        self.assertIsNotNone(vitals.sleep_view)
                        self.assertEqual((target.mover.yaw, target.mover.pitch), (-16000, 6000))
                    run.step({target: (16000, -6000, 0)})
                    self.assertIsNone(vitals.sleep_view)
                    self.assertEqual((target.mover.yaw, target.mover.pitch), (16000, -6000))

    def test_original_wait_and_explicit_authored_delay_drive_actual_sampling(self):
        self.check_boundary(early_damage=False)

    def test_shortened_wake_deadline_does_not_move_the_original_sampling_start(self):
        self.check_boundary(early_damage=True)
