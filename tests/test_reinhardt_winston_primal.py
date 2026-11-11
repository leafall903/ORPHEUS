"""Actual Winston Primal Rage against Reinhardt's combat states."""

import unittest
from itertools import pairwise

import test_game_reinhardt as fixtures
import test_reinhardt_winston_tesla as tesla_fixture

from ow174.game.commands import Command
from ow174.game.script.driver import HUD_MAX_HEALTH, ULT_CHARGE, ULT_COST
from ow174.game.winston_jump import GRAPH as JUMP_GRAPH
from ow174.game.world import ARMOUR_POOL


class ReinhardtPrimalTests(unittest.TestCase):
    setUp = tesla_fixture.ReinhardtTeslaTests.setUp
    step = tesla_fixture.ReinhardtTeslaTests.step
    weapon = tesla_fixture.ReinhardtTeslaTests.weapon
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance
    shield = fixtures.BarrierTests.shield

    def rage(self, *, rein=0):
        script = self.enemy.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertGreater(cost, 0)
        script.set_ult_charge(cost)
        self.step(fire=32, rein=rein)
        self.assertIsNotNone(self.fight.winston_primal.health_state(self.enemy))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 1000)
        self.assertEqual(self.health(self.enemy), 1000)

    def punch(self, *, fire=1, rein=0):
        for _ in range(80):
            self.step(fire=fire, rein=rein)
            if self.health(self.rein) < 500:
                break

    def test_actual_ultimate_refills_and_real_fire_strike_damage_survives_idle_expiry(self):
        target = self.fight._target_entity(self.enemy.body)
        self.fight.vitals_of(target).damage(300)
        self.rage()
        self.step(fire=0, rein=16)
        self.step(62, fire=0)
        self.assertEqual(self.health(self.enemy), 905)
        script = self.enemy.body_script
        frame = script.component.frame
        for offset in (500, 650):
            tick = self.run.frame + offset
            self.match.update(tick * .016, tick)
            if offset == 500:
                self.assertIsNotNone(self.fight.winston_primal.health_state(self.enemy))
                self.assertEqual(self.health(self.enemy), 905)
        self.assertEqual(script.component.frame, frame)
        self.assertIsNone(self.fight.winston_primal.health_state(self.enemy))
        self.assertEqual(self.health(self.enemy), 405)
        self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 500)
        self.assertNotIn(self.enemy.body, self.fight.winston_primal.bonus)
        self.assertEqual(self.fight.vitals_of(target).now[ARMOUR_POOL], 55)

    def test_actual_fist_hits_body_and_frontal_field_and_applies_passive(self):
        self.place(self.enemy, (0, 1, 2.5))
        self.step(15, fire=0, rein=2)
        self.rage(rein=2)
        self.punch(rein=2)
        self.assertEqual(self.health(self.rein), 465)
        self.assertEqual(self.shield().current, 1560)
        self.assertTrue(self.rein.mover.impulse_active)
        self.assertAlmostEqual(self.rein.mover.velocity[2], -25 * .7, delta=.01)
        self.assertAlmostEqual(self.rein.mover.velocity[1], 7 * .7, delta=.01)
        self.assertEqual(self.weapon().vars[53].value(), 100)
        self.assertEqual(self.enemy.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.enemy, Command(frame, buttons=1, yaw=-32768))
        self.assertEqual(self.health(self.rein), 465)
        self.assertEqual(self.shield().current, 1560)

    def test_actual_fist_can_hit_shield_without_reaching_reinhardt(self):
        self.place(self.enemy, (0, 1, 3))
        self.step(15, fire=0, rein=2)
        self.rage(rein=2)
        for _ in range(80):
            self.step(rein=2)
            if self.shield().current < 1600:
                break
        self.assertEqual(self.shield().current, 1560)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(self.rein.mover.impulse_active)

    def test_actual_punch_rejects_rear_wall_range_and_team(self):
        for case in ("rear", "wall", "range", "team"):
            with self.subTest(case=case):
                if case != "rear":
                    self.setUp()
                self.place(self.enemy, (0, 1, 6 if case == "range" else 2))
                if case == "wall":
                    fixtures.charge_wall(self, 1)
                if case == "team":
                    self.enemy.team = self.rein.team
                self.rage()
                self.step(80, yaw=0 if case == "rear" else -32768)
                self.assertTrue(self.fight.winston_primal.hits[self.enemy.body])
                self.assertEqual(self.health(self.rein), 500)
                self.assertFalse(self.rein.mover.impulse_active)

    def test_insufficient_charge_does_not_open_primal_pool(self):
        self.step(fire=32)
        self.assertIsNone(self.fight.winston_primal.health_state(self.enemy))
        self.assertEqual(self.health(self.enemy), 500)


    def test_alternating_fists_keep_authored_period_and_release_ends_new_swings(self):
        self.rage()
        hits = []
        health = 500
        for _ in range(150):
            self.place(self.rein, (0, 1, 0))
            self.rein.mover.velocity = (0, 0, 0)
            self.place(self.enemy, (0, 1, 1.5))
            self.step()
            current = self.health(self.rein)
            if current < health:
                hits.append(self.run.frame)
                self.assertEqual(health - current, 35)
                health = current
            if len(hits) >= 3:
                break
        self.assertEqual(len(hits), 3)
        self.assertTrue(all(40 <= b - a <= 42 for a, b in pairwise(hits)))
        keys = self.fight.winston_primal.hits[self.enemy.body]
        self.assertEqual({index for _, index in keys}, {35, 36})
        for _ in range(55):
            self.place(self.rein, (0, 1, 0))
            self.rein.mover.velocity = (0, 0, 0)
            self.step(fire=0)
        self.assertEqual(self.health(self.rein), health)

    def test_secondary_during_primal_does_not_punch_or_spend_ammo(self):
        self.place(self.enemy, (0, 1, 2))
        self.rage()
        self.step(80, fire=2)
        self.assertNotIn(self.enemy.body, self.fight.winston_primal.hits)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.weapon().vars[53].value(), 100)

    def test_charge_wall_kills_transformed_winston_and_removes_bonus(self):
        self.rage()
        fixtures.charge_wall(self, 9)
        target = self.fight._target_entity(self.enemy.body)
        self.fight.vitals_of(target).damage(960)
        self.step(fire=0, rein=8)
        for _ in range(100):
            self.step(fire=0)
            if self.fight.is_dead(self.enemy):
                break
        self.assertTrue(self.fight.is_dead(self.enemy))
        self.assertEqual(self.health(self.enemy), 0)
        self.assertNotIn(self.enemy.body, self.fight.winston_primal.bonus)
        self.assertNotIn(self.enemy.body, self.fight.winston_primal.hits)
        self.assertEqual(self.enemy.body_script.component.entity_var(HUD_MAX_HEALTH).value(), 500)


    def test_primal_resets_jump_and_applies_two_second_cooldown_until_expiry(self):
        self.step(fire=8)
        jump = next(i for i in self.enemy.body_script.component.instances.values()
                    if i.graph.index == JUMP_GRAPH)
        ability = jump.states[19]
        self.assertGreater(ability.remaining(), 5.9)
        self.step(115, fire=0)
        self.rage()
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(jump.vars[472].value(), 2)
        self.step(35, fire=0)
        self.step(fire=8)
        self.assertGreater(ability.remaining(), 1.9)
        self.assertLessEqual(ability.remaining(), 2)
        tick = self.run.frame + 650
        self.match.update(tick * .016, tick)
        self.assertIsNone(self.fight.winston_primal.health_state(self.enemy))
        self.assertEqual(jump.vars[472].value(), 6)


    def test_actual_earthshatter_interrupts_windup_without_ending_primal(self):
        self.place(self.enemy, (0, 1, 2))
        self.rage()
        self.step(14)
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        windup = None
        for _ in range(65):
            for index in (20, 21):
                state = self.weapon().states.get(index)
                if state is not None and state.active:
                    windup = state
            if self.fight.controlled(self.enemy.body):
                break
            self.step()
        self.assertTrue(self.fight.controlled(self.enemy.body))
        self.assertIsNotNone(windup)
        self.assertFalse(windup.active)
        self.assertEqual(self.health(self.rein), 500)
        self.assertNotIn(self.enemy.body, self.fight.winston_primal.hits)
        self.step(120)
        self.assertEqual(self.health(self.rein), 500)
        self.assertIsNotNone(self.fight.winston_primal.health_state(self.enemy))
        for _ in range(120):
            self.step()
            if self.health(self.rein) < 500:
                break
        self.assertEqual(self.health(self.rein), 465)
