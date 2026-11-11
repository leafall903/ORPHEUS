"""Actual D.Va attacks and Sigma Kinetic Grasp in the 104319 Match."""

import math
import unittest
from unittest.mock import patch

import test_game_dva_ana as ana_tests
import test_game_dva_created_body as created_tests
import test_game_dva_manual_npcs as manual_tests
from test_game_combat import aim

from ow174.game import body_entities, combat, deflection, npc, npc_control, sigma_grasp
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class DvaSigmaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ana_tests.DvaAnaTests.setUpClass()

    def scene(self, *, pilot_form=False, distance=4, facing=-32768, activate=True):
        run, pilot, sigma, _, source, _ = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
        x, y, z = source.feet
        sigma.mover.position[:] = (x, y, z + distance)
        sigma.mover.body = None
        for _ in range(30):
            run.step({sigma: (facing, 0, 0)})
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        if activate:
            run.step({sigma: (facing, 0, 8)})
            for _ in range(30):
                if instance.state(1).active:
                    break
                run.step({sigma: (facing, 0, 0)})
            self.assertTrue(instance.state(1).active)
        health = fight.vitals_of(fight.player_target(sigma))
        yaw, pitch = aim(fight._eye(source.owner), (x, y + 1.35, z + distance))
        return run, pilot, sigma, health, instance, yaw, pitch

    def test_actual_fusion_light_gun_and_missiles_convert_to_temporary_shields(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for pilot_form, attack, expected in ((False, 1, 22), (True, 1, 14), (False, 16, 126)):
            with self.subTest(pilot_form=pilot_form, attack=attack):
                run, pilot, sigma, health, _, yaw, pitch = self.scene(pilot_form=pilot_form)
                pilot.body_script.ult_seconds = 0
                actor = pilot.controlled_mech or pilot
                charge = actor.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value()
                base = health.total
                run.step({pilot: (yaw, pitch, attack), sigma: (-32768, 0, 0)})
                for _ in range(145):
                    run.step({sigma: (-32768, 0, 0), pilot: (yaw, pitch, 0)})
                self.assertEqual(sum(health.now.values()), base)
                self.assertFalse(pilot.client.messages(combat.HIT))
                self.assertEqual(charge.value(), initial_charge)
                bonus = health.temporary_shields[sigma_grasp.SHIELD_TAG].now
                self.assertAlmostEqual(bonus, expected * 0.6, places=4)

    def test_allied_grasp_converts_enforcer_falloff_at_contact_before_either_dva_form(self):
        fixture = manual_tests.ManualIncomingTests
        fixture.setUpClass()
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, sigma, sigma_health, instance, _, _ = self.scene(
                    pilot_form=pilot_form, distance=3, facing=0, activate=False,
                )
                match, fight = run.match, run.match.combat
                run.players[2].spawned = False
                sigma.team = pilot.team
                target = fight.player_target(pilot)
                health = fight.vitals_of(target)
                x, y, z = target.feet
                bot = npc.spawn(match, fixture.by_name["Enforcer"], (x, y, z + 16), team=1)
                npc_control.engage(match, bot, pilot)
                for _ in range(50):
                    run.step({})
                run.step({sigma: (0, 0, 8)})
                for _ in range(40):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), 25)
                grasp = instance.state(1)
                self.assertTrue(grasp.active)
                expected = []
                for shot in fight.rockets:
                    hit = fight._absorb_contact(bot, shot.position, shot.direction, 100, shot.definition)
                    if hit is not None:
                        distance, state = hit
                        self.assertIs(state, grasp)
                        self.assertGreater(distance, 8)
                        self.assertLess(distance, 15)
                        expected.append(2 * (1 - .8 * (distance - 8) / 7))
                self.assertEqual(len(expected), 25)
                npc_control.stop(match)
                pilot_before, sigma_before = health.total, sigma_health.total
                with patch.object(sigma_grasp, "absorb", wraps=sigma_grasp.absorb) as absorb:
                    for _ in range(145):
                        run.step({})
                amounts = [call.args[2] for call in absorb.call_args_list]
                self.assertEqual(len(amounts), len(expected))
                for actual, wanted in zip(sorted(amounts), sorted(expected), strict=True):
                    self.assertAlmostEqual(actual, wanted, places=6)
                self.assertEqual(health.total, pilot_before)
                self.assertEqual(sum(sigma_health.now.values()), sigma_before)
                bonus = sigma_health.temporary_shields[sigma_grasp.SHIELD_TAG].now
                self.assertAlmostEqual(bonus, .6 * sum(expected), places=4)

    def test_active_grasp_accepts_a_rear_incidence_that_crosses_its_front_box(self):
        run, _, sigma, _, instance, _, _ = self.scene()
        state = instance.state(1)
        self.assertEqual(state.evaluate(state.node.field("m_025D0C39"), watch=False), 180)
        anchor = run.match.combat._eye(sigma)
        x, y, z = anchor
        contact = deflection.grasp_contact(
            sigma.body_script, (x + 1.2, y, z - 1), (-0.8, 0, -0.6), 2,
            anchor, (0, 0, -1),
        )
        self.assertIsNotNone(contact)
        self.assertAlmostEqual(contact[0], 0.25)
        self.assertIs(contact[1], state)

    def test_actual_fusion_from_behind_bypasses_grasp(self):
        run, pilot, sigma, health, _, yaw, pitch = self.scene(facing=0)
        initial = health.total
        run.step({pilot: (yaw, pitch, 1), sigma: (0, 0, 0)})
        self.assertLess(health.total, initial)
        self.assertFalse(health.temporary_shields)

    def test_actual_quick_melee_damages_sigma_through_active_grasp(self):
        run, pilot, sigma, health, _, yaw, pitch = self.scene(distance=1.8, pilot_form=True)
        initial = health.total
        run.step({pilot: (yaw, pitch, 0x800), sigma: (-32768, 0, 0)})
        for _ in range(20):
            run.step({pilot: (yaw, pitch, 0), sigma: (-32768, 0, 0)})
        self.assertEqual(health.total, initial - 30)
        self.assertFalse(health.temporary_shields)

    def test_grasp_bonus_uses_damage_first_and_world_clock_decay(self):
        run, pilot, sigma, health, _, yaw, pitch = self.scene()
        fight = run.match.combat
        initial = health.total
        run.step({pilot: (yaw, pitch, 1), sigma: (-32768, 0, 0)})
        for _ in range(145):
            run.step({sigma: (-32768, 0, 0)})
        bonus = health.temporary_shields[sigma_grasp.SHIELD_TAG]
        record = fight.grasp_shields[sigma.body]
        self.assertGreater(record.starts, fight.now)
        x, y, z = fight.player_target(sigma).feet
        yaw, pitch = aim(fight._eye(pilot.controlled_mech), (x, y + .8, z))
        run.step({pilot: (yaw, pitch, 1)})
        self.assertEqual(bonus.now, 0)
        self.assertLess(health.total, initial)
        self.assertAlmostEqual(health.total, initial + 13.2 - 22, places=4)

        run, pilot, sigma, health, _, yaw, pitch = self.scene()
        fight = run.match.combat
        run.step({pilot: (yaw, pitch, 1), sigma: (-32768, 0, 0)})
        for _ in range(145):
            run.step({sigma: (-32768, 0, 0)})
        record = fight.grasp_shields[sigma.body]
        pool = health.temporary_shields[sigma_grasp.SHIELD_TAG]
        initial = pool.now
        run.frame = math.floor(record.starts / combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(pool.now, initial)
        run.frame += 32
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(pool.now, initial - (fight.now - record.starts) * 5)

    def test_actual_self_destruct_damages_sigma_through_active_grasp(self):
        run, pilot, sigma, _, instance, _, _ = self.scene(activate=False)
        fight = run.match.combat
        health = combat.Vitals(sigma.body, 5000)
        fight.vitals[sigma.body] = health
        fight._sync_body_health(sigma, health)
        mech = pilot.controlled_mech
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertTrue(mech.detached_bomb)
        for _ in range(155):
            run.step({sigma: (-32768, 0, 0)})
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(30):
            run.step({sigma: (-32768, 0, 0)})
        self.assertTrue(instance.state(1).active)
        self.assertEqual(health.total, 4000)
        self.assertFalse(health.temporary_shields)

    def test_actual_call_mech_damages_sigma_during_active_grasp_without_shield_gain(self):
        run, pilot, sigma, call_mech, old = created_tests.CreatedBodyTests().ready_call_mech()
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(sigma, find_hero("sigma")))
        run.step({pilot: (0, 0, 32)})
        for _ in range(24):
            run.step({})
        mech, = match.bots
        self.assertIsNot(mech, old)
        x, y, z = mech.position
        sigma.mover.position[:] = (x, y, z + 2)
        sigma.mover.body = None
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        health = fight.vitals_of(fight.player_target(sigma))
        before = health.total
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(45):
            run.step({sigma: (-32768, 0, 0)})
            if call_mech.state(20).counter:
                break
        self.assertEqual(call_mech.state(20).counter, 1)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(before - health.total, 250)
        self.assertGreater(sigma.mover.velocity[2], 20)
        self.assertGreater(sigma.mover.velocity[1], 2)
        for _ in range(145):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(grasp.state(1).active)
        self.assertFalse(health.temporary_shields)
        self.assertNotIn(sigma.body, fight.grasp_shields)

    def test_wall_precedes_active_grasp_contact(self):
        from test_game_mover import box, wall

        run, pilot, sigma, health, _, yaw, pitch = self.scene()
        fight = run.match.combat
        x, y, z = fight.player_target(pilot).feet
        run.match.collision_world = World(
            box(x - 10, x + 10, 0, y, z - 10, z + 10)
            + wall(x - 5, z + 1, x + 5, z + 1)
        )
        before = health.total
        run.step({pilot: (yaw, pitch, 1), sigma: (-32768, 0, 0)})
        for _ in range(145):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(health.total, before)
        self.assertFalse(health.temporary_shields)

    def test_authored_grant_clamps_a_large_absorption_callback_to_four_hundred(self):
        run, pilot, sigma, health, instance, _, _ = self.scene()
        # Exercise the authored conversion cap independently of the real weapon tests.
        sigma_grasp.absorb(instance.state(1), pilot.controlled_mech.entity, 720)
        for _ in range(145):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(health.temporary_shields[sigma_grasp.SHIELD_TAG].now, 400)
