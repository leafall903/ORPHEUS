"""Sigma attacks against an authored 104319 mech with external Matrix inputs."""

import gzip
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_sigma_combat as fixtures
import test_sigma_flux_placement as placement
from test_game_combat import aim

from ow174.game import combat, dva_matrix, mover, pools, sigma
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.world import OP_CREATE, OP_DESTROY


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_GRAPHS") and dva_matrix.TAGS_PATH,
                     "requires external 104319 mech graphs and projectile tags")
class SigmaDvaMatrixTests(unittest.TestCase):
    def setUp(self):
        data = graph._data()
        path = Path(os.environ["ORPHEUS_DVA_GRAPHS"])
        extract = json.loads(gzip.decompress(path.read_bytes()))
        body_key = f"0x{dva_matrix.MECH:016X}"
        hero_key = f"0x{dva_matrix.HERO:016X}"
        body = extract["entities"][body_key]
        bodies = {key: value for key, value in data["bodies"].items() if value["hero"] != hero_key}
        bodies[body_key] = {**body, "hero": hero_key}
        existing = {entry["index"] for entry in data["graphs"]}
        extra = [entry for entry in extract["graphs"] if entry["index"] not in existing]
        self.addCleanup(self.clear_graphs)
        self.enterContext(patch.object(graph, "_data", return_value={
            **data, "bodies": bodies, "graphs": data["graphs"] + extra,
        }))
        mover_table = json.loads(path.with_name("game_movers_174.json").read_text(encoding="utf-8"))
        self.enterContext(patch.object(mover, "_movers", return_value={
            **mover._movers(), dva_matrix.MECH: mover.MoverData(**mover_table[body_key]),
        }))
        self.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()
        pools.hero_pools.cache_clear()

    def arena(self, *, distance=8):
        run, shooter, mech, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=distance)
        position = list(mech.mover.position)
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[dva_matrix.HERO], body=dva_matrix.MECH)))
        mech.mover.position = position
        for _ in range(75):
            run.step({})
        self.assertEqual(mech.mover.data.stand_radius, 0.7)
        fight = run.match.combat
        self.assertAlmostEqual(fight._eye(mech)[1] - mech.mover.position[1], 1.74)
        health = fight.vitals_of(next(target for target in fight._targets() if target.owner is mech))
        self.assertEqual(health.now[combat.HEALTH_POOL], 300)
        self.assertEqual(health.now[combat.ARMOUR_POOL], 300)
        matrix = next(instance for instance in mech.body_script.component.instances.values()
                      if instance.graph.index == dva_matrix.MATRIX)
        self.assertEqual(matrix.vars[87].value(), 100)
        shooter.body_script.ult_seconds = 0
        shooter.body_script.set_ult_charge(0)
        return run, shooter, mech, health, matrix

    def attack(self, run, shooter, mech, button, *, face=-32768, hold=60):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(shooter), fight._eye(mech))
        for frame in range(hold):
            run.step({shooter: (yaw, pitch, button if frame == 0 else 0), mech: (face, 0, SECONDARY)})

    def test_matrix_consumes_both_hyperspheres_without_damage_or_charge(self):
        run, shooter, mech, health, matrix = self.arena()
        for _ in range(5):
            run.step({mech: (-32768, 0, SECONDARY)})
        self.assertTrue(matrix.states[3].active)
        fight = run.match.combat
        contacts = []
        matrix_hit = fight._matrix_hit

        def record_contact(rocket, reach):
            distance = matrix_hit(rocket, reach)
            if distance is not None:
                contacts.append(rocket.definition)
            return distance

        with (patch.object(fight, "_matrix_hit", side_effect=record_contact),
              patch.object(fight, "_explode", wraps=fight._explode) as explode):
            self.attack(run, shooter, mech, FIRE)
        self.assertEqual(contacts, [0x0400000000002D35, 0x04000000000032B4])
        self.assertFalse(explode.called)
        self.assertTrue(matrix.states[3].active)
        self.assertLess(matrix.vars[87].value(), 100)
        self.assertEqual(health.total, 600)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)
        run.step({})
        self.assertFalse(matrix.states[3].active)
        for _ in range(50):
            run.step({})
        fixtures.SigmaCombatTests().pair(run, shooter, mech)
        self.assertEqual(health.total, 510)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 90)

    def test_hyperspheres_hit_from_behind_an_active_matrix(self):
        run, shooter, mech, health, matrix = self.arena()
        for _ in range(5):
            run.step({mech: (0, 0, SECONDARY)})
        self.assertTrue(matrix.states[3].active)
        self.attack(run, shooter, mech, FIRE, face=0)
        self.assertTrue(matrix.states[3].active)
        self.assertEqual(health.total, 510)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 90)

    def test_allied_matrix_lets_both_hyperspheres_reach_their_airburst(self):
        run, shooter, mech, health, matrix = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mech.team = shooter.team
        for _ in range(5):
            run.step({mech: (-32768, 0, SECONDARY)})
        self.assertTrue(matrix.states[3].active)
        fight = run.match.combat
        with patch.object(fight, "_explode", wraps=fight._explode) as explode:
            self.attack(run, shooter, mech, FIRE)
        self.assertEqual([call.args[0].definition for call in explode.call_args_list],
                         [0x0400000000002D35, 0x04000000000032B4])
        self.assertTrue(all(call.args[0].left <= 0 for call in explode.call_args_list))
        self.assertEqual(health.total, 600)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.rockets)

    def test_matrix_retires_confirmed_hypersphere_copies_without_explosion_release(self):
        run, shooter, mech, health, matrix = self.arena(distance=18)
        for _ in range(5):
            run.step({mech: (-32768, 0, SECONDARY)})
        self.assertTrue(matrix.states[3].active)
        fight = run.match.combat
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorb:
            self.attack(run, shooter, mech, FIRE)
        shots = [call.args[0] for call in absorb.call_args_list]
        self.assertEqual([shot.definition for shot in shots], [0x0400000000002D35, 0x04000000000032B4])
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.shots)
        self.assertEqual(health.total, 600)
        for shot in shots:
            self.assertIsNone(shot.release_at)
            self.assertTrue(shot.created)
            self.assertTrue(all(arrival.received for arrival in shot.created.values()))
            for entity in shot.created:
                self.assert_retired_without_impact(shooter.client, entity)
            delivery, = shot.observers
            self.assertIs(delivery.viewer, mech)
            entity, = delivery.record.ids
            self.assert_retired_without_impact(mech.client, entity)

    def assert_retired_without_impact(self, client, entity):
        updates = [entry for entry in client.entities if entry.entity == entity]
        self.assertEqual([entry.op for entry in updates], [OP_CREATE, OP_DESTROY])
        self.assertIsNone(updates[-1].ch2)

    def test_accretion_passes_active_matrix_and_applies_control(self):
        run, shooter, mech, health, matrix = self.arena()
        for _ in range(5):
            run.step({mech: (-32768, 0, SECONDARY)})
        self.assertTrue(matrix.states[3].active)
        fight = run.match.combat
        x, y, z = mech.mover.position
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
        for frame in range(65):
            run.step({shooter: (yaw, pitch, 0x10 if frame == 0 else 0), mech: (-32768, 0, SECONDARY)})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual(health.total, 540)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 60)
        self.assertFalse(fight.rockets)

    def test_fusion_cannons_deal_eleven_hits_or_feed_active_grasp(self):
        for grasping in (False, True):
            with self.subTest(grasping=grasping):
                run, sigma_player, mech, _, _ = self.arena(distance=2)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is sigma_player)
                health = fight.vitals_of(target)
                x, y, z = sigma_player.mover.position
                yaw, pitch = aim(fight._eye(mech), (x, y + 1, z))
                if grasping:
                    run.step({sigma_player: (0, 0, 0x08)})
                    grasp = fixtures.SigmaCombatTests().ability(sigma_player, sigma.GRASP).instance
                    self.assertTrue(grasp.states[1].active)
                run.step({mech: (yaw, pitch, FIRE)})
                for _ in range(135):
                    run.step({})
                self.assertEqual(health.now[combat.HEALTH_POOL], 300)
                self.assertEqual(health.now[combat.SHIELDS_POOL], 100 if grasping else 78)
                self.assertEqual(len(mech.client.messages(combat.HIT)), 0 if grasping else 11)
                if grasping:
                    self.assertEqual(grasp.vars[21312].value(), 22)
                    self.assertAlmostEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 13.2, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 mesh")
    def test_fusion_cannon_pellets_only_reach_grasp_after_nearer_barrier_breaks(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, sigma_player, mech, _, _ = self.arena(distance=3.5)
                for frame in range(40):
                    run.step({sigma_player: (0, 0, SECONDARY if frame < 10 else 0)})
                fight = run.match.combat
                barrier, = fight.barriers.values()
                self.assertTrue(barrier.active)
                if breaking:
                    barrier.vitals.damage(690)
                run.step({sigma_player: (0, 0, 0x08)})
                grasp = fixtures.SigmaCombatTests().ability(sigma_player, sigma.GRASP).instance
                self.assertTrue(grasp.states[1].active)
                x, y, z = sigma_player.mover.position
                yaw, pitch = aim(fight._eye(mech), (x, y + 1, z))
                run.step({mech: (yaw, pitch, FIRE)})
                self.assertEqual(barrier.vitals.total, 0 if breaking else 678)
                self.assertEqual(barrier.active, not breaking)
                self.assertEqual(grasp.vars[21312].value(), 12 if breaking else 0)
                target = next(target for target in fight._targets() if target.owner is sigma_player)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertFalse(mech.client.messages(combat.HIT))

    def test_quick_melee_passes_an_active_matrix(self):
        run, shooter, mech, health, matrix = self.arena(distance=1.4)
        for _ in range(5):
            run.step({mech: (-32768, 0, SECONDARY)})
        self.assertTrue(matrix.states[3].active)
        self.attack(run, shooter, mech, 0x800, hold=35)
        self.assertTrue(matrix.states[3].active)
        self.assertEqual(health.total, 575)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 25)

    def test_boosters_contact_bypasses_grasp_once_and_applies_knockback(self):
        run, defender, mech, _, _ = self.arena(distance=6)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        health = fight.vitals_of(target)
        mech.body_script.ult_seconds = 0
        mech.body_script.set_ult_charge(0)
        run.step({defender: (0, 0, 0x08), mech: (-32768, 0, 0x08)})
        self.assertEqual(health.total, 400)
        grasp = fixtures.SigmaCombatTests().ability(defender, sigma.GRASP).instance
        for _ in range(25):
            run.step({mech: (-32768, 0, 0)})
            if health.total < 400:
                break
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(health.total, 390)
        self.assertLess(defender.mover.velocity[2], -16)
        self.assertGreater(defender.mover.velocity[1], 3)
        self.assertEqual(mech.body_script.component.entity_var(342).value(), 10)
        for _ in range(30):
            run.step({mech: (-32768, 0, 0)})
        self.assertEqual(health.total, 390)
        self.assertEqual(len(mech.client.messages(combat.HIT)), 1)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)

    def test_lethal_boosters_contact_ends_grasp_and_credits_only_remaining_health(self):
        run, defender, mech, _, _ = self.arena(distance=6)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        health = fight.vitals_of(target)
        health.now[combat.HEALTH_POOL] = 5
        health.now[combat.SHIELDS_POOL] = 0
        mech.body_script.ult_seconds = 0
        mech.body_script.set_ult_charge(0)
        run.step({defender: (0, 0, 0x08), mech: (-32768, 0, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(defender, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        for _ in range(30):
            run.step({mech: (-32768, 0, 0)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, mech.body)
        self.assertFalse(grasp.states[1].active)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)
        self.assertEqual(mech.body_script.component.entity_var(342).value(), 5)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 mesh")
    def test_boosters_cross_experimental_barrier_but_static_cover_stops_contact(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, defender, mech, _, _ = self.arena(distance=6)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is defender)
                health = fight.vitals_of(target)
                for frame in range(40):
                    run.step({defender: (0, 0, SECONDARY if frame < 10 else 0)})
                barrier, = fight.barriers.values()
                if wall:
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=4)
                run.step({mech: (-32768, 0, 0x08)})
                for _ in range(30):
                    run.step({mech: (-32768, 0, 0)})
                self.assertEqual(health.total, 400 if wall else 390)
                self.assertEqual(barrier.vitals.total, 700)

    def test_boosters_contact_excludes_allies_and_lateral_misses(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, defender, mech, _, _ = self.arena(distance=6)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is defender)
                health = fight.vitals_of(target)
                if allied:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mech.team = defender.team
                else:
                    mech.mover.position[0] += 3
                    mech.mover.body = None
                start = mech.mover.position[2]
                run.step({mech: (-32768, 0, 0x08)})
                for _ in range(40):
                    run.step({mech: (-32768, 0, 0)})
                self.assertLess(mech.mover.position[2], start - 6)
                self.assertEqual(health.total, 400)
                self.assertFalse(mech.client.messages(combat.HIT))

    def test_accretion_interrupts_boosters_before_contact_and_denies_recast(self):
        run, defender, mech, mech_health, _ = self.arena(distance=9)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        health = fight.vitals_of(target)
        booster = fixtures.SigmaCombatTests().ability(mech, dva_matrix.BOOSTERS).instance
        x, y, z = mech.mover.position
        yaw, pitch = aim(fight._eye(defender), (x, y + 1, z))
        run.step({defender: (yaw, pitch, 0x10)})
        for _ in range(35):
            run.step({defender: (yaw, pitch, 0)})
        run.step({defender: (yaw, pitch, 0), mech: (-32768, 0, 0x08)})
        self.assertTrue(booster.states[6].active)
        for _ in range(35):
            run.step({defender: (yaw, pitch, 0), mech: (-32768, 0, 0)})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual(mech_health.total, 540)
        self.assertFalse(booster.states[6].active)
        self.assertEqual(health.total, 400)
        for _ in range(150):
            if not fight.is_stunned(mech):
                break
            run.step({})
        self.assertFalse(fight.is_stunned(mech))
        self.assertGreater(booster.vars[61].value(), 0)
        run.step({mech: (-32768, 0, 0x08)})
        self.assertFalse(booster.states[6].active)
        self.assertEqual(health.total, 400)

    def test_flux_stops_live_boosters_and_denies_new_boosters_until_landing(self):
        for already_boosting in (False, True):
            with self.subTest(already_boosting=already_boosting):
                run, shooter, mech, health, _ = self.arena(distance=7)
                fight = run.match.combat
                shooter_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
                script = shooter.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                booster = fixtures.SigmaCombatTests().ability(mech, dva_matrix.BOOSTERS).instance
                track = booster.state(6)
                floor = tuple(mech.mover.position)
                placement.SigmaFluxPlacementTests().select(run, shooter, floor)
                caught = None
                for frame in range(240):
                    pressed = frame in ((55, 80, 160) if already_boosting else (80, 160))
                    run.step({mech: (-32768, 0, 0x08 if pressed else 0)})
                    if frame == 55 and already_boosting:
                        self.assertTrue(track.active)
                    if health.total == 555 and caught is None:
                        caught = mech.mover.position[2]
                    if caught is not None:
                        self.assertFalse(track.active)
                        self.assertEqual(mech.mover.position[2], caught)
                self.assertIsNotNone(caught)
                if already_boosting:
                    self.assertLess(caught, floor[2])
                else:
                    self.assertEqual(caught, floor[2])
                self.assertEqual(track.counter, int(already_boosting))
                self.assertEqual(health.total, 260)
                self.assertEqual(shooter_health.total, 400)
                self.assertFalse(mech.mover.airborne)
                run.step({mech: (-32768, 0, 0x08)})
                self.assertEqual(track.active, not already_boosting)

    def test_flux_lifts_and_slams_mech_with_matrix_active_at_both_hits(self):
        run, shooter, mech, health, matrix = self.arena()
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        floor = tuple(mech.mover.position)
        placement.SigmaFluxPlacementTests().select(run, shooter, floor)
        fight = run.match.combat
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(90):
                run.step({mech: (-32768, 0, SECONDARY)})
                if health.total < 600:
                    break
            self.assertTrue(matrix.states[3].active)
            self.assertEqual(health.now[combat.ARMOUR_POOL], 255)
            self.assertEqual(health.now[combat.HEALTH_POOL], 300)
            effect, = (instance for instance in mech.body_script.component.instances.values()
                       if instance.graph.index == sigma.FLUX_EFFECT)
            self.assertEqual(effect.source_entity(), shooter.body)
            for _ in range(8):
                run.step({mech: (-32768, 0, SECONDARY)})
            self.assertTrue(mech.mover.airborne)
            for _ in range(115):
                run.step({})
            self.assertFalse(matrix.states[3].active)
            self.assertGreater(mech.mover.position[1], floor[1] + 10)
            self.assertEqual(health.total, 555)

            for _ in range(90):
                run.step({mech: (-32768, 0, SECONDARY)})
                if health.total < 555:
                    break
            self.assertTrue(matrix.states[3].active)
            self.assertFalse(mech.mover.airborne)
            self.assertAlmostEqual(mech.mover.position[1], floor[1], delta=0.01)
            self.assertEqual(health.now[combat.ARMOUR_POOL], 0)
            self.assertEqual(health.now[combat.HEALTH_POOL], 260)
            self.assertEqual([call.args[2] for call in damage.call_args_list if call.args[1].owner is mech],
                             [50, 300])
        for _ in range(90):
            run.step({})
        self.assertNotIn(effect.id, mech.body_script.component.instances)
        self.assertEqual(health.total, 260)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_grasp_consumes_the_full_micro_missile_burst(self):
        for grasping in (False, True):
            with self.subTest(grasping=grasping):
                run, sigma_player, mech, _, _ = self.arena(distance=6)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is sigma_player)
                health = fight.vitals_of(target)
                x, y, z = sigma_player.mover.position
                yaw, pitch = aim(fight._eye(mech), (x, y + 1, z))
                if grasping:
                    run.step({sigma_player: (0, 0, 0x08)})
                    grasp = fixtures.SigmaCombatTests().ability(sigma_player, sigma.GRASP).instance
                    self.assertTrue(grasp.states[1].active)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for frame in range(150):
                        # Retry after the burst, while the missile cooldown is still running.
                        run.step({mech: (yaw, pitch, 0x10 if frame in (0, 125) else 0)})
                self.assertEqual([call.args[2].instance.graph.index for call in shoot.call_args_list],
                                 [0x1128] * 18)
                self.assertFalse(fight.rockets)
                if grasping:
                    self.assertEqual(grasp.vars[21312].value(), 126)
                    self.assertEqual(health.now[combat.HEALTH_POOL], 300)
                    self.assertEqual(health.now[combat.SHIELDS_POOL], 100)
                    self.assertAlmostEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 75.6, places=4)
                    self.assertFalse(mech.client.messages(combat.HIT))
                else:
                    self.assertEqual(health.total, 274)


if __name__ == "__main__":
    unittest.main()
