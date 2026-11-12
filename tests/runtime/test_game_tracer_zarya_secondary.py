"""Actual Particle Cannon secondary against Tracer and her defenses."""

import unittest
from dataclasses import replace

import test_game_tracer_zarya_barrier as barrier_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, tracer
from ow174.game.collision import World
from ow174.game.script import expr


class TracerZaryaSecondaryTests(unittest.TestCase):
    def setUp(self):
        self.case = barrier_tests.TracerZaryaBarrierTests()
        self.case.setUp()
        self.case.place(self.case.zarya, 0, 6)
        self.run = self.case.run
        self.fight = self.case.fight
        self.tracer = self.case.tracer
        self.zarya = self.case.zarya
        self.health = self.case.health(self.tracer)
        x, y, z = self.tracer.mover.position
        self.aim = aim(self.fight._eye(self.zarya), (x, y + 1, z))

    def step(self, *, fire=0, buttons=0, yaw=0):
        self.run.step({self.zarya: (*self.aim, fire), self.tracer: (yaw, 0, buttons)})

    def launch(self):
        self.step(fire=2)
        for _ in range(55):
            if self.fight.rockets:
                return self.fight.rockets[0]
            self.step()
        self.fail("Particle grenade did not launch")

    def settle(self):
        for _ in range(125):
            if not self.fight.rockets:
                return
            self.step()
        self.fail("Particle grenade did not end")

    def test_actual_gravity_and_single_splash_hit(self):
        rocket = self.launch()
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.speed, 25)
        before = rocket.velocity
        self.step()
        self.assertEqual(rocket.velocity[1], expr.f32(before[1] - expr.f32(expr.f32(9.8) * expr.f32(0.016))))
        self.settle()
        self.assertEqual(self.health.total, 103)
        self.assertEqual(len(self.zarya.client.messages(combat.HIT)), 1)
        for _ in range(25):
            self.step()
        self.assertEqual(self.health.total, 103)

    def test_blink_evades_grenade_after_launch(self):
        self.launch()
        old = self.tracer.mover.position[0]
        self.step(buttons=8, yaw=16384)
        self.assertGreater(self.tracer.mover.position[0] - old, 6)
        self.settle()
        self.assertEqual(self.health.total, 150)

    def test_recall_protects_tracer_when_wall_impact_explodes_in_range(self):
        self.wall(behind=True)
        self.launch()
        self.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.settle()
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)

    def wall(self, *, behind=False):
        x, y, z = self.tracer.mover.position
        z += -0.75 if behind else 1
        world = World(box(x - 6, x + 6, y - 1, y + 4, z, z + 0.2))
        self.run.match.collision_world = world

    def test_wall_and_ally_reject_damage(self):
        self.wall()
        self.launch()
        self.settle()
        self.assertEqual(self.health.total, 150)
        self.setUp()
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team = self.zarya.team
        self.launch()
        self.settle()
        self.assertEqual(self.health.total, 150)
