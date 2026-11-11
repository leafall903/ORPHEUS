"""Actual owned D.Va Micro Missiles against Hanzo and his defenses."""

import math
import unittest
from unittest.mock import patch

import test_game_hanzo_owned_mech as mech_tests
from test_game_combat import aim, in_world
from test_server_journal import wall_world

from ow174.game import arrow_deflection, combat, matrix, sigma_grasp, weapon_spread, zarya_barrier
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.script.expr import to_float
from ow174.game.script.nodes import Ability


class HanzoOwnedMissileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.HanzoOwnedMechTests.setUpClass()

    def arena(self, distance=2):
        run, pilot, mech, hanzo, _, _ = mech_tests.HanzoOwnedMechTests().arena(distance=distance)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is hanzo)
        fight.vitals_of(target)
        x, y, z = mech.position
        hx, hy, hz = hanzo.mover.position
        angles = aim((x, y + combat.eye_height(mech.body), z), (hx, hy + 1.1, hz))
        return run, pilot, mech, hanzo, angles

    def volley(self, run, pilot, angles, *, defender=None, buttons=0):
        defense = {defender: (-32768, 0, buttons)} if defender is not None else {}
        run.step({pilot: (*angles, 16), **defense})
        for _ in range(159):
            run.step({pilot: (*angles, 0), **defense})

    def test_tap_fires_all_eighteen_and_near_expiry_tap_does_not_buffer(self):
        run, pilot, mech, hanzo, angles = self.arena()
        fight = run.match.combat
        hanzo_vitals = fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, 500)
        ability = next(i.states[0] for i in mech.body_script.component.instances.values()
                       if i.graph.index == 0x1128)
        self.assertIsNone(ability.node.field("m_246438AD"))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.volley(run, pilot, angles)
            self.assertEqual([(c.args[2].instance.graph.index, c.args[2].node.pos, c.args[3])
                              for c in shoot.call_args_list], [(0x1128, 10, i) for i in range(18)])
            self.assertEqual(hanzo_vitals.total, 374)
            hits = [notice["+0x78"]["+0x4"] for notice in pilot.client.messages(combat.HIT)]
            self.assertEqual(hits, [3, 4] * 18)
            self.assertEqual(mech.body_script.component.entity_var(ULT_CHARGE).value(), 126)
            for _ in range(700):
                if ability.remaining() <= 0.08:
                    break
                run.step({})
            self.assertGreater(ability.remaining(), 0.032)
            run.step({pilot: (*angles, 16)})
            self.assertFalse(ability.flags & Ability.RUNNING)
            for _ in range(12):
                run.step({pilot: (*angles, 0)})
            self.assertEqual(ability.remaining(), 0)
            self.assertEqual(shoot.call_count, 18)
            self.volley(run, pilot, angles)
            self.assertEqual(shoot.call_count, 36)
            self.assertEqual(hanzo_vitals.total, 248)

    def test_launch_uses_quantized_direction_and_admitted_projectile(self):
        run, pilot, mech, _, _ = self.arena()
        fight = run.match.combat
        angles = 16384, 16250
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for frame in range(40):
                run.step({pilot: (*angles, 16 if frame == 0 else 0)})
                if fight.rockets:
                    break
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, mech)
        self.assertEqual(rocket.source_entity, mech.entity)
        self.assertEqual(fired.call_args.args[3], 0x0400000000001E8A)
        self.assertEqual(rocket.direction, weapon_spread.single_launch_direction(combat.aim(*angles)))
        self.assertGreater(math.dist(rocket.direction, combat.aim(*angles)), .01)
        self.assertEqual((rocket.speed, rocket.left, rocket.range_left, rocket.gravity),
                         (40, 2.5, 100, 0))
        self.assertTrue(rocket.matrix_absorbable)
        self.assertTrue(rocket.direct.deflectable)

    def test_wall_ally_and_lifetime_prevent_hanzo_damage(self):
        for case, distance in (("wall", 9), ("ally", 2), ("lifetime", 101)):
            with self.subTest(case=case):
                run, pilot, mech, hanzo, angles = self.arena(distance)
                fight = run.match.combat
                vitals = fight.vitals[hanzo.body]
                if case == "wall":
                    run.match.collision_world = wall_world()
                elif case == "ally":
                    pilot.team = mech.team = hanzo.team
                self.volley(run, pilot, angles)
                for _ in range(330):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(pilot.client.messages(combat.HIT))

    def add_defender(self, run, hanzo, hero, *, x=0, z=4, warmup=80):
        match = run.match
        defender = match.add_player(3, "Defender", hero, hanzo.team, False)
        in_world(match, defender)
        run.players.append(defender)
        position = (x, hanzo.mover.position[1], z)
        defender.spawn = position, 180
        self.assertTrue(match.switch_hero(defender, heroes()[hero]))
        mover = defender.controlled_mech.control_mover if defender.controlled_mech else defender.mover
        mover.position[:] = position
        mover.body = None
        for _ in range(warmup):
            run.step({})
        return defender

    def test_active_genji_reflects_missiles_with_source_credit(self):
        run, pilot, mech, hanzo, angles = self.arena(9)
        fight = run.match.combat
        genji = self.add_defender(run, hanzo, arrow_deflection.GENJI)
        self.volley(run, pilot, angles, defender=genji, buttons=16)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        self.assertLess(fight.vitals[mech.entity].total, 600)
        self.assertTrue(genji.client.messages(combat.HIT))
        self.assertFalse(pilot.client.messages(combat.HIT))
        self.assertEqual(to_float(mech.body_script.component.entity_var(ULT_CHARGE).value()), 0)

    def test_active_owned_matrix_absorbs_missiles_before_explosion(self):
        run, pilot, mech, hanzo, angles = self.arena(9)
        fight = run.match.combat
        defender = self.add_defender(run, hanzo, matrix.DVA)
        self.volley(run, pilot, angles, defender=defender, buttons=2)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertEqual(fight.vitals[defender.controlled_mech.entity].total, 600)
        self.assertFalse(pilot.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)

    def test_actual_bow_interrupts_salvo_and_preserves_a_launched_missile(self):
        for launched in (False, True):
            with self.subTest(launched=launched):
                run, pilot, mech, hanzo, angles = self.arena(6)
                fight = run.match.combat
                mech_vitals = fight.vitals[mech.entity] = combat.Vitals(mech.entity, 1)
                x, y, z = mech.position
                bow = aim(fight._eye(hanzo), (x, y + 1.1, z))
                for _ in range(70):
                    run.step({hanzo: (*bow, 1)})
                run.step({pilot: (*angles, 16), hanzo: (*bow, 1)})
                self.assertFalse(fight.rockets)
                if launched:
                    for _ in range(40):
                        run.step({pilot: (*angles, 0), hanzo: (*bow, 1)})
                        if fight.rockets:
                            break
                    missile, = fight.rockets
                    self.assertIs(missile.shooter, mech)
                run.step({hanzo: (*bow, 0)})
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(140):
                        run.step({})
                self.assertTrue(mech_vitals.dead)
                self.assertIsNone(pilot.controlled_mech)
                self.assertFalse(shoot.call_args_list)
                self.assertEqual(fight.vitals[hanzo.body].total, 193 if launched else 200)
                self.assertEqual(to_float(mech.body_script.component.entity_var(ULT_CHARGE).value()), 0)
                self.assertEqual(len(pilot.client.messages(combat.HIT)), 2 if launched else 0)

    def test_owned_matrix_entry_lock_allows_first_three_missiles(self):
        run, pilot, _, hanzo, angles = self.arena(9)
        fight = run.match.combat
        defender = self.add_defender(run, hanzo, matrix.DVA, warmup=25)
        self.volley(run, pilot, angles, defender=defender, buttons=2)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertEqual(fight.vitals[defender.controlled_mech.entity].total, 589.5)
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 6)

    def test_active_grasp_converts_the_eighteen_intercepted_missiles(self):
        run, pilot, _, hanzo, angles = self.arena(9)
        fight = run.match.combat
        defender = self.add_defender(run, hanzo, sigma_grasp.SIGMA)
        for frame in range(160):
            run.step({pilot: (*angles, 16 if frame == 0 else 0),
                      defender: (-32768, 0, 8 if frame >= 12 else 0)})
        vitals = fight.vitals[defender.body]
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertAlmostEqual(vitals.total - vitals.bonus_shields, 400, delta=1e-5)
        self.assertAlmostEqual(vitals.bonus_shields, 75.6, delta=1e-5)
        self.assertFalse(pilot.client.messages(combat.HIT))

    def test_projected_barrier_blocks_the_whole_breaking_missile(self):
        run, pilot, mech, hanzo, angles = self.arena()
        fight = run.match.combat
        hx, hy, hz = hanzo.mover.position
        zarya = self.add_defender(run, hanzo, zarya_barrier.HERO, x=4, z=hz + 2)
        shield = aim(fight._eye(zarya), (hx, hy + 1, hz))
        run.step({zarya: (*shield, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, hanzo.body))
        fight.zarya_barriers.damage(hanzo, 191, mech.entity)
        self.volley(run, pilot, angles)
        self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
        self.assertEqual(fight.vitals[hanzo.body].total, 88)
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 32)

    def test_ninety_nine_metres_is_inside_projectile_range(self):
        run, pilot, _, hanzo, angles = self.arena(99)
        fight = run.match.combat
        self.volley(run, pilot, angles)
        for _ in range(170):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[hanzo.body].total, 74)

    def test_ground_impact_splash_falls_off_to_the_outer_ring(self):
        for offset, expected in ((1, 147.9814), (1.9, 180.6526), (2.5, 200)):
            with self.subTest(offset=offset):
                run, pilot, mech, hanzo, _ = self.arena(6)
                fight = run.match.combat
                hanzo.mover.position[0] = offset
                hanzo.mover.body = None
                x, y, z = mech.position
                angles = aim((x, y + combat.eye_height(mech.body), z), (0, -0.001, 6))
                self.volley(run, pilot, angles)
                self.assertAlmostEqual(fight.vitals[hanzo.body].total, expected, delta=0.0001)
                self.assertEqual(len(pilot.client.messages(combat.HIT)), 18 if offset < 2 else 0)
