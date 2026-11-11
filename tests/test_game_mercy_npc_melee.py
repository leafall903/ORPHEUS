"""Actual Trooper and Heavy Assault manual melee against Mercy."""

import unittest

import test_game_mercy_npc_geometry as geometry
from test_game_mover import FLOOR, wall

from ow174.game import npc_control
from ow174.game.collision import World


class MercyNpcMeleeTests(unittest.TestCase):
    def encounter(self, name, distance=2):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        fixture = geo.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.name == name)
        run, mercy, bot, _ = fixture.spawn(definition, 1)
        bot.position = (0, 1, distance)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        fixture.tick(run, mercy, bot, 12, buttons=0)
        self.assertEqual(npc_control.start(run.match, mercy, melee=True), bot.entity)
        return fixture, run, mercy, bot, health

    def test_actual_melee_hits_once_per_hold_for_both_npcs(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(npc=name):
                fixture, run, mercy, bot, health = self.encounter(name)
                fixture.tick(run, mercy, bot, 62, buttons=0)
                self.assertEqual(health.total, 200 - damage)
                if name == "Heavy Assault":
                    self.assertLess(mercy.mover.position[2], 0)
                else:
                    self.assertEqual(mercy.mover.position[2], 0)
                self.assertEqual(run.match.combat.rockets, [])
                npc_control.stop(run.match)
                fixture.tick(run, mercy, bot, 22, buttons=0)
                self.assertGreaterEqual(health.total, 200 - damage)

    def test_wall_and_out_of_range_prevent_both_actual_melees(self):
        for name in ("Trooper", "Heavy Assault"):
            for blocked in ("wall", "range"):
                with self.subTest(npc=name, blocked=blocked):
                    fixture, run, mercy, bot, health = self.encounter(name, 8 if blocked == "range" else 2)
                    if blocked == "wall":
                        run.match.collision_world = World(FLOOR + wall(-4, 1, 4, 1))
                    fixture.tick(run, mercy, bot, 62, buttons=0)
                    self.assertEqual(health.total, 200)
                    self.assertEqual(run.match.combat.rockets, [])
