"""Authored Heavy Assault charge against the active D.Va body."""

import unittest

import test_game_dva_ana as ana_tests
import test_game_dva_created_body as created
import test_game_dva_manual_npcs as manual_tests
from test_game_combat import aim, dva_pilot_input
from test_game_mover import box, wall

from ow174.game import combat, npc, npc_charge, npc_control
from ow174.game.collision import World
from ow174.game.content import find_hero


class DvaNpcChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manual_tests.ManualIncomingTests.setUpClass.__func__(cls)

    def scene(self, *, pilot_form=False, boost_health=False):
        run, pilot, other, _, _ = created.CreatedBodyTests().scenario()
        other.spawned = False
        fight = run.match.combat
        if pilot_form:
            dva_pilot_input(pilot)
            pilot.mover.position[0] += 10
            pilot.mover.body = None
        for _ in range(100):
            run.step({})
        target = fight.player_target(pilot)
        if boost_health:
            fight.vitals[target.entity] = combat.Vitals(target.entity, 500)
        x, y, z = target.feet
        bot = npc.spawn(run.match, self.by_name["Heavy Assault"], (x, y, z + 6), team=1)
        return run, pilot, bot, target, fight.vitals_of(target)

    def test_authored_charge_hits_both_dva_forms_and_bypasses_matrix(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, bot, target, health = self.scene(pilot_form=pilot_form, boost_health=pilot_form)
                initial = health.total
                start = bot.position[2]
                for _ in range(5):
                    run.step({pilot: (0, 0, 0 if pilot_form else 2)})
                npc_control.engage(run.match, bot, pilot, charge=True)
                for _ in range(100):
                    run.step({pilot: (0, 0, 0 if pilot_form else 2)})
                    if health.total < initial:
                        break
                instance = npc_charge.instance(bot)
                self.assertLess(bot.position[2], start - 3)
                self.assertEqual(instance.state(53).counter, 1)
                self.assertEqual(health.total, initial - (175 if pilot_form else 170))
                self.assertFalse(any(r.shooter is bot for r in run.match.combat.rockets))
                if not pilot_form:
                    active = next(i for i in target.owner.body_script.component.instances.values()
                                  if i.graph.index == 0x2CC)
                    self.assertTrue(active.state(3).active)
                for _ in range(20):
                    run.step({})
                self.assertEqual(instance.state(53).counter, 1)

    def test_heavy_charge_spends_projected_barrier_on_either_dva_form(self):
        for pilot_form in (False, True):
            for barrier_health in (200, 5):
                with self.subTest(pilot_form=pilot_form, barrier_health=barrier_health):
                    self.check_charge_barrier(pilot_form, barrier_health)

    def check_charge_barrier(self, pilot_form, barrier_health):
        run, pilot, ana, soldier, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=True,
        )
        ana.spawned = soldier.spawned = False
        match = run.match
        fight = match.combat
        zarya = run.players[3]
        zarya.team = pilot.team
        self.assertTrue(match.switch_hero(zarya, find_hero("zarya")))
        x, y, z = target.feet
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        a, b = target.segment()
        centre = tuple((p + q) / 2 for p, q in zip(a, b, strict=True))
        yaw, pitch = aim(fight._eye(zarya), centre)
        for _ in range(30):
            run.step({zarya: (yaw, pitch, 0)})
        bot = npc.spawn(match, self.by_name["Heavy Assault"], (x, y, z + 6), team=1)
        npc_control.engage(match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({zarya: (yaw, pitch, 0)})
            if bot.charge_mover is not None:
                break
        self.assertIsNotNone(bot.charge_mover)
        before = health.total
        run.step({zarya: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = barrier_health
        instance = npc_charge.instance(bot)
        for _ in range(40):
            run.step({})
            if instance.state(53).counter:
                break
        self.assertEqual(instance.state(53).counter, 1)
        self.assertEqual(health.total, before)
        self.assertEqual(bubble.health, max(0, barrier_health - 175))
        self.assertIs(fight.zarya_bubbles.for_target(target.owner), bubble if barrier_health == 200 else None)
        for _ in range(25):
            run.step({})
        self.assertEqual(instance.state(53).counter, 1)
        self.assertEqual(health.total, before)
        self.assertGreater(instance.state(41).remaining(), 14)
        self.assertIs(fight.player_target(pilot).owner, target.owner)

    def test_authored_charge_can_kill_normal_pilot(self):
        run, pilot, bot, target, health = self.scene(pilot_form=True)
        npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertEqual(npc_charge.instance(bot).state(53).counter, 1)
        self.assertNotEqual(target.entity, bot.entity)

    def test_charge_stops_on_new_wall_or_manual_stop(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, pilot, bot, target, health = self.scene()
                x, y, z = target.feet
                npc_control.engage(run.match, bot, pilot, charge=True)
                for _ in range(100):
                    run.step({})
                    if bot.charge_mover is not None:
                        break
                self.assertIsNotNone(bot.charge_mover)
                self.assertEqual(health.total, 600)
                moving_from = bot.position[2]
                for _ in range(4):
                    run.step({})
                self.assertAlmostEqual(moving_from - bot.position[2], 4 * 15 * combat.TICK, delta=0.01)
                if cover:
                    run.match.collision_world = World(
                        box(x - 12, x + 12, 0, y, z - 8, z + 12)
                        + wall(x - 6, z + 2, x + 6, z + 2)
                    )
                else:
                    npc_control.stop(run.match)
                for _ in range(45):
                    run.step({})
                self.assertIsNone(bot.charge_mover)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                self.assertGreater(bot.position[2], z + 2)
                self.assertEqual(health.total, 600)
                self.assertGreater(npc_charge.instance(bot).state(41).remaining(), 14)

    def test_actual_fusion_kills_charging_npc_without_late_impact(self):
        run, pilot, bot, target, health = self.scene()
        fight = run.match.combat
        npc_health = combat.Vitals(bot.entity, 1)
        fight.vitals[bot.entity] = npc_health
        npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({})
            if bot.charge_mover is not None:
                break
        self.assertIsNotNone(bot.charge_mover)
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(target.owner), (x, y + 1.1, z))
        run.step({pilot: (yaw, pitch, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(npc_health.killer, target.entity)
        self.assertIsNone(bot.charge_mover)
        stopped = bot.position
        for _ in range(40):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(health.total, 600)

    def test_stop_cancels_actual_windup_and_preserves_cooldown(self):
        run, pilot, bot, _, health = self.scene()
        initial = bot.position
        npc_control.engage(run.match, bot, pilot, charge=True)
        instance = npc_charge.instance(bot)
        for _ in range(60):
            run.step({})
            windup = instance.states.get(71)
            if windup is not None and windup.active:
                break
        self.assertIsNotNone(windup)
        self.assertTrue(windup.active)
        self.assertIsNone(bot.charge_mover)
        npc_control.stop(run.match)
        for _ in range(70):
            run.step({})
        self.assertEqual(bot.position, initial)
        self.assertIsNone(bot.charge_mover)
        self.assertEqual(health.total, 600)
        self.assertEqual(instance.state(33).counter, 0)
        self.assertGreater(instance.state(41).remaining(), 13)

    def test_controlled_boosters_interrupt_charge_and_preserve_knockback(self):
        run, pilot, bot, _, health = self.scene()
        fight = run.match.combat
        npc_health = fight.vitals[bot.entity]
        npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({})
            if bot.charge_mover is not None:
                break
        self.assertIsNotNone(bot.charge_mover)
        initial = npc_health.total
        run.step({pilot: (0, 0, 8)})
        for _ in range(30):
            run.step({})
            if npc_health.total < initial:
                break
        self.assertEqual(npc_health.total, initial - 10)
        self.assertIsNone(bot.charge_mover)
        self.assertIsNotNone(bot.impulse_mover)
        self.assertGreater(bot.velocity[2], 0)
        z = bot.position[2]
        for _ in range(4):
            run.step({})
        self.assertGreater(bot.position[2], z)
        self.assertEqual(health.total, 600)
        instance = npc_charge.instance(bot)
        ability = instance.state(41)
        self.assertGreater(ability.remaining(), 14)
        self.assertEqual(instance.state(53).counter, 0)

        npc_control.stop(run.match)
        for _ in range(180):
            run.step({})
            if bot.impulse_mover is None:
                break
        self.assertIsNone(bot.impulse_mover)
        target = fight.player_target(pilot)
        x, y, z = target.feet
        bot.position = (x, y, z + 6)
        npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({})
            self.assertIsNone(bot.charge_mover)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(health.total, 600)
        self.assertEqual(instance.state(53).counter, 0)
        npc_control.stop(run.match)
        for _ in range(1100):
            if ability.remaining() == 0:
                break
            run.step({})
            self.assertIsNone(bot.charge_mover)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(health.total, 600)

        npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({})
            if instance.state(53).counter:
                break
        self.assertEqual(instance.state(53).counter, 1)
        self.assertEqual(health.total, 430)
        self.assertGreater(ability.remaining(), 14)

    def test_lethal_charge_releases_mech_without_reusing_impact_on_pilot(self):
        run, pilot, bot, target, _ = self.scene()
        fight = run.match.combat
        mech_health = combat.Vitals(target.entity, 1)
        fight.vitals[target.entity] = mech_health
        npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(100):
            run.step({})
            if pilot.controlled_mech is None:
                break
        self.assertTrue(mech_health.dead)
        self.assertEqual(mech_health.killer, bot.entity)
        self.assertIsNone(pilot.controlled_mech)
        target_now = fight.player_target(pilot)
        self.assertIs(target_now.owner, pilot)
        health = fight.vitals_of(target_now)
        for _ in range(30):
            run.step({})
        self.assertEqual(health.total, 150)
        self.assertEqual(npc_charge.instance(bot).state(53).counter, 1)
