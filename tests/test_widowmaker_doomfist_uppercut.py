"""Actual Doomfist Uppercut against Widowmaker in build 104319."""

import unittest

from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import doomfist_punch, doomfist_uppercut
from ow174.game.collision import World
from ow174.game.content import heroes


class WidowmakerUppercutTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[doomfist_punch.HERO]))
        self.a.mover.position[:] = (0, 0, 1.5)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat

    def uppercut(self, yaw=0):
        self.run.step({self.b: (yaw, 0, 8)})
        highest = {self.a: 0, self.b: 0}
        for _ in range(90):
            self.run.step({self.b: (yaw, 0, 0)})
            for player in highest:
                highest[player] = max(highest[player], player.mover.position[1])
        return highest

    def test_actual_uppercut_hits_once_lifts_and_lands_both_bodies(self):
        highest = self.uppercut()
        self.assertEqual(self.health(self.a), 125, self.fight.uppercuts.seen)
        self.assertGreater(highest[self.a], 0.5)
        self.assertGreater(highest[self.b], 0.5)
        for _ in range(140):
            self.run.step({})
        self.assertAlmostEqual(self.a.mover.position[1], 0, delta=0.01)
        self.assertAlmostEqual(self.b.mover.position[1], 0, delta=0.01)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        before = weapon.vars[53].value()
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), before - 1)

    def test_repress_during_cooldown_rejects_then_new_press_hits(self):
        self.uppercut()
        self.assertEqual(self.health(self.a), 125)
        instance = next(i for i in self.b.body_script.component.instances.values()
                        if i.graph.index == doomfist_uppercut.GRAPH)
        activation = instance.state(9).counter
        self.assertGreater(instance.state(0).remaining(), 0)
        self.uppercut()
        self.assertEqual(instance.state(9).counter, activation)
        self.assertEqual(self.health(self.a), 125)
        for _ in range(450):
            self.run.step({})
        self.assertEqual(instance.state(0).remaining(), 0)
        self.a.mover.position[:] = (0, 0, 1.5)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.uppercut()
        self.assertEqual(instance.state(9).counter, activation + 1)
        self.assertEqual(self.health(self.a), 75)

    def test_wall_rejects_actual_uppercut(self):
        self.run.match.collision_world = World([
            (-40, 0, -40, -40, 0, 40, 40, 0, 40),
            (-40, 0, -40, 40, 0, 40, 40, 0, -40),
            (-5, 0, 0.75, 5, 0, 0.75, 5, 6, 0.75),
            (-5, 0, 0.75, 5, 6, 0.75, -5, 6, 0.75),
        ])
        self.uppercut()
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.vitals_of(self.target(self.b)).temporary_shields)

    def test_miss_and_allies_reject_damage_but_keep_graph_cooldown(self):
        for mode in ('rear', 'range', 'ally'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'range':
                    self.a.mover.position[2] = 8
                elif mode == 'ally':
                    self.a.team = self.b.team
                self.uppercut(-32768 if mode == 'rear' else 0)
                self.assertEqual(self.health(self.a), 175)
                self.assertFalse(self.fight.vitals_of(self.target(self.b)).temporary_shields)
                instance = next(i for i in self.b.body_script.component.instances.values()
                                if i.graph.index == doomfist_uppercut.GRAPH)
                self.assertGreater(instance.state(0).remaining(), 0)



class WidowmakerUppercutBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_absorbs_uppercut_without_lift_or_passive_grant(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[doomfist_punch.HERO]))
        self.b.mover.position[:] = (0, 0, 1.5)
        self.c.mover.position[:] = (-8, 0, 0)
        self.b.mover.body = self.c.mover.body = None
        fight = self.run.match.combat
        self.activate()
        self.run.step({self.b: (-32768, 0, 8)})
        for _ in range(70):
            self.run.step({self.b: (-32768, 0, 0)})
            self.assertAlmostEqual(self.a.mover.position[1], 0, delta=0.01)
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 150)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(fight.vitals_of(self.target(self.b)).temporary_shields)
