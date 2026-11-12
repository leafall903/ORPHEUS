"""Actual command-driven Torbjorn magazine, reload and Overload modifier evidence."""

import unittest
from itertools import pairwise
from unittest import mock

import test_game_torbjorn as projectile_tests

from ow174.game.commands import Command


class TorbjornResourceTests(unittest.TestCase):
    def arena(self, boosted=False):
        run, owner, victim = projectile_tests.TorbjornProjectileTests().arena()
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x1683)
        self.assertEqual(weapon.vars[198].value(), 18)
        self.assertEqual(weapon.vars[53].value(), 18)
        if boosted:
            run.step({owner: (16384, 0, 16)})
            self.advance(run, owner, 45)
        return run, owner, victim, weapon

    @staticmethod
    def advance(run, owner, ticks, buttons=0):
        for _ in range(ticks):
            run.step({owner: (16384, 0, buttons)})  # aim away, so victim death cannot truncate the probe

    @staticmethod
    def launches(spy, weapon):
        return [(call.args[2].index, call.args[4] // 16) for call in spy.call_args_list
                if call.args[2].instance is weapon]

    @staticmethod
    def select(run, owner, action):
        run.frame += 1
        owner.take_commands([Command(run.frame, yaw=16384, action=action)])

    def test_primary_full_magazine_empty_gate_and_automatic_reload_recovery(self):
        run, owner, victim, weapon = self.arena()
        with mock.patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
            self.advance(run, owner, 700, 1)
            self.assertEqual(len(self.launches(shoot, weapon)), 18)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.advance(run, owner, 70, 1)
            self.assertEqual(len(self.launches(shoot, weapon)), 18)
            self.assertEqual(weapon.vars[53].value(), 18)
            self.advance(run, owner, 70, 1)
            self.assertEqual(len(self.launches(shoot, weapon)), 19)
            self.assertEqual(weapon.vars[53].value(), 17)
        self.assertEqual(run.match.combat.vitals[victim.body].total, 200.0)

    def test_secondary_exhausts_nine_two_ammo_volleys_then_recovers(self):
        run, owner, _, weapon = self.arena()
        with mock.patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
            self.advance(run, owner, 430, 2)
            self.assertEqual(len(self.launches(shoot, weapon)), 9)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.advance(run, owner, 100, 2)
            self.assertEqual(len(self.launches(shoot, weapon)), 9)
            self.assertEqual(weapon.vars[53].value(), 18)
            self.advance(run, owner, 60, 2)
            self.assertEqual(len(self.launches(shoot, weapon)), 10)
            self.assertEqual(weapon.vars[53].value(), 16)

    def test_overload_changes_both_actual_fire_intervals_without_changing_ammo_cost(self):
        for boosted in (False, True):
            for button, expected, cost in ((1, 0.42 if boosted else 0.6, 1),
                                           (2, 0.56 if boosted else 0.8, 2)):
                with self.subTest(boosted=boosted, button=button):
                    run, owner, _, weapon = self.arena(boosted)
                    fight = run.match.combat
                    with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        self.advance(run, owner, 150, button)
                        shots = self.launches(shoot, weapon)
                    self.assertGreaterEqual(len(shots), 4)
                    for (_, first), (_, second) in pairwise(shots):
                        self.assertAlmostEqual((second - first) * 0.016, expected, delta=0.017)
                    self.assertEqual(weapon.vars[53].value(), 18 - cost * len(shots))

    def test_alternating_primary_secondary_edges_cannot_bypass_shared_fire_delay(self):
        run, owner, _, weapon = self.arena()
        with mock.patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
            for at in range(150):
                run.step({owner: (16384, 0, 1 if at % 2 == 0 else 2)})
            shots = self.launches(shoot, weapon)
        self.assertGreater(len(shots), 1)
        self.assertLessEqual(len(shots), 4)
        self.assertTrue(all(b[1] - a[1] >= 37 for a, b in pairwise(shots)))

    def test_releasing_fire_stops_repeats_and_simultaneous_buttons_do_not_double_cast(self):
        for buttons, state, cost in ((1, 10, 1), (2, 13, 2), (3, 10, 1)):
            with self.subTest(buttons=buttons):
                run, owner, _, weapon = self.arena()
                fight = run.match.combat
                with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    self.advance(run, owner, 1, buttons)
                    self.advance(run, owner, 149)
                    shots = self.launches(shoot, weapon)
                self.assertEqual([index for index, _ in shots], [state])
                self.assertEqual(weapon.vars[53].value(), 18 - cost)

    def test_death_during_reload_prevents_further_attacks_or_resource_input(self):
        run, owner, victim, weapon = self.arena()
        self.advance(run, owner, 1, 1)
        self.advance(run, owner, 50)
        self.advance(run, owner, 1, 0x400)
        self.assertTrue(weapon.states[8].active)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, target, 1000.0, False, None, run.frame)
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.advance(run, owner, 160, 1 | 2 | 0x400 | 16)
            self.assertEqual(self.launches(shoot, weapon), [])
        self.assertTrue(fight.is_dead(owner))
        self.assertTrue(owner.body_script.component.owner_dead)

    def test_expired_overload_restores_normal_fire_and_manual_reload_timings(self):
        run, owner, _, weapon = self.arena(True)
        self.advance(run, owner, 320)
        fight = run.match.combat
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.advance(run, owner, 150, 1)
            shots = self.launches(shoot, weapon)
        self.assertEqual(len(shots), 4)
        for (_, first), (_, second) in pairwise(shots):
            self.assertAlmostEqual((second - first) * 0.016, 0.6, delta=0.017)
        self.advance(run, owner, 50)
        self.advance(run, owner, 1, 0x400)
        self.assertTrue(weapon.states[8].active)
        self.assertEqual(weapon.vars[581].value(), 2.0)

    def test_manual_reload_refill_is_separate_from_fire_ready_and_overload_shortens_both(self):
        for boosted, refill, duration in ((False, 62, 125), (True, 43, 88)):
            with self.subTest(boosted=boosted):
                run, owner, _, weapon = self.arena(boosted)
                self.advance(run, owner, 1, 1)
                self.advance(run, owner, 50)
                self.advance(run, owner, 1, 0x400)
                self.assertTrue(weapon.states[8].active)
                self.assertAlmostEqual(weapon.vars[581].value(), 1.4 if boosted else 2.0, places=5)
                self.assertEqual(weapon.vars[53].value(), 17)
                with mock.patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
                    self.advance(run, owner, refill - 1, 1)
                    self.assertEqual(weapon.vars[53].value(), 17)
                    self.advance(run, owner, 1, 1)
                    self.assertEqual(weapon.vars[53].value(), 18)
                    self.assertTrue(weapon.states[8].active)
                    self.advance(run, owner, duration - refill - 2, 1)
                    self.assertEqual(self.launches(shoot, weapon), [])
                    self.advance(run, owner, 5, 1)
                    self.assertEqual(len(self.launches(shoot, weapon)), 1)

    def test_holster_cancels_reload_and_reselecting_cancels_pending_background_refill(self):
        for before_refill in (True, False):
            with self.subTest(before_refill=before_refill):
                run, owner, _, weapon = self.arena()
                self.advance(run, owner, 1, 1)
                self.advance(run, owner, 50)
                self.advance(run, owner, 1, 0x400)
                self.advance(run, owner, 20 if before_refill else 70)
                self.select(run, owner, 2)
                self.advance(run, owner, 50)
                self.assertFalse(weapon.states[8].active)
                self.assertEqual(weapon.vars[53].value(), 17 if before_refill else 18)
                self.select(run, owner, 1)
                self.advance(run, owner, 150)
                self.assertEqual(weapon.vars[53].value(), 17 if before_refill else 18)

    def test_hammer_equipped_background_reload_refills_after_authored_two_seconds(self):
        run, owner, _, weapon = self.arena()
        self.advance(run, owner, 1, 1)
        self.advance(run, owner, 50)
        self.select(run, owner, 2)
        self.assertEqual(weapon.vars[53].value(), 17)
        self.assertTrue(weapon.states[25].active)
        self.advance(run, owner, 124)
        self.assertEqual(weapon.vars[53].value(), 17)
        self.advance(run, owner, 2)
        self.assertEqual(weapon.vars[53].value(), 18)
        self.select(run, owner, 1)
        self.advance(run, owner, 45)
        self.assertEqual(weapon.vars[53].value(), 18)

    def test_overload_increases_actual_run_speed_and_removes_modifier_at_expiry(self):
        speeds = []
        for boosted in (False, True):
            run, owner, victim, _ = self.arena(boosted)
            for _ in range(100):
                run.frame += 1
                run.match.update(run.frame * 0.016, run.frame)
                owner.take_commands([Command(run.frame, yaw=16384, forward=127)])
                victim.take_commands([Command(run.frame)])
            speeds.append(owner.mover.velocity[0])
            self.advance(run, owner, 320)
            for _ in range(40):
                run.frame += 1
                run.match.update(run.frame * 0.016, run.frame)
                owner.take_commands([Command(run.frame, yaw=16384, forward=127)])
                victim.take_commands([Command(run.frame)])
            self.assertAlmostEqual(owner.mover.velocity[0], 5.5, delta=0.002)
        self.assertAlmostEqual(speeds[0], 5.5, delta=0.002)
        self.assertAlmostEqual(speeds[1] / speeds[0], 1.3, delta=0.002)
