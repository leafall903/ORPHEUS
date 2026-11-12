"""Actual 00FE input against Tracer under the reconstructed Tesla cone."""
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_winston_barrier as barrier_tests

from ow174.game import combat, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes

WINSTON = 0x02E0000000000009


class TracerTeslaTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.winston, _ = tracer_tests.TracerPistolTests().fight()
        self.fight = self.run.match.combat
        self.run.match.switch_hero(self.winston, heroes()[WINSTON])
        self.place(5)
        self.target = next(target for target in self.fight._targets() if target.owner is self.tracer)
        self.vitals = self.fight.vitals_of(self.target)

    def place(self, distance):
        x, y, z = self.tracer.mover.position
        barrier_tests.TracerWinstonBarrierTests.move(self.winston, (x, y, z + distance))

    def fire(self, buttons=FIRE, yaw=-32768, tracer_buttons=0):
        self.run.step({self.winston: (yaw, 0, buttons), self.tracer: (0, 0, tracer_buttons)})

    def test_real_tesla_ticks_release_duplicate_and_fresh_press(self):
        for _ in range(10):
            self.fire()
        self.assertAlmostEqual(self.vitals.total, 150 - 10 * .96)
        self.assertEqual(len(self.winston.client.messages(combat.HIT)), 10)
        self.fight.command(self.winston, Command(self.run.frame, yaw=-32768, buttons=FIRE))
        self.assertAlmostEqual(self.vitals.total, 150 - 10 * .96)
        for _ in range(5):
            self.fire(0)
        self.assertAlmostEqual(self.vitals.total, 150 - 10 * .96)
        # The actual release subtree still gates an early re-press.
        for _ in range(7):
            self.fire()
            self.assertAlmostEqual(self.vitals.total, 150 - 10 * .96)
        self.fire()
        self.assertAlmostEqual(self.vitals.total, 150 - 11 * .96)
        old = self.winston.body
        self.run.match.switch_hero(self.winston, heroes()[WINSTON])
        self.assertNotIn(old, self.fight.tesla_frames)

    def test_range_rear_and_friendly_reject_real_tesla_input(self):
        self.place(12)
        self.fire()
        self.place(5)
        self.fire(yaw=0)
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.winston.team = self.tracer.team
        self.fire()
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.winston.client.messages(combat.HIT))

    def test_triangle_wall_blocks_tesla(self):
        from test_game_mover import wall

        x, y, z = self.tracer.mover.position
        floor = [(-100, y, -100, 100, y, 100, 100, y, -100),
                 (-100, y, -100, -100, y, 100, 100, y, 100)]
        world = World(floor + wall(x - 4, z + 2, x + 4, z + 2, y, y + 4))
        self.run.match.collision_world = world
        self.fire()
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.winston.client.messages(combat.HIT))

    def test_actual_recall_rejects_tesla_then_damage_resumes(self):
        self.fire(0, tracer_buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        for _ in range(80):
            self.fire()
            if not tracer.recalling(self.tracer):
                break
            self.assertEqual(self.vitals.total, 150)
        self.assertFalse(tracer.recalling(self.tracer))
        self.fire(0)
        self.fire()
        self.assertLess(self.vitals.total, 150)

    def test_actual_enemy_bubble_blocks_tesla_once_for_multiple_targets(self):
        fixture = barrier_tests.TracerWinstonBarrierTests()
        run, target, owner, shooter = fixture.arena()
        run.match.switch_hero(shooter, heroes()[WINSTON])
        shooter.team, target.team = 0, owner.team
        fixture.move(target, (0, 1, 2))
        fixture.move(shooter, (0, 1, 7))
        bubble = fixture.cast(run, owner)
        run.step({shooter: (-32768, 0, FIRE)})
        self.assertAlmostEqual(bubble.health, 700 - .96)
        self.assertEqual(fixture.health(run, target).total, 150)
        self.assertEqual(fixture.health(run, owner).total, 500)
        self.assertFalse(shooter.client.messages(combat.HIT))
