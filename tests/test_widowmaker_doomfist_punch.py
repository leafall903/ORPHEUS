"""Actual Doomfist Rocket Punch against Widowmaker in build 104319."""

import unittest

from test_game_combat import aim
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import doomfist_punch
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.expr import Entity


class WidowmakerDoomfistPunchTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[doomfist_punch.HERO]))
        self.a.mover.position[:] = (0, 0, 6)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat
        self.punches = self.fight.punches

    def wall(self, z):
        self.run.match.collision_world = World([
            (-40, 0, -40, -40, 0, 40, 40, 0, 40),
            (-40, 0, -40, 40, 0, 40, 40, 0, -40),
            (-5, 0, z, 5, 0, z, 5, 4, z),
            (-5, 0, z, 5, 4, z, -5, 4, z),
        ])

    def cast(self, frames=1):
        for _ in range(frames):
            self.run.step({self.b: (0, 0, SCOPE)})
        self.run.step({})

    def wait_for_contact(self):
        for _ in range(90):
            if self.health(self.a) < 175:
                return
            self.run.step({})
        instance = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x0D06)
        self.fail(str((self.b.mover.position, self.punches.dashes, instance.state(7).counter)))

    def test_actual_minimum_punch_deals_fifty_moves_and_blocks_fire(self):
        self.cast()
        self.wait_for_contact()
        self.assertEqual(self.health(self.a), 125)
        self.assertTrue(self.fight.is_stunned(self.a))
        doom = self.fight.vitals_of(self.target(self.b))
        self.assertAlmostEqual(doom.temporary_shields[doomfist_punch.SHIELD_TAG].now, 30)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.press(FIRE, 4)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertGreater(self.a.mover.position[2], 6.1)
        self.press(0, 60)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        self.assertEqual(self.health(self.a), 125)
        self.assertAlmostEqual(doom.temporary_shields[doomfist_punch.SHIELD_TAG].now, 17, delta=0.2)
        self.press(0, 230)
        self.assertLess(doom.temporary_shields[doomfist_punch.SHIELD_TAG].now, 17)

    def test_actual_wall_contact_applies_one_bonus(self):
        self.wall(7.15)
        self.cast()
        self.wait_for_contact()
        for _ in range(70):
            self.run.step({})
        self.assertEqual(self.health(self.a), 75, self.a.mover.position)
        self.assertFalse(self.punches.impacts)
        self.assertLess(self.a.mover.position[2], 7.15)

    def test_actual_full_charge_hits_once_for_one_hundred(self):
        self.cast(90)
        self.wait_for_contact()
        self.assertEqual(self.health(self.a), 75)
        for _ in range(90):
            self.run.step({})
        self.assertEqual(self.health(self.a), 75)
        self.assertTrue(self.punches.dashes[self.b.body].finished)

    def test_full_charge_wall_bonus_kills_once_and_clears_impact(self):
        self.wall(7.15)
        self.cast(90)
        self.wait_for_contact()
        for _ in range(80):
            self.run.step({})
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertFalse(self.punches.impacts)
        self.assertFalse(self.fight.is_stunned(self.a))

    def test_primary_cancel_starts_cooldown_and_new_press_can_hit_after_expiry(self):
        start = tuple(self.b.mover.position)
        for _ in range(20):
            self.run.step({self.b: (0, 0, SCOPE)})
        self.run.step({self.b: (0, 0, FIRE)})
        for _ in range(45):
            self.run.step({})
        graph = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x0D06)
        self.assertEqual(tuple(self.b.mover.position), start)
        self.assertFalse(self.punches.dashes)
        self.assertGreater(graph.state(0).remaining(), 0)
        self.cast(20)
        for _ in range(270):
            self.run.step({})
        self.assertFalse(self.punches.dashes)
        self.assertEqual(graph.state(0).remaining(), 0)
        self.assertEqual(self.health(self.a), 175)
        self.cast()
        self.wait_for_contact()
        self.assertEqual(self.health(self.a), 125)

    def test_wall_before_target_rejects_hit(self):
        self.wall(4)
        self.cast()
        for _ in range(95):
            self.run.step({})
        self.assertTrue(self.punches.dashes[self.b.body].finished)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.is_stunned(self.a))



class WidowmakerDoomfistBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_absorbs_punch_without_control_or_passive_grant(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[doomfist_punch.HERO]))
        self.b.mover.position[:] = (0, 0, 6)
        self.b.mover.body = None
        fight = self.run.match.combat
        self.activate()
        yaw, _ = aim(fight._eye(self.b), fight._eye(self.a))
        self.run.step({self.b: (yaw, 0, SCOPE)})
        for _ in range(55):
            self.run.step({self.b: (yaw, 0, 0)})
        self.assertEqual(self.barrier().vars[536].value(), Entity(self.a.body))
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 150)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(fight.is_stunned(self.a))
        self.assertFalse(fight.vitals_of(self.target(self.b)).temporary_shields)
        self.assertEqual(tuple(self.a.mover.position), (0, 0, 0))
