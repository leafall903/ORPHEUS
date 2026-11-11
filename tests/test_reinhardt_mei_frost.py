"""Actual Mei spray against Reinhardt armour, barrier, and input."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
from test_game_combat import aim

from ow174.game import defenses, mei_frost
from ow174.game.commands import Command
from ow174.game.content import heroes


class ReinhardtMeiFrostTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    def arena(self, distance=4):
        if fixtures.BARRIER_DATA.is_file():
            self.enterContext(patch.dict(os.environ, {
                "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
            }))
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.place(self.enemy, (0, 1, distance))
        self.place(self.other, (20, 1, 0))
        self.step(6)

    def step(self, count=1, *, spray=0, rein=0, forward=0, yaw=0):
        for _ in range(count):
            angles = aim(self.match.combat._eye(self.enemy),
                         (self.rein.mover.position[0], self.rein.mover.position[1] + 1,
                          self.rein.mover.position[2]))
            if forward:
                self.run.frame += 1
                self.match.update(self.run.frame * .016, self.run.frame)
                self.rein.take_commands([Command(self.run.frame, yaw=yaw, buttons=rein, forward=forward)])
            else:
                self.run.step({self.enemy: (*angles, spray), self.rein: (yaw, 0, rein)})

    def test_actual_single_spray_damage_slow_and_gap_expiry(self):
        self.arena()
        fight = self.match.combat
        self.step(spray=1)
        self.step(16)
        self.assertAlmostEqual(self.health(self.rein), 498.625)
        [sample] = fight.frost[self.rein.body].values()
        self.assertAlmostEqual(sample[0], .2)
        self.step(4, forward=127)
        self.assertAlmostEqual(self.rein.mover.values.run_forward,
                               self.rein.mover.data.run_forward * .8, places=4)
        self.step(70)
        self.assertNotIn(self.rein.body, fight.frost)
        self.assertEqual(self.rein.mover.values.run_forward, self.rein.mover.data.run_forward)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "authored Barrier Field descriptor required")
    def test_held_barrier_blocks_actual_spray_damage_and_frost(self):
        self.arena()
        self.step(15, rein=2)
        self.step(90, rein=2, spray=1)
        fight = self.match.combat
        self.assertLess(fight.barriers[self.rein.body].current, 1600)
        self.assertEqual(self.health(self.rein), 500)
        self.assertNotIn(self.rein.body, fight.frost)
        self.assertFalse(mei_frost.frozen(fight, self.rein.body))

    def test_sustained_spray_freezes_and_denies_abilities_until_thaw(self):
        self.arena()
        fight = self.match.combat
        for _ in range(145):
            self.step(spray=1)
            if mei_frost.frozen(fight, self.rein.body):
                break
        self.assertTrue(mei_frost.frozen(fight, self.rein.body))
        key = (self.rein.body, self.enemy.body, "frost")
        deadline = fight.controls[key]
        self.assertAlmostEqual(deadline - fight.now, 1.3)
        before = tuple(self.rein.mover.position)
        self.step(8, spray=1, rein=16, yaw=8192)
        self.assertEqual(fight.controls[key], deadline)
        self.assertEqual(self.rein.mover.yaw, 0)
        self.step(12, forward=127, rein=8, yaw=8192)
        self.assertEqual(tuple(self.rein.mover.position), before)
        self.assertNotIn(self.rein.body, fight.charge_drives)
        self.step(70)
        self.assertFalse(mei_frost.frozen(fight, self.rein.body))
        self.step(20, forward=127)
        self.assertGreater(self.rein.mover.position[2], before[2])

    def test_freeze_interrupts_an_actual_fire_strike_windup(self):
        self.arena()
        fight = self.match.combat
        for _ in range(145):
            self.step(spray=1)
            samples = fight.frost.get(self.rein.body, {})
            if any(amount >= .91 for amount, _ in samples.values()):
                break
        else:
            self.fail("Spray did not reach the pre-freeze threshold")
        self.step(spray=1, rein=16)
        fire_strike = next(i for i in self.rein.body_script.component.instances.values()
                           if i.graph.index == 0x00E2)
        self.assertTrue(fire_strike.states[9].flags & 1)
        self.step(25, spray=1)
        self.assertTrue(mei_frost.frozen(fight, self.rein.body))
        self.assertFalse(fire_strike.states[9].flags & 1)
        self.step(95)
        self.assertEqual(self.health(self.enemy), 250)
        self.assertFalse(any(r.shooter is self.rein for r in fight.rockets))

    def test_freeze_cancels_hammer_and_melee_windup_before_contact(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.arena()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
                fight = self.match.combat
                for _ in range(145):
                    self.step(spray=1)
                    samples = fight.frost.get(self.rein.body, {})
                    if any(amount >= .91 for amount, _ in samples.values()):
                        break
                else:
                    self.fail("Spray did not reach the pre-freeze threshold")
                self.step(spray=1, rein=button)
                self.assertEqual(self.rein.body_script.buttons, button)
                hammer = next(i for i in self.rein.body_script.component.instances.values()
                              if i.graph.index == 0x48)
                self.assertTrue(hammer.states[13].active)
                for _ in range(25):
                    self.step(spray=1, rein=button)
                    if mei_frost.frozen(fight, self.rein.body):
                        break
                self.assertTrue(mei_frost.frozen(fight, self.rein.body))
                self.assertEqual(self.health(self.enemy), 250)
                self.place(self.enemy, (10, 1, 0))
                self.place(self.other, (0, 1, 3))
                deadline = fight.controls[self.rein.body, self.enemy.body, "frost"]
                while fight.now + .032 < deadline:
                    self.step(rein=button)
                    self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.other.mover.position[0], 0)
                self.step(50, rein=button)
                self.assertFalse(mei_frost.frozen(fight, self.rein.body))
                self.assertEqual(self.health(self.other), 115)

    def test_wall_range_and_team_prevent_actual_frost(self):
        for case in ("wall", "range", "ally"):
            with self.subTest(case=case):
                self.arena(distance=15 if case == "range" else 4)
                if case == "wall":
                    fixtures.charge_wall(self, z=2)
                if case == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.enemy.team = self.rein.team
                self.step(48, spray=1)
                self.assertEqual(self.health(self.rein), 500)
                self.assertNotIn(self.rein.body, self.match.combat.frost)

    def test_spray_penetrates_two_bodies_and_replacement_retires_frost(self):
        self.arena()
        self.place(self.other, (0, 1, -2))
        self.step(5)
        self.step(spray=1)
        self.step(20)
        self.assertAlmostEqual(self.health(self.rein), 498.625)
        self.assertAlmostEqual(self.health(self.other), 197.25)
        old = self.rein.body
        self.assertIn(old, self.match.combat.frost)
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[self.rein.hero.guid]))
        self.assertNotIn(old, self.match.combat.frost)
        self.assertNotIn(self.rein.body, self.match.combat.frost)

    def test_actual_icicle_deals_armour_damage_without_frost(self):
        self.arena()
        self.step(spray=2)
        self.step(60)
        self.assertEqual(self.health(self.rein), 430)
        self.assertNotIn(self.rein.body, self.match.combat.frost)

    def test_two_actual_spray_sources_keep_independent_default_progress(self):
        self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[defenses.MEI]))
        self.place(self.other, (4, 1, 0))
        self.step(6)
        fight = self.match.combat
        for _ in range(95):
            commands = {}
            for player in (self.enemy, self.other):
                angles = aim(fight._eye(player), (0, 2, 0))
                commands[player] = (*angles, 1)
            self.run.step(commands)
            samples = fight.frost.get(self.rein.body, {})
            if len(samples) == 2 and sum(progress for progress, _ in samples.values()) > 1:
                break
        else:
            self.fail("Two spray streams did not reach the combined threshold")
        self.assertFalse(mei_frost.frozen(fight, self.rein.body))
        for _ in range(95):
            self.step(spray=1)
            if mei_frost.frozen(fight, self.rein.body):
                break
        self.assertIn((self.rein.body, self.enemy.body, "frost"), fight.controls)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "authored Barrier Field descriptor required")
    def test_rear_spray_freeze_lowers_active_barrier(self):
        self.arena()
        fight = self.match.combat
        self.step(15, rein=2, yaw=-32768)
        self.assertTrue(fight.barriers[self.rein.body].active)
        for _ in range(145):
            self.step(spray=1, rein=2, yaw=-32768)
            if mei_frost.frozen(fight, self.rein.body):
                break
        self.assertTrue(mei_frost.frozen(fight, self.rein.body))
        self.assertFalse(fight.barriers[self.rein.body].active)

    def test_unleased_spray_retires_with_its_source_body(self):
        self.arena()
        fight = self.match.combat
        with patch.object(fight.projectiles, "fired", return_value=None):
            self.step(spray=1)
        [rocket] = fight.rockets
        self.assertIsNone(rocket.shot)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.assertNotIn(rocket, fight.rockets)
        self.step(15)
        self.assertEqual(self.health(self.rein), 500)
        self.assertNotIn(self.rein.body, fight.frost)
