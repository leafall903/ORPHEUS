"""Actual 0241 beam input and target outputs against Tracer."""
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes

ZARYA = 0x02E0000000000068


class TracerZaryaBeamTests(unittest.TestCase):
    def setUp(self):
        self.run, self.target, self.caster, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.match.switch_hero(self.caster, heroes()[ZARYA])
        self.fight = self.match.combat
        self.shown = next(target for target in self.fight._targets() if target.owner is self.target)
        self.vitals = self.fight.vitals_of(self.shown)
        self.weapon = next(i for i in self.caster.body_script.component.instances.values()
                           if i.graph.index == 0x0241)
        self.place(5)

    def place(self, distance):
        x, y, z = self.shown.feet
        self.caster.mover.position = [x, y, z + distance]
        self.caster.mover.body = None
        self.direction = aim(self.fight._eye(self.caster), (x, y + 0.9, z))

    def fire(self, frames=1, buttons=FIRE, direction=None):
        for _ in range(frames):
            self.run.step({self.caster: (*(self.direction if direction is None else direction), buttons)})

    def test_actual_ray_output_damage_and_release_reuse(self):
        self.fire(10)
        self.assertEqual(self.weapon.vars[536].value().id, self.target.body)
        self.assertTrue(self.weapon.states[43].ray_hit)
        self.assertEqual(self.weapon.vars[1648].value(), 15)
        self.assertAlmostEqual(self.vitals.total, 138)
        self.assertEqual(len(self.caster.client.messages(combat.HIT)), 10)
        self.fight.command(self.caster, Command(self.run.frame))
        self.fire(20, buttons=0)
        self.assertAlmostEqual(self.vitals.total, 138)
        self.fire(10)
        self.assertAlmostEqual(self.vitals.total, 126)

    def test_actual_ray_rejects_miss_range_team_and_wall(self):
        for reason in ("miss", "range", "team", "wall"):
            with self.subTest(reason=reason):
                self.setUp()
                direction = (0, 0) if reason == "miss" else None
                if reason == "range":
                    self.place(18)
                elif reason == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.caster.team = self.target.team
                elif reason == "wall":
                    x, y, z = self.shown.feet
                    self.match.collision_world = World(box(x - 5, x + 5, y, y + 5, z + 2, z + 2.1))
                self.fire(10, direction=direction)
                self.assertEqual(self.weapon.vars[536].value().id, 0)
                self.assertEqual(self.vitals.total, 150)

    def test_actual_blink_leaves_beam_and_clears_graph_target(self):
        self.fire(10)
        self.run.step({self.target: (16384, 0, 8), self.caster: (*self.direction, FIRE)})
        self.fire(10)
        self.assertEqual(self.weapon.vars[536].value().id, 0)
        self.assertAlmostEqual(self.vitals.total, 138)

    def test_actual_recall_rejects_beam_then_damage_resumes(self):
        self.run.step({self.target: (0, 0, 16)})
        self.assertTrue(tracer.recalling(self.target))
        self.fire(30)
        self.assertEqual(self.weapon.vars[536].value().id, 0)
        self.assertEqual(self.vitals.total, 150)
        for _ in range(80):
            if not tracer.recalling(self.target):
                break
            self.fire(buttons=0)
        self.assertFalse(tracer.recalling(self.target))
        self.fire(10)
        self.assertLess(self.vitals.total, 150)
