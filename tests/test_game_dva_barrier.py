"""D.Va live weapon commands against the authored Reinhardt barrier mesh."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_dva_created_body as created_body_tests
from test_game_combat import aim, dva_pilot_input

from ow174.game import body_entities, combat, reinhardt_barrier
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReinhardtBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("ORPHEUS_REINHARDT_BARRIER_GEOMETRY"):
            raise unittest.SkipTest("requires the build 104319 Reinhardt barrier collision mesh")
        created_body_tests.CreatedBodyTests.setUpClass()

    def scene(self, pilot_form=False, distance=4):
        run, pilot, defender, _, mech = created_body_tests.CreatedBodyTests().scenario()
        self.assertTrue(run.match.switch_hero(defender, find_hero("reinhardt")))
        if pilot_form:
            dva_pilot_input(pilot)
            pilot.mover.position[0] += 10
            pilot.mover.body = None
        actor = pilot if pilot_form else mech
        x, y, z = pilot.mover.position if pilot_form else mech.position
        defender.mover.position[:] = (x, y, z + distance)
        defender.mover.body = None
        for _ in range(100):
            run.step({})
        for _ in range(35):
            run.step({defender: (-32768, 0, 2)})
        barrier = reinhardt_barrier.active(defender)
        self.assertIsNotNone(barrier)
        self.assertEqual(barrier.vars[586].value(), 1600)
        fight = run.match.combat
        health = fight.vitals_of(fight.player_target(defender))
        x, y, z = defender.mover.position
        yaw, pitch = aim(fight._eye(actor), (x, y + 1.4, z))
        return run, pilot, defender, barrier, health, yaw, pitch

    def test_actual_weapons_spend_barrier_health_without_hitting_body(self):
        for pilot_form, button in ((False, 1), (True, 1), (False, 16)):
            with self.subTest(pilot_form=pilot_form, button=button):
                run, pilot, defender, barrier, health, yaw, pitch = self.scene(pilot_form)
                run.step({pilot: (yaw, pitch, button), defender: (-32768, 0, 2)})
                frames = 240 if button == 16 else 25
                for _ in range(frames):
                    run.step({pilot: (yaw, pitch, 0), defender: (-32768, 0, 2)})
                self.assertEqual(health.total, 500)
                self.assertEqual(pilot.client.messages(combat.HIT), [])
                damage = 126 if button == 16 else (14 if pilot_form else 22)
                self.assertEqual(barrier.vars[586].value(), 1600 - damage)

    def test_lowered_barrier_exposes_reinhardt_to_actual_fusion_cannons(self):
        run, pilot, defender, barrier, health, yaw, pitch = self.scene()
        for _ in range(8):
            run.step({defender: (-32768, 0, 0)})
        self.assertIsNone(reinhardt_barrier.active(defender))
        run.step({pilot: (yaw, pitch, 1)})
        self.assertLess(health.total, 500)
        self.assertEqual(barrier.vars[586].value(), 1600)

    def test_actual_fusion_break_opens_path_for_later_pellets(self):
        run, pilot, defender, barrier, health, yaw, pitch = self.scene()
        barrier.write((expr.INSTANCE, 586), expr.f32(6))
        barrier.component.run_now()
        run.step({pilot: (yaw, pitch, 1), defender: (-32768, 0, 2)})
        self.assertEqual(barrier.vars[586].value(), 0)
        self.assertIsNone(reinhardt_barrier.active(defender))
        self.assertLess(health.total, 500)

    def test_breaking_micro_missile_is_blocked_before_later_rounds_hit(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        run, pilot, defender, barrier, health, yaw, pitch = self.scene()
        charge = pilot.controlled_mech.body_script.component.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        barrier.write((expr.INSTANCE, 586), expr.f32(10))
        barrier.component.run_now()
        run.step({pilot: (yaw, pitch, 16), defender: (-32768, 0, 2)})
        for _ in range(45):
            run.step({defender: (-32768, 0, 2)})
            if barrier.vars[586].value() == 0:
                break
        self.assertEqual(barrier.vars[586].value(), 0)
        self.assertIsNone(reinhardt_barrier.active(defender))
        self.assertEqual(health.total, 500)
        self.assertEqual(charge.value(), initial_charge)
        self.assertFalse(pilot.client.messages(combat.HIT))
        for _ in range(150):
            run.step({defender: (-32768, 0, 2)})
        self.assertEqual(health.total, 444)
        self.assertEqual(charge.value(), 56)
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 32)

    def test_wall_and_allied_barrier_ordering(self):
        from test_game_mover import box, wall

        for covered in (False, True):
            with self.subTest(covered=covered):
                run, pilot, defender, barrier, health, yaw, pitch = self.scene()
                match = run.match
                if covered:
                    x, y, z = pilot.controlled_mech.position
                    match.collision_world = World(
                        box(x - 10, x + 10, 0, y, z - 10, z + 10)
                        + wall(x - 5, z + 1.5, x + 5, z + 1.5)
                    )
                else:
                    match.game_map = replace(match.game_map, free_for_all=False)
                    defender.team = pilot.team
                run.step({pilot: (yaw, pitch, 1), defender: (-32768, 0, 2)})
                self.assertEqual(health.total, 500)
                self.assertEqual(barrier.vars[586].value(), 1600)

    def test_actual_damage_waits_for_lowered_barrier_before_regeneration(self):
        run, pilot, defender, barrier, _, yaw, pitch = self.scene()
        run.step({pilot: (yaw, pitch, 1), defender: (-32768, 0, 2)})
        for _ in range(135):
            run.step({defender: (-32768, 0, 2)})
        self.assertEqual(barrier.vars[586].value(), 1578)
        for _ in range(115):
            run.step({defender: (-32768, 0, 0)})
        self.assertEqual(barrier.vars[586].value(), 1578)
        for _ in range(32):
            run.step({defender: (-32768, 0, 0)})
        self.assertEqual(barrier.vars[586].value(), 1600)
        for _ in range(35):
            run.step({defender: (-32768, 0, 2)})
        self.assertIs(reinhardt_barrier.active(defender), barrier)

    def test_detached_blast_spends_shield_once_and_protects_body_even_when_shield_breaks(self):
        for initial in (1600, 600):
            with self.subTest(initial=initial):
                run, pilot, defender, barrier, health, _, _ = self.scene()
                if initial != 1600:
                    barrier.write((expr.INSTANCE, 586), expr.f32(initial))
                    barrier.component.run_now()
                bomb = self.arm_bomb(run, pilot, defender)
                for _ in range(185):
                    run.step({defender: (-32768, 0, 2)})
                self.assertEqual(barrier.vars[586].value(), initial)
                self.assertEqual(health.total, 500)
                run.step({defender: (-32768, 0, 2)})
                self.assertEqual(barrier.vars[586].value(), max(0, initial - 1000))
                self.assertEqual(health.total, 500)
                self.assertEqual(defender.mover.velocity[2], 0)
                self.assertFalse(pilot.client.messages(combat.HIT))
                for _ in range(25):
                    run.step({defender: (-32768, 0, 2)})
                self.assertNotIn(bomb, run.match.bots)
                self.assertEqual(barrier.vars[586].value(), max(0, initial - 1000))

    def test_detached_blast_uses_outer_curve_at_shield_surface(self):
        run, pilot, defender, barrier, health, _, _ = self.scene(distance=10)
        bomb = self.arm_bomb(run, pilot, defender)
        for _ in range(185):
            run.step({defender: (-32768, 0, 2)})
        x, y, z = bomb.position
        hit, = reinhardt_barrier.blast_targets(run.match.combat, bomb, (x, y + 1.3, z), 20)
        self.assertIs(hit[0], barrier)
        distance = hit[1]
        self.assertGreater(distance, 4)
        self.assertLess(distance, 10)
        run.step({defender: (-32768, 0, 2)})
        expected_damage = 50 + 850 * (20 - distance) / (20 - 4)
        self.assertAlmostEqual(barrier.vars[586].value(), 1600 - expected_damage, delta=0.002)
        self.assertEqual(health.total, 500)

    def test_detached_blast_respects_wall_and_ally_and_exposes_body_with_shield_behind(self):
        from test_game_mover import box, wall

        for case in ("wall", "ally", "behind"):
            with self.subTest(case=case):
                run, pilot, defender, barrier, health, _, _ = self.scene()
                match = run.match
                facing = 0 if case == "behind" else -32768
                if case == "wall":
                    x, y, z = pilot.controlled_mech.position
                    match.collision_world = World(
                        box(x - 10, x + 10, 0, y, z - 10, z + 10)
                        + wall(x - 5, z + 1.5, x + 5, z + 1.5)
                    )
                elif case == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    defender.team = pilot.team
                else:
                    health = combat.Vitals(defender.body, 5000)
                    match.combat.vitals[defender.body] = health
                self.arm_bomb(run, pilot, defender, facing)
                for _ in range(186):
                    run.step({defender: (facing, 0, 2)})
                if case == "behind":
                    self.assertEqual(health.total, 4000)
                    self.assertGreater(defender.mover.velocity[2], 0)
                else:
                    self.assertEqual(health.total, 500)
                    self.assertEqual(barrier.vars[586].value(), 1600)

    def test_detached_blast_acquires_shield_even_with_body_outside_range(self):
        for placement, expect_hit in ((20.85, True), (23, False)):
            with self.subTest(placement=placement):
                run, pilot, defender, barrier, health, _, _ = self.scene(distance=placement)
                fight = run.match.combat
                bomb = self.arm_bomb(run, pilot, defender)
                for _ in range(185):
                    run.step({defender: (-32768, 0, 2)})
                x, y, z = bomb.position
                target = fight.player_target(defender)
                a, b = target.segment()
                self.assertGreater(combat.segment_distance((x, y + 1.3, z), a, b) - target.radius, 20)
                run.step({defender: (-32768, 0, 2)})
                self.assertEqual(barrier.vars[586].value() < 1600, expect_hit)
                self.assertEqual(health.total, 500)

    def arm_bomb(self, run, pilot, defender, facing=-32768):
        bomb = pilot.controlled_mech
        component = bomb.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32), defender: (facing, 0, 2)})
        run.step({defender: (facing, 0, 2)})
        self.assertTrue(bomb.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)
        return bomb
