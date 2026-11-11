"""Actual Winston Tesla input against Reinhardt and his Barrier Field."""

import unittest

import test_game_reinhardt as fixtures

from ow174.game import stats
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.winston_weapons import GRAPH, HERO


class ReinhardtTeslaTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance
    shield = fixtures.BarrierTests.shield

    def setUp(self):
        fixtures.BarrierTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[HERO]))
        self.place(self.enemy, (0, 1, 6))
        self.fight = self.match.combat
        self.step(3, fire=0)

    def step(self, frames=1, *, fire=1, rein=0, yaw=-32768):
        for _ in range(frames):
            self.run.step({self.rein: (0, 0, rein), self.enemy: (yaw, 0, fire)})

    def weapon(self):
        return next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == GRAPH)

    def test_primary_damages_armour_consumes_ammo_and_stops_on_release(self):
        self.step(20)
        self.assertAlmostEqual(self.health(self.rein), 500 - 20 * .96 * .8)
        self.assertTrue(self.weapon().states[10].active)
        self.assertTrue(self.weapon().states[13].active)
        self.assertLess(self.weapon().vars[53].value(), 100)
        self.assertAlmostEqual(self.enemy.stats.value(stats.HERO_DAMAGE), 20 * .96 * .8)
        before = self.health(self.rein)
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.enemy, Command(frame, buttons=1, yaw=-32768))
        self.assertEqual(self.health(self.rein), before)
        self.step(8, fire=0)
        self.assertFalse(self.weapon().states[13].active)
        self.assertEqual(self.health(self.rein), before)

    def test_barrier_blocks_two_targets_and_break_slice_has_no_body_spill(self):
        self.place(self.other, (0.5, 1, -1.5))
        self.step(15, fire=0, rein=2)
        self.step(10, rein=2)
        shield = self.shield()
        self.assertAlmostEqual(shield.current, 1600 - 10 * .96, places=3)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 200)
        shield.current = .5
        self.step(rein=2)
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 200)
        self.step(rein=2)
        self.assertAlmostEqual(self.health(self.rein), 500 - .96 * .8)
        self.assertAlmostEqual(self.health(self.other), 200 - .96)

    def test_world_cover_range_rear_and_team_deny_real_primary(self):
        cases = ("wall", "range", "rear", "team")
        for case in cases:
            with self.subTest(case=case):
                if case != "wall":
                    self.setUp()
                if case == "wall":
                    fixtures.charge_wall(self, 3)
                elif case == "range":
                    self.place(self.enemy, (0, 1, 12))
                elif case == "team":
                    self.rein.team = self.enemy.team
                self.step(12, yaw=0 if case == "rear" else -32768)
                self.assertTrue(self.weapon().states[13].active)
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE), 0)

    def test_real_shatter_interrupts_primary_and_recovery_reopens_fire(self):
        self.step(5)
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        for _ in range(65):
            if self.fight.controlled(self.enemy.body):
                break
            self.step()
        self.assertTrue(self.fight.controlled(self.enemy.body))
        stopped = self.health(self.rein)
        self.step(25)
        self.assertFalse(self.weapon().states[13].active)
        self.assertEqual(self.health(self.rein), stopped)
        for _ in range(185):
            self.step()
            if self.health(self.rein) < stopped:
                break
        self.assertFalse(self.fight.controlled(self.enemy.body))
        self.assertLess(self.health(self.rein), stopped)

    def test_tesla_kills_reinhardt_and_retires_the_live_barrier(self):
        self.step(15, fire=0, rein=2)
        self.assertTrue(self.shield().active)
        target = self.fight._target_entity(self.rein.body)
        self.fight.vitals_of(target).damage(499.5, bypass_armour=True)
        self.step(yaw=-32768, rein=2)
        self.assertEqual(self.health(self.rein), .5)
        self.place(self.enemy, (0, 1, -3))
        self.step(yaw=0, rein=2)
        self.assertEqual(self.health(self.rein), 0)
        self.assertTrue(self.fight.is_dead(self.rein))
        self.assertFalse(self.shield().active)
        self.assertFalse(self.fight._barrier_instance(self.rein).states[17].active)
        self.assertEqual(self.fight.vitals_of(target).killer, self.enemy.body)

    def test_reinhardt_fire_strike_kills_winston_and_ends_active_tesla(self):
        self.step(5)
        target = self.fight._target_entity(self.enemy.body)
        self.fight.vitals_of(target).damage(450)
        self.step(rein=16)
        for _ in range(65):
            if self.fight.is_dead(self.enemy):
                break
            self.step()
        self.assertTrue(self.fight.is_dead(self.enemy))
        before = self.health(self.rein)
        self.step(8)
        self.assertEqual(self.health(self.rein), before)
        self.assertNotIn(self.enemy.body, self.fight.tesla_frames)

    def test_normal_secondary_does_not_start_tesla_or_spend_ammo(self):
        self.step(30, fire=2)
        state = self.weapon().states.get(13)
        self.assertFalse(state is not None and state.active)
        self.assertEqual(self.weapon().vars[53].value(), 100)
        self.assertEqual(self.health(self.rein), 500)

    def test_actual_charge_pin_stops_tesla_and_wall_hit_releases(self):
        fixtures.charge_wall(self, 9)
        self.step(5)
        self.step(rein=8)
        for _ in range(100):
            if self.fight.pinned(self.enemy.body):
                break
            self.step()
        self.assertTrue(self.fight.pinned(self.enemy.body))
        stopped = self.health(self.rein)
        self.step()
        self.assertFalse(self.weapon().states[13].active)
        self.assertEqual(self.health(self.rein), stopped)
        for _ in range(50):
            if not self.fight.pinned(self.enemy.body):
                break
            self.step()
        self.assertFalse(self.fight.pinned(self.enemy.body))
        self.assertEqual(self.health(self.enemy), 205)

    def test_actual_magazine_exhaustion_and_reload(self):
        for _ in range(345):
            self.step()
            if not self.weapon().vars[53].value():
                break
        self.assertEqual(self.weapon().vars[53].value(), 0)
        self.step(1)
        self.assertFalse(self.weapon().states[13].active)
        before = self.health(self.rein)
        self.step(25)
        self.assertEqual(self.health(self.rein), before)
        for _ in range(100):
            self.step()
            if self.health(self.rein) < before:
                break
        self.assertGreater(self.weapon().vars[53].value(), 0)
        self.assertLess(self.health(self.rein), before)
