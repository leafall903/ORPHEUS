"""Actual Enforcer volleys traverse their individual authored ring rays."""

import unittest

import test_game_mercy_npc_geometry as geometry
from test_game_mover import FLOOR, wall

from ow174.game import npc_control
from ow174.game.collision import World


class MercyEnforcerFanTests(unittest.TestCase):
    def launch(self, distance):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        fixture = geo.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.name == "Enforcer")
        run, mercy, bot, _ = fixture.spawn(definition, 1)
        bot.position = (0, 1, distance)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        fixture.tick(run, mercy, bot, 1, buttons=0)
        self.assertEqual(npc_control.start(run.match, mercy), bot.entity)
        for _ in range(180):
            fixture.tick(run, mercy, bot, 1, buttons=0)
            if fight.rockets:
                break
        rays = [r for r in fight.rockets if r.shooter is bot]
        self.assertEqual(len(rays), 25)
        self.assertEqual(len({r.direction for r in rays}), 25)
        bot.firing = False  # Release input while preserving already-fired projectiles.
        return fixture, run, mercy, bot, health

    def test_actual_fan_hits_at_close_range_and_partly_misses_at_distance(self):
        for distance, expected in ((2, 150), (5, 150), (12, 176)):
            with self.subTest(distance=distance):
                fixture, run, mercy, bot, health = self.launch(distance)
                fixture.tick(run, mercy, bot, 36, buttons=0)
                self.assertEqual(health.total, expected)

    def test_wall_can_intercept_only_part_of_an_actual_volley(self):
        fixture, run, mercy, bot, health = self.launch(5)
        run.match.collision_world = World(FLOOR + wall(0.05, 2.5, 3, 2.5))
        fixture.tick(run, mercy, bot, 36, buttons=0)
        self.assertGreater(health.total, 150)
        self.assertLess(health.total, 200)
