"""Actual Roadhog chain hook against the two active D.Va forms."""

import math
import unittest
from unittest.mock import patch

import test_game_dva_ana as ana_tests
from test_game_combat import aim

from ow174.game import combat, hook
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero


class DvaHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ana_tests.DvaAnaTests.setUpClass()

    def scene(self, *, pilot_form=False, distance=10, with_zarya=False):
        fixture = ana_tests.DvaAnaTests()
        run, pilot, hog, soldier, target, health = fixture.scene(pilot_form=pilot_form, with_zarya=with_zarya)
        self.assertTrue(run.match.switch_hero(hog, find_hero("roadhog")))
        x, y, z = target.feet
        hog.mover.position[:] = (x, y, z + distance)
        hog.mover.body = None
        for _ in range(30):
            run.step({})
        return run, pilot, hog, soldier, target, health

    @staticmethod
    def step(run, pilot, hog, *, cast=0, buttons=0, yaw_override=None):
        fight = run.match.combat
        target = fight.player_target(pilot)
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(hog), (x, y + 1.1, z))
        if yaw_override is not None:
            yaw = yaw_override
        run.step({hog: (yaw, pitch, cast), pilot: (0, 0, buttons)})

    def latch(self, run, pilot, hog, target, *, buttons=0):
        fight = run.match.combat
        self.step(run, pilot, hog, cast=8, buttons=buttons)
        for _ in range(65):
            self.step(run, pilot, hog, buttons=buttons)
            if fight.hooks.controlled(target.entity):
                return fight.hooks.flights[hog.body]
        self.fail("actual hook did not latch")

    def test_both_forms_take_one_hit_and_are_pulled_then_regain_weapons(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, hog, _, target, health = self.scene(pilot_form=pilot_form)
                fight = run.match.combat
                before = health.total
                flight = self.latch(run, pilot, hog, target)
                self.assertEqual(health.total, before - 30)
                parameters = flight.reach, flight.speed, flight.radius, flight.pull_speed
                self.assertEqual(parameters, (20, 60, 0.5, 40))
                attacker = fight.vitals_of(fight.player_target(hog))
                attacker_start = attacker.total
                for _ in range(8):
                    self.step(run, pilot, hog, buttons=1)
                self.assertTrue(fight.is_stunned(target.owner))
                self.assertEqual(attacker.total, attacker_start)
                self.assertLess(math.dist(fight.player_target(pilot).feet, hog.mover.position), 7)
                for _ in range(65):
                    self.step(run, pilot, hog)
                self.assertFalse(fight.is_stunned(target.owner))
                separation = math.dist(fight.player_target(pilot).feet, hog.mover.position)
                self.assertAlmostEqual(separation, 3, places=2)
                self.assertEqual(len(hog.client.messages(combat.HIT)), 1)
                self.assertEqual(health.total, before - 30)
                self.step(run, pilot, hog, buttons=1)
                for _ in range(20):
                    run.step({})
                self.assertLess(attacker.total, attacker_start)

    def test_actual_front_hook_reaches_mech_and_stops_active_matrix(self):
        run, pilot, hog, _, target, health = self.scene()
        instances = target.owner.body_script.component.instances.values()
        matrix = next(i for i in instances if i.graph.index == 0x2CC)
        for _ in range(10):
            self.step(run, pilot, hog, buttons=2)
        self.assertTrue(matrix.state(3).active)
        before = health.total
        self.latch(run, pilot, hog, target, buttons=2)
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(health.total, before - 30)
        ability = matrix.state(0)
        self.assertEqual(ability.cur, matrix.vars[472].value())
        self.assertGreater(ability.remaining(), 0)
        start = ability.last
        fight = run.match.combat
        for _ in range(80):
            if not fight.is_stunned(target.owner):
                break
            self.step(run, pilot, hog, buttons=2)
        self.assertFalse(fight.is_stunned(target.owner))
        self.assertEqual(ability.last, start)
        self.assertFalse(matrix.state(3).active)

    def test_actual_hook_stops_micro_missiles_without_removing_launched_rounds(self):
        run, pilot, hog, enemy, target, mech_health = self.scene(distance=3)
        fight = run.match.combat
        mech = target.owner
        x, y, z = target.feet
        hog.mover.position[:] = (x + 3, y, z)
        enemy.mover.position[:] = (x, y, z + 15)
        hog.mover.body = enemy.mover.body = None
        enemy.team = hog.team
        run.step({})
        health = fight.vitals_of(fight.player_target(enemy))
        before = health.total
        mech_before = mech_health.total
        yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + 15))
        ability = next(i.state(0) for i in mech.body_script.component.instances.values()
                       if i.graph.index == 0x1128)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({pilot: (yaw, pitch, 16)})
            for _ in range(45):
                if fight.rockets:
                    break
                run.step({})
            self.assertTrue(any(rocket.shooter is mech for rocket in fight.rockets))
            self.latch(run, pilot, hog, target)
            launched = sum(call.args[0] is mech for call in shoot.call_args_list)
            self.assertGreater(launched, 0)
            self.assertLess(launched, 18)
            self.assertTrue(any(rocket.shooter is mech for rocket in fight.rockets))
            self.assertEqual(mech_health.total, mech_before - 30)
            self.assertGreater(ability.remaining(), 0)
            for _ in range(100):
                run.step({pilot: (yaw, pitch, 16)})
            self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), launched)
        self.assertLess(health.total, before)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.is_stunned(mech))
        self.assertIs(pilot.controlled_mech, mech)

    def test_actual_hook_respects_wall_ally_miss_and_range(self):
        from test_game_mover import box, wall

        for case in ("wall", "ally", "miss", "range"):
            with self.subTest(case=case):
                run, pilot, hog, _, target, health = self.scene(distance=23 if case == "range" else 10)
                x, y, z = target.feet
                if case == "wall":
                    run.match.collision_world = World(
                        box(x - 20, x + 20, 0, y, z - 20, z + 30)
                        + wall(x - 10, z + 4, x + 10, z + 4)
                    )
                if case == "ally":
                    hog.team = pilot.team
                before = health.total
                miss = 16384 if case == "miss" else None
                self.step(run, pilot, hog, cast=8, yaw_override=miss)
                for _ in range(65):
                    self.step(run, pilot, hog, yaw_override=miss)
                self.assertEqual(health.total, before)
                self.assertFalse(run.match.combat.hooks.controlled(target.entity))

    def test_hook_pull_completes_without_victim_commands_in_both_forms(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, hog, _, target, _ = self.scene(pilot_form=pilot_form)
                fight = run.match.combat
                self.latch(run, pilot, hog, target)
                initial = math.dist(fight.player_target(pilot).feet, hog.mover.position)
                for _ in range(75):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                final = math.dist(fight.player_target(pilot).feet, hog.mover.position)
                self.assertLess(final, initial)
                self.assertAlmostEqual(final, 3, places=2)
                self.assertFalse(fight.is_stunned(target.owner))

    def test_delayed_hook_input_is_blocked_after_recovery_or_caster_replacement(self):
        for pilot_form in (False, True):
            for replaced in (False, True):
                with self.subTest(pilot_form=pilot_form, replaced=replaced):
                    self.check_delayed_hook_input(pilot_form, replaced)

    def check_delayed_hook_input(self, pilot_form, replaced):
        run, pilot, hog, _, target, _ = self.scene(pilot_form=pilot_form)
        match, fight = run.match, run.match.combat
        self.latch(run, pilot, hog, target)
        lock, = fight.input_locks[target.entity]
        self.assertEqual(lock.effect, hook.INPUT_LOCK)
        self.assertEqual(lock.start, run.frame)
        self.assertIsNone(lock.end)
        for _ in range(5 if replaced else 75):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            if not fight.hooks.controlled(target.entity):
                break
        if replaced:
            self.assertTrue(fight.hooks.controlled(target.entity))
            self.assertTrue(match.switch_hero(hog, find_hero("soldier")))
        self.assertFalse(fight.is_stunned(target.owner))
        self.assertFalse(fight.hooks.controlled(target.entity))
        self.assertEqual(lock.end, run.frame)
        mover = pilot.mover if pilot_form else target.owner.control_mover
        feet = tuple(mover.position)
        yaw, pitch = aim(fight._eye(target.owner), fight._eye(hog))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            pilot.take_commands([Command(run.frame - 2, yaw=yaw, pitch=pitch, buttons=1, forward=127)])
            pilot.take_commands([Command(run.frame - 1, yaw=yaw, pitch=pitch, buttons=1)])
            self.assertFalse(any(call.args[0] is target.owner for call in shoot.call_args_list))
            self.assertEqual(tuple(mover.position[0::2]), feet[0::2])
            pilot.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
            self.assertTrue(any(call.args[0] is target.owner for call in shoot.call_args_list))

    def test_actual_caster_death_releases_the_controlled_mech_immediately(self):
        run, pilot, hog, soldier, target, _ = self.scene()
        fight = run.match.combat
        health = fight.vitals_of(fight.player_target(hog))
        health.damage(health.total - 18)
        fight._sync_body_health(hog, health)
        self.latch(run, pilot, hog, target)
        x, y, z = hog.mover.position
        soldier.mover.position[:] = (x + 4, y, z)
        soldier.mover.body = None
        for _ in range(2):
            self.step(run, pilot, hog)
        yaw, pitch = aim(fight._eye(soldier), (x, y + 1.2, z))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertTrue(health.dead)
        self.assertFalse(fight.is_stunned(target.owner))
        self.assertNotIn(hog.body, fight.hooks.flights)

    def test_actual_projected_barrier_stops_hook_before_mech_contact(self):
        run, pilot, hog, _, target, health = self.scene(with_zarya=True)
        fight = run.match.combat
        zarya = run.players[3]
        zarya.team = pilot.team
        self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
        x, y, z = target.feet
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        a, b = target.segment()
        centre = tuple((p + q) / 2 for p, q in zip(a, b, strict=True))
        yaw, pitch = aim(fight._eye(zarya), centre)
        for _ in range(30):
            run.step({zarya: (yaw, pitch, 0)})
        run.step({zarya: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        before = health.total
        self.step(run, pilot, hog, cast=8)
        for _ in range(60):
            self.step(run, pilot, hog)
        self.assertEqual(bubble.health, 170)
        self.assertEqual(health.total, before)
        self.assertFalse(fight.is_stunned(target.owner))
        self.assertNotIn(target.entity, fight.input_locks)
