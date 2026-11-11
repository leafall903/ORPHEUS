"""Actual Fusion Cannon attacks from normally selected owned D.Va mechs."""

import math
import unittest
from unittest.mock import patch

import test_game_hanzo_owned_mech as mech_tests
from test_game_combat import aim, in_world
from test_server_journal import wall_world

from ow174.game import arrow_deflection, combat, weapon_spread, zarya_barrier
from ow174.game.content import heroes
from ow174.game.script.expr import to_float
from ow174.game.script.driver import ULT_CHARGE


class HanzoOwnedFusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.HanzoOwnedMechTests.setUpClass()

    def fire(self, distance, *, height=0.85, wall=False, ally=False, health=None, cover=None):
        fixture = mech_tests.HanzoOwnedMechTests()
        run, pilot, mech, hanzo, _, _ = fixture.arena(distance=distance)
        match = run.match
        fight = match.combat
        if wall:
            match.collision_world = wall_world()
        if ally:
            pilot.team = mech.team = hanzo.team
        if health is not None:
            fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, health)
        caster = None
        if cover is not None:
            x, y, z = hanzo.mover.position
            caster = self.add_defender(run, hanzo, zarya_barrier.HERO, (x + 4, y, z + 2))
            angles = aim(fight._eye(caster), (x, y + 1, z))
            run.step({caster: (*angles, 16)})
            self.assertEqual(fight.zarya_barriers.projected[caster.body][1:], (200, hanzo.body))
            fight.zarya_barriers.damage(hanzo, 200 - cover, mech.entity)
        x, y, z = mech.position
        hx, hy, hz = hanzo.mover.position
        yaw, pitch = aim((x, y + combat.eye_height(mech.body), z), (hx, hy + height, hz))
        pilot.body_script.ult_seconds = 0
        pilot_charge = pilot.body_script.component.entity_var(ULT_CHARGE).value()
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rays:
            run.step({pilot: (yaw, pitch, 1)})
            for _ in range(25):
                run.step({})
        self.assertEqual(rays.call_count, 11)
        self.assertTrue(all(ray.args[0] is mech and ray.args[4].amount == 2 and ray.args[5] == 100
                            for ray in rays.call_args_list))
        directions = [ray.args[3] for ray in rays.call_args_list]
        self.assertEqual(len(set(directions)), 11)
        self.assertTrue(all(abs(math.hypot(*direction) - 1) < 1e-6 for direction in directions))
        self.assertEqual(pilot.body_script.component.entity_var(ULT_CHARGE).value(), pilot_charge)
        target = next(target for target in fight._targets() if target.owner is hanzo)
        hits = [hit["+0x78"] for hit in pilot.client.messages(combat.HIT)]
        if caster is not None:
            self.assertNotIn(caster.body, fight.zarya_barriers.projected)
        return fight.vitals_of(target), hits, mech, pilot, hanzo

    def add_defender(self, run, hanzo, hero, position):
        match = run.match
        defender = match.add_player(3, "Defender", hero, hanzo.team, False)
        in_world(match, defender)
        run.players.append(defender)
        defender.spawn = position, 180
        self.assertTrue(match.switch_hero(defender, heroes()[hero]))
        defender.mover.position[:] = position
        defender.mover.body = None
        for _ in range(25):
            run.step({})
        return defender

    def test_ring_math_matches_reference_vectors(self):
        seed = weapon_spread.seed_base(0xC000100F, 7, 3, 1)
        self.assertEqual(seed, 4294)
        rays = weapon_spread.ring_directions((0, 0, 1), 4, 0.7, 0,
                                             ((0, 1), (0.5, 4), (1, 6)), seed, 0)
        vectors = {
            0: (-0.0018974293489009142, -0.005561683792620897, 0.9999827146530151),
            1: (-0.003408055752515793, 0.017237834632396698, 0.9998456239700317),
            5: (0.0051661389879882336, 0.05936498939990997, 0.9982229471206665),
            10: (0.04760240018367767, 0.032761186361312866, 0.9983289837837219),
        }
        for index, expected in vectors.items():
            self.assertLess(math.dist(rays[index], expected), 2e-7)

    def test_normal_owned_primary_emits_eleven_body_hits_and_credits_the_mech_meter(self):
        vitals, hits, mech, _, hanzo = self.fire(2)
        self.assertEqual(vitals.total, 178)
        self.assertEqual(hits, [{"+0x0": {"+0x0": hanzo.body}, "+0x4": 2, "+0x8": 0}] * 11)
        self.assertEqual(mech.body_script.component.entity_var(ULT_CHARGE).value(), 22)

    def test_upper_contacts_use_scoped_critical_multiplier(self):
        vitals, hits, mech, _, _ = self.fire(2, height=1.3)
        self.assertEqual(len(hits), 11)
        self.assertTrue(any(hit["+0x8"] & combat.CRITICAL for hit in hits))
        for hit in hits:
            self.assertEqual(hit["+0x4"], 4 if hit["+0x8"] & combat.CRITICAL else 2)
        dealt = sum(hit["+0x4"] for hit in hits)
        self.assertEqual(vitals.total, 200 - dealt)
        self.assertEqual(mech.body_script.component.entity_var(ULT_CHARGE).value(), dealt)

    def test_held_input_repeats_authored_volley_and_release_stops_firing(self):
        run, pilot, mech, hanzo, _, _ = mech_tests.HanzoOwnedMechTests().arena(distance=2)
        fight = run.match.combat
        x, y, z = mech.position
        hx, hy, hz = hanzo.mover.position
        angles = aim((x, y + combat.eye_height(mech.body), z), (hx, hy + 0.85, hz))
        for _ in range(62):
            run.step({pilot: (*angles, 1)})
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 77)
        self.assertEqual(fight.vitals[hanzo.body].total, 46)
        for _ in range(30):
            run.step({pilot: (*angles, 0)})
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 77)
        self.assertEqual(fight.vitals[hanzo.body].total, 46)

    def test_far_rounds_fall_off_and_wall_team_range_block_damage(self):
        vitals, hits, mech, _, _ = self.fire(25)
        self.assertAlmostEqual(vitals.total, 197.6, delta=1e-5)
        self.assertEqual([hit["+0x4"] for hit in hits], [1, 1, 1])
        self.assertAlmostEqual(mech.body_script.component.entity_var(ULT_CHARGE).value(), 2.4, delta=1e-5)
        for distance, options in ((6, {"wall": True}), (2, {"ally": True}), (101, {})):
            with self.subTest(distance=distance, **options):
                vitals, hits, mech, _, _ = self.fire(distance, **options)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(hits)
                self.assertEqual(to_float(mech.body_script.component.entity_var(ULT_CHARGE).value()), 0)

    def test_lethal_pellet_credits_pilot_with_owned_entity_source(self):
        vitals, hits, mech, pilot, hanzo = self.fire(2, health=1)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, mech.entity)
        self.assertEqual(hits, [{"+0x0": {"+0x0": hanzo.body}, "+0x4": 1, "+0x8": combat.KILLING}])
        self.assertEqual(hanzo.client.messages(combat.DEATH_NOTICE),
                         [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": mech.entity}}])
        kill, = pilot.client.messages(combat.KILL_NOTICE)
        self.assertEqual((kill["+0x80"], kill["+0x84"]), ({"+0x0": mech.entity}, {"+0x0": hanzo.body}))
        self.assertEqual(mech.body_script.component.entity_var(ULT_CHARGE).value(), 1)

    def test_projected_barrier_break_has_no_spill_before_later_pellets(self):
        vitals, hits, mech, _, hanzo = self.fire(2, cover=9)
        self.assertEqual(vitals.total, 188)
        self.assertEqual(hits, [{"+0x0": {"+0x0": hanzo.body}, "+0x4": 2, "+0x8": 0}] * 6)
        self.assertEqual(mech.body_script.component.entity_var(ULT_CHARGE).value(), 12)

    def test_allied_deflect_turns_fusion_away_from_hanzo(self):
        run, pilot, mech, hanzo, _, _ = mech_tests.HanzoOwnedMechTests().arena(distance=8)
        fight = run.match.combat
        genji = self.add_defender(run, hanzo, arrow_deflection.GENJI, (0, hanzo.mover.position[1], 3))
        run.step({genji: (-32768, 0, 16)})
        x, y, z = mech.position
        yaw, pitch = aim((x, y + combat.eye_height(mech.body), z), (0, hanzo.mover.position[1] + 1.1, 8))
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rays:
            run.step({pilot: (yaw, pitch, 1), genji: (-32768, 0, 0)})
            for _ in range(20):
                run.step({genji: (-32768, 0, 0)})
        self.assertEqual([ray.args[0] for ray in rays.call_args_list], [mech, genji] * 11)
        target = next(target for target in fight._targets() if target.owner is hanzo)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertLess(fight.vitals[mech.entity].total, 600)
        self.assertFalse(pilot.client.messages(combat.HIT))
        self.assertTrue(genji.client.messages(combat.HIT))
        self.assertEqual(to_float(mech.body_script.component.entity_var(ULT_CHARGE).value()), 0)
