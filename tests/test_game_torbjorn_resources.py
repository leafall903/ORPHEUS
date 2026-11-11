"""Actual command-driven Torbjorn magazine, reload and Overload modifier evidence."""

import unittest
from itertools import pairwise
from unittest import mock

import test_game_torbjorn as projectile_tests
from test_game_combat import aim

from ow174.game import stats
from ow174.game.commands import Command


class TorbjornResourceTests(unittest.TestCase):
    def arena(self, boosted=False, distance=5.0):
        run, owner, victim = projectile_tests.TorbjornProjectileTests().arena(distance)
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

    def test_last_ammo_secondary_fires_ten_pellets_then_empty_input_is_rejected(self):
        run, owner, victim, weapon = self.arena(distance=1.5)
        self.advance(run, owner, 630, 1)
        self.advance(run, owner, 50)
        self.assertEqual(weapon.vars[53].value(), 1)
        fight = run.match.combat
        hp = fight.vitals[victim.body]
        self.assertEqual(hp.total, 200.0)
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({owner: (0, 0, 2)})
            self.advance(run, owner, 25)
            self.assertEqual(len(self.launches(shoot, weapon)), 1)
            self.assertEqual(hp.total, 75.0)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.advance(run, owner, 30, 2)
            self.assertEqual(len(self.launches(shoot, weapon)), 1)

    def test_overload_changes_both_actual_fire_intervals_without_changing_ammo_cost(self):
        for boosted in (False, True):
            for button, expected, cost in ((1, 0.42 if boosted else 0.6, 1),
                                           (2, 0.56 if boosted else 0.8, 2)):
                with self.subTest(boosted=boosted, button=button):
                    run, owner, _, weapon = self.arena(boosted)
                    fight = run.match.combat
                    with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        # Four ordinary0.8s volleys need more than150ticks
                        # when the first input starts on the next frame.
                        self.advance(run, owner, 200, button)
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

    def test_shared_fire_delay_retains_its_selected_rate_across_overload_expiry(self):
        for first, second, boosted_delay, ordinary_delay in ((1, 2, .42, .8), (2, 1, .56, .6)):
            with self.subTest(first=first, second=second):
                self.check_fire_delay_across_overload_expiry(first, second, boosted_delay, ordinary_delay)

    def check_fire_delay_across_overload_expiry(self, first, second, boosted_delay, ordinary_delay):
        run, owner, victim, weapon = self.arena(distance=1.5)
        fight = run.match.combat
        run.step({owner: (0, 0, 16)})
        hp = fight.vitals[owner.body]
        victim_target = next(target for target in fight._targets() if target.owner is victim)
        victim_hp = fight.vitals_of(victim_target)
        self.advance(run, owner, 299)
        self.assertEqual(hp.over_armour.amount, 100)
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({owner: (0, 0, first)})
            first_frame = run.frame
            self.assertEqual(weapon.vars[53].value(), 18 - first)
            expiry_frame = None
            for _ in range(55):
                run.step({owner: (0, 0, second)})
                if expiry_frame is None and hp.over_armour.source is None:
                    expiry_frame = run.frame
                if len(self.launches(shoot, weapon)) == 2:
                    break
                self.assertEqual(weapon.vars[53].value(), 18 - first)
            second_frame = run.frame
            self.assertIsNotNone(expiry_frame)
            self.assertLess(first_frame, expiry_frame)
            self.assertLess(expiry_frame, second_frame)
            self.assertEqual(weapon.vars[53].value(), 15)

            for _ in range(60):
                self.advance(run, owner, 1, second)
                if len(self.launches(shoot, weapon)) == 3:
                    break
                self.assertEqual(weapon.vars[53].value(), 15)
            third_frame = run.frame
            self.assertEqual(weapon.vars[53].value(), 15 - second)
        indices = {1: 10, 2: 13}
        self.assertEqual(self.launches(shoot, weapon), [
            (indices[first], first_frame), (indices[second], second_frame), (indices[second], third_frame)
        ])
        self.assertAlmostEqual((second_frame - first_frame) * .016, boosted_delay, delta=.017)
        self.assertAlmostEqual((third_frame - second_frame) * .016, ordinary_delay, delta=.017)
        self.assertEqual(victim_hp.total, 5)

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

    def test_boosted_manual_reload_keeps_refill_and_ready_deadlines_through_overload_expiry(self):
        for expiry in (.35, .95):
            with self.subTest(overload_seconds_at_reload=expiry):
                run, owner, victim, rifle = self.arena(True)
                fight = run.match.combat
                hp = fight.vitals[owner.body]
                target = next(item for item in fight._targets() if item.owner is victim)
                victim_hp = fight.vitals_of(target)
                self.advance(run, owner, 1, 1)
                self.advance(run, owner, 50)
                remaining = owner.body_script.component.entity_var(9143)
                for _ in range(270):
                    if remaining.value() <= expiry:
                        break
                    self.advance(run, owner, 1)
                self.assertAlmostEqual(remaining.value(), expiry, delta=.0161)
                self.assertEqual(hp.over_armour.amount, 100)
                self.advance(run, owner, 1, 0x400)
                reload = rifle.state(8)
                self.assertTrue(reload.active)
                activation = reload.counter
                start = run.frame
                refill, ready = (43, 88)
                ammo = rifle.vars[53]
                self.assertEqual(ammo.value(), 17)
                x, y, z = victim.mover.position
                direction = aim(fight._eye(owner), (x, y + .8, z))
                transition = None

                with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    for age in range(1, ready - 1):
                        run.step({owner: (*direction, 1)})
                        self.assertEqual(self.launches(shots, rifle), [])
                        self.assertEqual(ammo.value(), 17 if age < refill else 18)
                        self.assertEqual(reload.counter, activation)
                        self.assertEqual(victim_hp.total, 200)
                        if transition is None and hp.over_armour.amount == 0:
                            transition = age
                    self.assertIsNotNone(transition)
                    if expiry < .6:
                        self.assertLess(transition, refill)
                    else:
                        self.assertGreater(transition, refill)
                    for _ in range(6):
                        run.step({owner: (*direction, 1)})
                        if self.launches(shots, rifle):
                            break
                    launch, = self.launches(shots, rifle)
                    self.assertEqual(launch[0], 10)
                    self.assertAlmostEqual(launch[1] - start, ready, delta=1)
                self.assertFalse(reload.active)
                self.assertEqual(ammo.value(), 17)
                for _ in range(25):
                    if victim_hp.total < 200:
                        break
                    run.step({})
                self.assertEqual(victim_hp.total, 130)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70)


    def test_overload_cancels_manual_reload_on_both_sides_of_the_refill(self):
        for age in (20, 70):
            with self.subTest(age=age):
                run, owner, victim, rifle = self.arena()
                fight = run.match.combat
                hp = fight.vitals[owner.body]
                target = next(item for item in fight._targets() if item.owner is victim)
                victim_hp = fight.vitals_of(target)
                self.advance(run, owner, 1, 1)
                self.advance(run, owner, 50)
                self.advance(run, owner, 1, 0x400)
                reload = rifle.state(8)
                self.assertTrue(reload.active)
                activation = reload.counter
                start = run.frame
                self.advance(run, owner, age - 1)
                ammo = rifle.vars[53]
                expected_ammo = 17 if age < 62 else 18
                self.assertEqual(ammo.value(), expected_ammo)
                self.assertTrue(reload.active)
                self.advance(run, owner, 1, 16)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertFalse(reload.active)
                self.assertEqual(reload.counter, activation)
                self.advance(run, owner, 45)
                self.assertEqual(ammo.value(), expected_ammo)

                if age < 62:
                    self.advance(run, owner, 1, 0x400)
                    self.assertTrue(reload.active)
                    self.assertGreater(reload.counter, activation)
                    self.assertAlmostEqual(rifle.vars[581].value(), 1.4, places=5)
                    self.advance(run, owner, 42)
                    self.assertEqual(ammo.value(), 17)
                    self.advance(run, owner, 1)
                    self.assertEqual(ammo.value(), 18)
                    self.advance(run, owner, 46)
                    self.assertFalse(reload.active)
                else:
                    self.assertLess(run.frame, start + 125)
                x, y, z = victim.mover.position
                direction = aim(fight._eye(owner), (x, y + .8, z))
                for _ in range(30):
                    run.step({owner: (*direction, 1)})
                    if ammo.value() == 17:
                        break
                self.assertEqual(ammo.value(), 17)
                self.advance(run, owner, 25)
                self.assertEqual(victim_hp.total, 130)
                self.assertEqual(ammo.value(), 17)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70)


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

    def test_background_reload_latches_duration_across_overload_start_and_expiry(self):
        for scenario in ("starts_holstered", "expires_default", "expires_after_manual_reload"):
            with self.subTest(scenario=scenario):
                starts = scenario == "starts_holstered"
                manual_reload = scenario == "expires_after_manual_reload"
                run, owner, victim, rifle = self.arena(not starts, distance=1.5)
                fight = run.match.combat
                hp = fight.vitals[owner.body]
                target = next(item for item in fight._targets() if item.owner is victim)
                victim_hp = fight.vitals_of(target)
                self.advance(run, owner, 1, 1)
                self.advance(run, owner, 50)
                if not starts:
                    remaining = owner.body_script.component.entity_var(9143)
                    for _ in range(260):
                        if remaining.value() <= 1.1:
                            break
                        self.advance(run, owner, 1)
                    self.assertGreater(remaining.value(), .9)
                    self.assertEqual(hp.over_armour.amount, 100)
                    if manual_reload:
                        self.advance(run, owner, 1, 0x400)
                        self.assertTrue(rifle.states[8].active)
                        self.assertAlmostEqual(rifle.vars[581].value(), 1.4, places=5)
                self.select(run, owner, 2)
                wait = rifle.state(25)
                ammo = rifle.vars[53]
                self.assertTrue(wait.active)
                self.assertEqual(ammo.value(), 17)
                duration = 1400 if manual_reload else 2000
                self.assertEqual(wait.ms, duration)
                original_start, original_counter = wait.start, wait.counter

                self.advance(run, owner, 25)
                if starts:
                    run.step({owner: (0, 0, 16)})
                    self.assertEqual(hp.over_armour.amount, 100)
                self.advance(run, owner, 45 if starts else 15)
                for _ in range(30):
                    run.step({owner: (0, 0, 1)})
                    if victim_hp.total < 200:
                        break
                self.assertEqual(victim_hp.total, 145)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)
                self.assertTrue(wait.active)
                self.assertEqual(ammo.value(), 17)
                self.assertEqual((wait.start, wait.counter, wait.ms),
                                 (original_start, original_counter, duration))

                deadline = original_start + duration
                while wait.active and owner.body_script.component.now < deadline:
                    run.step({})
                    if owner.body_script.component.now < deadline:
                        self.assertEqual(ammo.value(), 17)
                self.assertFalse(wait.active)
                self.assertEqual(wait.ended_at, deadline)
                self.assertEqual(ammo.value(), 18)
                self.assertEqual(hp.over_armour.amount, 100 if starts else 0)

                self.select(run, owner, 1)
                self.advance(run, owner, 45)
                x, y, z = victim.mover.position
                direction = aim(fight._eye(owner), (x, y + .8, z))
                run.step({owner: (*direction, 1)})
                for _ in range(25):
                    if victim_hp.total < 145:
                        break
                    run.step({})
                self.assertEqual(ammo.value(), 17)
                self.assertEqual(victim_hp.total, 75)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 125)


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

    def test_background_refill_and_rivet_fire_survive_three_holster_reentry_cycles(self):
        for boosted in (False, True):
            with self.subTest(boosted=boosted):
                run, owner, _, weapon = self.arena(boosted)
                self.advance(run, owner, 1, 1)
                self.advance(run, owner, 50)
                self.assertEqual(weapon.vars[53].value(), 17)
                for cycle in range(3):
                    self.select(run, owner, 2)
                    self.advance(run, owner, 126)
                    self.assertEqual(weapon.vars[53].value(), 18, (boosted, cycle))
                    self.select(run, owner, 1)
                    self.advance(run, owner, 45)
                    fight = run.match.combat
                    with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        self.advance(run, owner, 1, 1)
                        self.advance(run, owner, 50)
                    self.assertEqual(len(self.launches(shoot, weapon)), 1, (boosted, cycle))
                    self.assertEqual(weapon.vars[53].value(), 17, (boosted, cycle))
