"""Actual Winston Jump Pack and Reinhardt's damage, shield and control paths."""

import unittest

import test_game_reinhardt as fixtures
import test_reinhardt_winston_tesla as tesla_fixture

from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.winston_jump import GRAPH


class ReinhardtJumpPackTests(unittest.TestCase):
    setUp = tesla_fixture.ReinhardtTeslaTests.setUp
    step = tesla_fixture.ReinhardtTeslaTests.step
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance
    shield = fixtures.BarrierTests.shield

    def jump(self, *, yaw=-32768, pitch=-16384, rein=0):
        self.run.step({self.rein: (0, 0, rein), self.enemy: (yaw, pitch, 8)})

    def instance(self):
        return next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == GRAPH)

    def land(self, *, rein=0):
        for _ in range(170):
            self.step(fire=0, rein=rein)
            if self.enemy.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.enemy.body, self.fight.winston_jump.flights)
        self.assertFalse(self.instance().states[2].active)

    def test_actual_takeoff_damages_armour_once_and_launch_is_deduplicated(self):
        self.place(self.enemy, (0, 1, 2))
        self.jump()
        self.assertEqual(self.health(self.rein), 499.5)
        self.assertGreater(self.instance().states[19].remaining(), 5.9)
        self.assertAlmostEqual(self.enemy.mover.velocity[1], 30)
        velocity = self.enemy.mover.velocity
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.enemy, Command(frame, buttons=8, pitch=-16384))
        self.assertEqual(self.enemy.mover.velocity, velocity)
        self.assertEqual(self.health(self.rein), 499.5)

    def test_real_landing_damages_armour_once_and_uses_reinhardt_knockback_scalar(self):
        self.jump()
        self.assertEqual(self.health(self.rein), 500)
        self.place(self.rein, (0, 1, 5))
        self.land()
        self.assertLess(self.health(self.rein), 475)
        self.assertGreater(self.health(self.rein), 455)
        self.assertTrue(self.rein.mover.impulse_active)
        state = self.instance().states[3]
        horizontal = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
        scale = (500 - self.health(self.rein) + 5) / 50
        self.assertAlmostEqual(self.rein.mover.velocity[2], -horizontal * scale * .7, delta=.01)
        after = self.health(self.rein)
        self.step(15, fire=0)
        self.assertEqual(self.health(self.rein), after)

    def test_actual_barrier_intercepts_takeoff_and_landing_without_body_impulse(self):
        self.place(self.enemy, (0, 1, 3))
        self.step(15, fire=0, rein=2)
        self.jump(rein=2)
        shield = self.shield()
        self.assertEqual(shield.current, 1599)
        self.assertEqual(self.health(self.rein), 500)
        self.land(rein=2)
        self.assertLess(shield.current, 1599)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(self.rein.mover.impulse_active)

    def test_landing_breaks_barrier_without_damage_or_impulse_behind_it(self):
        self.place(self.enemy, (0, 1, 3))
        self.place(self.other, (0.4, 1, -1))
        self.step(15, fire=0, rein=2)
        self.jump(rein=2)
        shield = self.shield()
        shield.current = 5
        self.land(rein=2)
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.rein.mover.impulse_active)
        self.assertFalse(self.other.mover.impulse_active)

    def test_takeoff_rejects_wall_team_and_range(self):
        for case in ("wall", "team", "range"):
            with self.subTest(case=case):
                if case != "wall":
                    self.setUp()
                if case == "wall":
                    self.place(self.enemy, (0, 1, 3))
                    fixtures.charge_wall(self, 1.5)
                elif case == "team":
                    self.place(self.enemy, (0, 1, 3))
                    self.enemy.team = self.rein.team
                self.jump()
                self.assertIn(self.enemy.body, self.fight.winston_jump.flights)
                self.assertEqual(self.health(self.rein), 500)

    def test_actual_earthshatter_blocks_cast_until_control_ends(self):
        self.rein.body_script.set_ult_charge(1540)
        self.step(fire=0, rein=32)
        for _ in range(65):
            if self.fight.controlled(self.enemy.body):
                break
            self.step(fire=0)
        self.assertTrue(self.fight.controlled(self.enemy.body))
        self.jump()
        self.assertNotIn(self.enemy.body, self.fight.winston_jump.flights)
        self.step(185, fire=0)
        self.assertFalse(self.fight.controlled(self.enemy.body))
        self.jump()
        self.assertIn(self.enemy.body, self.fight.winston_jump.flights)

    def test_authored_cooldown_rejects_early_press_and_allows_fresh_jump(self):
        self.jump()
        ability = self.instance().states[19]
        first = ability.last
        self.land()
        self.jump()
        self.assertEqual(ability.last, first)
        self.assertNotIn(self.enemy.body, self.fight.winston_jump.flights)
        self.step(320, fire=0)
        self.assertEqual(ability.remaining(), 0)
        self.jump()
        self.assertGreater(ability.last, first)
        self.assertIn(self.enemy.body, self.fight.winston_jump.flights)


    def test_actual_fire_strike_kills_airborne_winston_and_cancels_landing(self):
        self.place(self.enemy, (0, 1, 4))
        target = self.fight._target_entity(self.enemy.body)
        self.fight.vitals_of(target).damage(450)
        self.step(fire=0, rein=16)
        for _ in range(60):
            self.step(fire=0)
            if any(rocket.position[2] >= 2.0 for rocket in self.fight.rockets):
                break
        self.assertFalse(self.fight.is_dead(self.enemy))
        self.assertTrue(self.fight.rockets)
        self.jump()
        self.assertIn(self.enemy.body, self.fight.winston_jump.flights)
        self.assertTrue(self.enemy.mover.airborne)
        self.step(8, fire=0)
        self.assertTrue(self.fight.is_dead(self.enemy))
        self.assertNotIn(self.enemy.body, self.fight.winston_jump.flights)
        self.assertFalse(self.instance().states[2].active)
        before = self.health(self.rein)
        self.place(self.rein, (0, 1, 4))
        self.step(125, fire=0)
        self.assertEqual(self.health(self.rein), before)

    def test_hero_replacement_removes_pending_landing_and_cast_token(self):
        self.jump()
        old = self.enemy.body
        instance = self.instance()
        self.assertIn(old, self.fight.winston_jump.flights)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        self.assertNotIn(old, self.fight.winston_jump.flights)
        self.assertNotIn(old, self.fight.winston_jump.seen)
        self.assertFalse(instance.states[2].active)
        self.place(self.rein, (0, 1, 6))
        self.step(125, fire=0)
        self.assertEqual(self.health(self.rein), 500)
