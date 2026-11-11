"""Executed Barrier actor/mesh combat over an explicit verified external model."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_sigma_combat as fixtures
from test_game_combat import KANEZAKA_DM, Run, aim
from test_sigma_flux_placement import SigmaFluxPlacementTests

from ow174.game import combat, dva_matrix, props, sigma, symmetra_beam
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr

MODEL = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")


@unittest.skipUnless(MODEL, "requires the manifest-verified external Sigma C015 model")
class SigmaBarrierTests(unittest.TestCase):
    def arena(self, *, names=("Sigma", "Enemy"), hold=20, deploy=True):
        run = Run(KANEZAKA_DM, names)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player, *others = run.players
        run.match.switch_hero(player, heroes()[sigma.HERO])
        x, y, z = player.mover.position
        for other in others:
            other.mover.position = [x, y, z + 10]
        if deploy:
            for frame in range(50):
                run.step({player: (0, 0, SECONDARY if frame < hold else 0)})
        actor, = run.match.combat.barriers.values()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        vitals = run.match.combat.vitals_of(target)
        return run, player, others, actor, vitals

    def facing(self, run, enemy, player):
        x, y, z = player.mover.position
        return aim(run.match.combat._eye(enemy), (x, y + 0.8, z))

    def test_actual_dynamic_physics_mesh_is_used_and_static_filter_remains_default(self):
        run, player, _others, actor, _vitals = self.arena()
        raw = Path(MODEL).read_bytes()
        self.assertEqual(props.model_shapes(raw, 0xFFFF), [])
        shape, = props.model_shapes(raw, 0xFFFF, include_dynamic=True)
        self.assertEqual((shape[0], len(shape[2]), len(shape[3])), ("mesh", 62, 120))
        self.assertEqual(len(actor.geometry.masks), 120)
        instance = actor.instance
        self.assertEqual(instance.component.entity_var(512).value(), expr.Entity(actor.entity))
        self.assertEqual(instance.vars[586].value(), 700)
        self.assertTrue(actor.child_request_seen)
        self.assertTrue(actor.active)
        self.assertEqual(actor.phase, 3)
        self.assertIsNotNone(actor.intersection(run.match.combat._eye(player), (0, 0, 1), 20))

    def test_continuous_deployment_survives_command_gap_then_releases_and_recalls(self):
        for gap, stays_held in ((45, True), (90, True), (90, False)):
            with self.subTest(gap=gap, stays_held=stays_held):
                run, player, _, actor, _ = self.arena(deploy=False)
                for _ in range(12):
                    run.step({player: (0, 0, SECONDARY)})
                self.assertEqual(actor.phase, 2)
                before = actor.position
                for _ in range(gap):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(actor.phase, 2)
                self.assertAlmostEqual(actor.position[2] - before[2], gap * combat.TICK * 16.5)

                player.take_commands([Command(run.frame, buttons=SECONDARY if stays_held else 0, extra=0)])
                if stays_held:
                    run.step({player: (0, 0, SECONDARY)})
                    self.assertTrue(actor.active)
                    self.assertEqual(actor.phase, 2)
                    self.assertIs(run.match.combat.barriers[actor.entity], actor)

                run.step({})
                run.step({})
                self.assertEqual(actor.phase, 3)
                parked = actor.position
                for _ in range(18):
                    run.step({})
                self.assertEqual(actor.position, parked)
                run.step({player: (0, 0, SECONDARY)})
                run.step({})
                self.assertFalse(actor.active)
                self.assertEqual(actor.phase, 1)

    def test_toggle_deployment_keeps_moving_on_release_then_parks_on_next_press(self):
        run, player, _, actor, _ = self.arena(deploy=False)
        player.body_script.component.set(23619, True)
        for _ in range(12):
            run.step({player: (0, 0, SECONDARY)})
        start = actor.position
        for _ in range(15):
            run.step({})
        self.assertEqual(actor.phase, 2)
        self.assertAlmostEqual(actor.position[2] - start[2], 15 * combat.TICK * 16.5)

        run.step({player: (0, 0, SECONDARY)})
        parked = actor.position
        for _ in range(22):
            run.step({})
        self.assertTrue(actor.active)
        self.assertEqual(actor.phase, 3)
        self.assertEqual(actor.position, parked)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(actor.active)
        self.assertEqual(actor.phase, 1)
        self.assertGreater(actor.instance.states[111].remaining(), 1.9)

    def test_rifle_hits_reduce_barrier_health_without_harming_owner(self):
        run, player, others, actor, vitals = self.arena()
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(actor.vitals.total, 640)
        run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(actor.instance.vars[586].value(), 640)

    def test_long_range_deployment_stops_at_distant_wall_and_intercepts(self):
        run, player, (enemy,), actor, vitals = self.arena(deploy=False)
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 119.8]
        enemy.mover.body = None
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=120)
        for _ in range(490):
            run.step({player: (0, 0, SECONDARY)})
        self.assertTrue(actor.active)
        self.assertEqual(actor.phase, 2)
        self.assertAlmostEqual(actor.position[2] - z, 119.1, delta=0.002)
        parked = actor.position
        run.step({})
        run.step({})
        self.assertEqual(actor.phase, 3)
        for _ in range(18):
            run.step({})

        yaw, pitch = aim(run.match.combat._eye(enemy), run.match.combat._eye(player))
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(actor.position, parked)
        self.assertLess(actor.vitals.total, 700)
        self.assertEqual(vitals.total, 400)
        durability = actor.vitals.total
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(actor.active)
        for _ in range(15):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(actor.vitals.total, durability)
        self.assertLess(vitals.total, 400)

    def test_rifle_hits_on_barrier_do_not_restart_owner_shield_recovery(self):
        run, player, enemy, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=10)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        shield = combat.SHIELDS_POOL
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(vitals.now[shield], 80)
        for frame in range(187):
            buttons = SECONDARY if frame < 8 else 0
            rifle = FIRE if frame >= 160 else 0
            run.step({player: (0, 0, buttons), enemy: (yaw, pitch, rifle)})
            self.assertEqual(vitals.now[shield], 80)
        barrier, = fight.barriers.values()
        self.assertTrue(barrier.active)
        self.assertLess(barrier.vitals.total, 700)
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(vitals.now[shield], 80.24)
        durability_at_recovery = barrier.vitals.total
        for _ in range(45):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertLess(barrier.vitals.total, durability_at_recovery)
        self.assertEqual(vitals.now[shield], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertTrue(barrier.active)

    def test_sigma_mirror_defenses_exclude_barrier_and_temporary_shields_from_flux_charge(self):
        run, defender, (attacker,), barrier, vitals = self.arena(hold=8)
        position = list(attacker.mover.position)
        run.match.switch_hero(attacker, heroes()[sigma.HERO])
        attacker.mover.position = position
        script = attacker.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        fixture = fixtures.SigmaCombatTests()
        run.step({})
        fixture.pair(run, attacker, defender)
        self.assertEqual(barrier.vitals.total, 590)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        run.step({defender: (0, 0, SECONDARY)})
        for _ in range(60):
            run.step({})
        self.assertFalse(barrier.active)
        run.step({defender: (0, 0, 0x08)})
        fixture.pair(run, attacker, defender)
        grasp = fixture.ability(defender, sigma.GRASP).instance
        self.assertEqual(grasp.vars[21312].value(), 110)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(75):
            run.step({})
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 66)
        fixture.pair(run, attacker, defender)
        self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 0)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 56)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(script.component.entity_var(342).value(), 44)

    def test_recall_between_hyperspheres_routes_second_sphere_to_active_grasp(self):
        self.assert_second_hypersphere_reaches_grasp(breaking=False)

    def test_first_hypersphere_breaks_barrier_second_reaches_grasp(self):
        self.assert_second_hypersphere_reaches_grasp(breaking=True)

    def test_reaper_shell_breaks_barrier_and_remaining_pellets_reach_grasp(self):
        run, defender, (attacker,), barrier, vitals = self.arena(hold=10)
        run.match.switch_hero(attacker, heroes()[0x02E0000000000002])
        x, y, z = defender.mover.position
        attacker.mover.position = [x, y, z + 3.5]
        barrier.vitals.damage(640)
        self.assertEqual(barrier.vitals.total, 60)
        yaw, pitch = aim(run.match.combat._eye(attacker), (x, y + 1, z))
        run.step({defender: (0, 0, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(defender, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        run.step({attacker: (yaw, pitch, FIRE)})
        for _ in range(35):
            run.step({})
        self.assertEqual(barrier.vitals.total, 0)
        self.assertFalse(barrier.active)
        self.assertEqual(grasp.vars[21312].value(), 60)
        self.assertEqual(vitals.total, 400)
        for _ in range(100):
            run.step({})
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 36)
        self.assertFalse(attacker.client.messages(combat.HIT))

    def assert_second_hypersphere_reaches_grasp(self, *, breaking):
        run, defender, (attacker,), barrier, vitals = self.arena(hold=20)
        if breaking:
            barrier.vitals.damage(660)
        initial_durability = barrier.vitals.total
        position = list(attacker.mover.position)
        run.match.switch_hero(attacker, heroes()[sigma.HERO])
        attacker.mover.position = position
        script = attacker.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(attacker), fight._eye(defender))
        run.step({defender: (0, 0, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(defender, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        run.step({attacker: (yaw, pitch, FIRE)})
        for _ in range(40):
            if barrier.vitals.total < initial_durability:
                break
            run.step({attacker: (yaw, pitch, 0)})
        remaining = 0 if breaking else 645
        self.assertEqual(barrier.vitals.total, remaining)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(vitals.total, 400)
        if not breaking:
            run.step({attacker: (yaw, pitch, 0), defender: (0, 0, SECONDARY)})
            run.step({attacker: (yaw, pitch, 0)})
        self.assertFalse(barrier.active)
        self.assertTrue(grasp.states[1].active)
        for _ in range(35):
            run.step({attacker: (yaw, pitch, 0)})
        self.assertEqual(grasp.vars[21312].value(), 55)
        self.assertEqual(barrier.vitals.total, remaining)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(90):
            run.step({})
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 33)

    def test_experimental_barrier_does_not_intercept_flux_on_its_owner(self):
        run, defender, (attacker,), barrier, vitals = self.arena(hold=20)
        position = list(attacker.mover.position)
        run.match.switch_hero(attacker, heroes()[sigma.HERO])
        attacker.mover.position = position
        fight = run.match.combat
        target = next(entry for entry in fight._targets() if entry.owner is defender)
        self.assertFalse(fight._blast_passes_barriers(attacker, fight._eye(attacker), target, None))
        script = attacker.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        floor = tuple(defender.mover.position)
        SigmaFluxPlacementTests().select(run, attacker, floor)
        for _ in range(150):
            run.step({})
            if defender.mover.airborne:
                break
        self.assertTrue(defender.mover.airborne)
        self.assertEqual(vitals.total, 350)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.vitals.total, 700)
        for _ in range(340):
            run.step({})
            if vitals.total < 350:
                break
        self.assertFalse(defender.mover.airborne)
        self.assertAlmostEqual(defender.mover.position[1], floor[1], delta=0.01)
        self.assertEqual(vitals.total, 150)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.vitals.total, 700)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_airborne_barrier_owner_can_recall_and_expose_grounded_ally(self):
        self.assert_flux_ends_ally_barrier_cover(lethal=False)

    def test_lethal_flux_slam_removes_ally_barrier_cover_in_damage_frame(self):
        self.assert_flux_ends_ally_barrier_cover(lethal=True)

    def assert_flux_ends_ally_barrier_cover(self, *, lethal):
        run = Run(KANEZAKA_DM, ("Owner", "Caster", "Rifle", "Ally"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        owner, attacker, rifle, ally = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = ally.team = 0
        attacker.team = rifle.team = 1
        run.match.switch_hero(owner, heroes()[sigma.HERO])
        run.match.switch_hero(attacker, heroes()[sigma.HERO])
        x, y, z = owner.mover.position
        attacker.mover.position = [x + 5, y, z + 10]
        rifle.mover.position = [x, y, z + 10]
        ally.mover.position = [x, y, z - 10]
        fight = run.match.combat
        for frame in range(50):
            run.step({owner: (0, 0, SECONDARY if frame < 20 else 0)})
        barrier = next(actor for actor in fight.barriers.values() if actor.owner is owner)
        owner_target = next(entry for entry in fight._targets() if entry.owner is owner)
        owner_health = fight.vitals_of(owner_target)
        if lethal:
            owner_health.now[combat.HEALTH_POOL] = 245
            owner_health.now[combat.SHIELDS_POOL] = 0
        ally_target = next(entry for entry in fight._targets() if entry.owner is ally)
        ally_health = fight.vitals_of(ally_target)
        self.assertFalse(fight._blast_passes_barriers(rifle, fight._eye(rifle), ally_target, None))
        script = attacker.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        SigmaFluxPlacementTests().select(run, attacker, tuple(owner.mover.position))
        for _ in range(150):
            run.step({})
            if owner.mover.position[1] > y + 3:
                break
        self.assertGreater(owner.mover.position[1], y + 3)
        self.assertEqual(owner_health.total, 195 if lethal else 350)
        self.assertFalse(ally.mover.airborne)
        yaw, pitch = aim(fight._eye(rifle), fight._eye(ally))
        for _ in range(20):
            run.step({rifle: (yaw, pitch, FIRE)})
            if barrier.vitals.total < 700:
                break
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(ally_health.total, 200)
        if lethal:
            for _ in range(300):
                self.assertTrue(barrier.active)
                run.step({})
                if owner_health.dead:
                    break
            self.assertTrue(owner_health.dead)
            self.assertEqual(owner_health.killer, attacker.body)
            self.assertNotIn(barrier.entity, fight.barriers)
            self.assertEqual(owner.body_script.component.entity_var(512).value(), expr.Entity(0))
        else:
            run.step({owner: (0, 0, SECONDARY)})
            run.step({})
            self.assertTrue(owner.mover.airborne)
            self.assertGreater(barrier.instance.states[111].remaining(), 1.9)
        self.assertFalse(barrier.active)
        self.assertTrue(fight._blast_passes_barriers(rifle, fight._eye(rifle), ally_target, None))
        for _ in range(16):
            run.step({rifle: (yaw, pitch, FIRE)})
            if ally_health.total < 200:
                break
        self.assertLess(ally_health.total, 200)
        self.assertEqual(barrier.vitals.total, 680)
        if not lethal:
            self.assertTrue(owner.mover.airborne)

    def test_zarya_beam_hits_barrier_instead_of_owner(self):
        run, player, (enemy,), actor, vitals = self.arena()
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[0x02E0000000000068])
        enemy.mover.position = position
        run.step({player: (0, 0, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(30):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(vitals.total, 400)
        self.assertAlmostEqual(actor.vitals.total, 664)
        self.assertEqual(grasp.vars[21312].value(), 0)

    def test_symmetra_primary_charges_and_refills_ammo_on_experimental_barrier(self):
        if dva_matrix.TAGS_PATH is None:
            self.skipTest("requires the build 104319 target-tag catalog")
        run, player, (enemy,), actor, vitals = self.arena()
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        run.step({})
        weapon = next(i for i in enemy.body_script.component.instances.values()
                      if i.graph.index == symmetra_beam.WEAPON)
        ammo = weapon.find_var(0, 53)
        level = weapon.find_var(0, 499)
        target = weapon.find_var(0, 16627)
        for _ in range(34):
            run.step({enemy: (0, 0, FIRE)})
        self.assertLess(ammo.value(), 66)
        self.assertEqual(actor.vitals.total, 700)

        # Keep the last pre-break step above 1, the 1B55 recall threshold.
        sigma.damage_barrier(run.match.combat, actor, 2)
        yaw, pitch = self.facing(run, enemy, player)
        run.step({player: (0, 0, 8), enemy: (yaw, pitch, FIRE)})
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        for _ in range(4):
            run.step({enemy: (yaw, pitch, FIRE)})
        before_refill = ammo.value()
        for _ in range(25):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(vitals.total, 400)
        self.assertAlmostEqual(actor.vitals.total, 698 - 30 * 60 * combat.TICK)
        self.assertAlmostEqual(ammo.value() - before_refill, 25 * 7 * combat.TICK, delta=0.001)
        self.assertEqual(target.value(), expr.Entity(actor.entity))

        levels = {level.value()}
        for _ in range(320):
            run.step({enemy: (yaw, pitch, FIRE)})
            levels.add(level.value())
            if actor.vitals.total == 0:
                break
        self.assertEqual(levels, {60, 120, 180})
        self.assertEqual(actor.vitals.total, 0)
        self.assertFalse(actor.active)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(ammo.value(), 70)
        for _ in range(4):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(vitals.total, 400 - 4 * 180 * combat.TICK)
        self.assertEqual(target.value(), expr.Entity(player.body))
        self.assertLess(ammo.value(), 70)

    def test_symmetra_primary_triggers_authored_barrier_recall_at_fractional_health(self):
        run, player, (enemy,), actor, vitals = self.arena()
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        run.step({})
        sigma.damage_barrier(run.match.combat, actor, 698.88)
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(actor.vitals.total, 0.16)
        self.assertEqual(vitals.total, 400)
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertFalse(actor.active)
        self.assertAlmostEqual(actor.vitals.total, 0.16)
        self.assertAlmostEqual(vitals.total, 399.04)

    def test_symmetra_orb_stops_at_experimental_barrier_ahead_of_grasp(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, player, (enemy,), actor, vitals = self.arena()
                fight = run.match.combat
                if breaking:
                    sigma.damage_barrier(fight, actor, 610)
                orb = fixtures.SigmaCombatTests().launch_symmetra_orb(run, player, enemy, grasp_yaw=0)
                grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
                self.assertTrue(grasp.states[1].active)
                for _ in range(35):
                    run.step({})
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(actor.vitals.total, 0 if breaking else 580)
                self.assertEqual(actor.active, not breaking)
                self.assertEqual(grasp.vars[21312].value(), 0)
                self.assertEqual(vitals.total, 400)
                self.assertFalse(vitals.temporary_shields)

    def test_zarya_particle_grenade_detonates_on_barrier(self):
        run, player, (enemy,), actor, vitals = self.arena(hold=8)
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[0x02E0000000000068])
        enemy.mover.position = position
        run.step({})
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, SECONDARY)})
        for _ in range(55):
            run.step({})
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(actor.vitals.total, 653)
        self.assertEqual(vitals.total, 400)

    def test_orisa_fusion_driver_hits_experimental_barrier(self):
        run, player, (orisa,), actor, vitals = self.arena()
        position = list(orisa.mover.position)
        run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
        orisa.mover.position = position
        run.step({})
        yaw, pitch = self.facing(run, orisa, player)
        for frame in range(30):
            run.step({orisa: (yaw, pitch, FIRE if frame < 20 else 0)})
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(actor.vitals.total, 656)
        self.assertEqual(actor.instance.vars[586].value(), 656)
        self.assertEqual(vitals.total, 400)

    def test_orisa_halt_orb_crosses_experimental_barrier_but_pull_respects_cover(self):
        for crossed in (False, True):
            with self.subTest(crossed=crossed):
                run, player, (orisa,), actor, vitals = self.arena(hold=8)
                position = list(orisa.mover.position)
                run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
                orisa.mover.position = position
                run.step({})
                start = tuple(player.mover.position)
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(orisa), fight._eye(player))
                run.step({orisa: (yaw, pitch, SECONDARY)})
                threshold = actor.position[2] - 0.2 if crossed else start[2] + 4.9
                for _ in range(38):
                    run.step({orisa: (yaw, pitch, 0)})
                    if fight.rockets and fight.rockets[0].position[2] < threshold:
                        break
                rocket, = fight.rockets
                run.step({orisa: (yaw, pitch, SECONDARY)})
                self.assertIsNotNone(rocket.halt_at)
                self.assertEqual(rocket.position[2] < actor.position[2], crossed)
                self.assertLess(rocket.position[2] - start[2], 5)
                for _ in range(70):
                    run.step({})
                self.assertFalse(fight.rockets)
                if crossed:
                    self.assertGreater(player.mover.position[2] - start[2], 0.5)
                else:
                    self.assertEqual(tuple(player.mover.position), start)
                self.assertEqual(actor.vitals.total, 700)
                self.assertEqual(vitals.total, 400)

    def test_helix_near_owner_is_blocked_including_explosion(self):
        run, player, others, actor, vitals = self.arena(hold=8)
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        self.assertLess(actor.position[2] - player.mover.position[2], 3)
        run.step({enemy: (yaw, pitch, SECONDARY)})
        for _ in range(50):
            run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(actor.vitals.total, 580)
        self.assertEqual(vitals.total, 400)
        self.assertFalse(run.match.combat.rockets)

    def test_accretion_barrier_contact_blocks_owner_damage_control_and_impulse(self):
        self.assert_accretion_barrier_contact(breaking=False)

    def test_accretion_breaks_experimental_barrier_without_leaking_to_owner(self):
        self.assert_accretion_barrier_contact(breaking=True)

    def test_accretion_radius_cannot_reach_the_owner_through_a_close_barrier(self):
        self.assert_accretion_barrier_contact(breaking=False, hold=1)

    def assert_accretion_barrier_contact(self, *, breaking, hold=8):
        run, player, others, actor, vitals = self.arena(hold=hold)
        enemy, = others
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[sigma.HERO])
        enemy.mover.position = position
        enemy.body_script.ult_seconds = 0
        enemy.body_script.set_ult_charge(0)
        if breaking:
            actor.vitals.damage(660)
            self.assertEqual(actor.vitals.total, 40)
        before = tuple(player.mover.position)
        fight = run.match.combat
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, 0x10)})
        for _ in range(120):
            run.step({enemy: (yaw, pitch, 0)})
            self.assertFalse(fight.is_stunned(player))
            self.assertFalse(player.mover.external_impulse)
        self.assertEqual(actor.vitals.total, 0 if breaking else 630)
        self.assertEqual(enemy.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(tuple(player.mover.position), before)
        if breaking:
            self.assertFalse(actor.active)
            self.assertGreater(actor.instance.states[111].remaining(), 2)
            run.step({player: (0, 0, SECONDARY)})
            run.step({})
            self.assertFalse(actor.active)
            fixtures.SigmaCombatTests().pair(run, enemy, player)
            self.assertEqual(vitals.total, 290)
            self.assertEqual(enemy.body_script.component.entity_var(342).value(), 110)

    def test_recalling_barrier_after_accretion_launch_exposes_owner(self):
        run, player, (enemy,), barrier, vitals = self.arena(hold=8)
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[sigma.HERO])
        enemy.mover.position = position
        script = enemy.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        fight = run.match.combat
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, 0x10)})
        for _ in range(65):
            if fight.rockets:
                break
            run.step({enemy: (yaw, pitch, 0)})
        rock, = fight.rockets
        self.assertIs(rock.shooter, enemy)
        self.assertTrue(barrier.active)
        self.assertEqual(vitals.total, 400)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(barrier.active)
        self.assertIn(rock, fight.rockets)
        run.step({player: (0, 0, SECONDARY)})
        self.assertFalse(barrier.active)
        for _ in range(40):
            if rock not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(rock, fight.rockets)
        self.assertEqual(barrier.vitals.total, 700)
        self.assertEqual(vitals.total, 330)
        self.assertEqual(fight.stuns[player.body] - fight.tick, 50)
        self.assertEqual(script.component.entity_var(342).value(), 70)

    def test_recall_has_real_cooldown_and_redeployment_restores_protection(self):
        run, player, others, actor, vitals = self.arena()
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 400)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(actor.active)
        ability = actor.instance.states[111]
        self.assertGreater(ability.remaining(), 1.9)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(actor.active)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, 0)})
        run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertLess(vitals.total, 400)
        for _ in range(150):
            run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(ability.remaining(), 0)
        for frame in range(30):
            run.step({player: (0, 0, SECONDARY if frame < 20 else 0), enemy: (yaw, pitch, 0)})
        self.assertTrue(actor.active)
        before = vitals.total
        run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, before)

    def test_release_stops_motion_and_wall_probe_stops_held_motion(self):
        run, _player, _others, actor, _vitals = self.arena()
        position = actor.position
        for _ in range(20):
            run.step({})
        self.assertEqual(actor.position, position)
        run, player, _others, actor, _vitals = self.arena(deploy=False)
        x, _y, z = player.mover.position
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=8)
        for _ in range(100):
            run.step({player: (0, 0, SECONDARY)})
        self.assertTrue(actor.active)
        self.assertAlmostEqual(actor.position[0], x)
        self.assertAlmostEqual(actor.position[2] - z, 7.1, delta=0.002)

    def test_barrier_deployed_next_to_wall_does_not_spawn_through_it(self):
        run, player, _others, barrier, _vitals = self.arena(deploy=False)
        x, _y, z = player.mover.position
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=0.6)
        for _ in range(50):
            run.step({player: (0, 0, SECONDARY)})
        self.assertTrue(barrier.active)
        self.assertAlmostEqual(barrier.position[0], x)
        self.assertLess(barrier.position[2], z + 0.6)
        stopped = barrier.position
        for _ in range(15):
            run.step({})
        self.assertEqual(barrier.position, stopped)

    def test_oblique_deployment_measures_wall_clearance_along_launch_ray(self):
        yaw = 4096
        angle = math.pi / 8
        for wall_offset in (0.6, 8):
            with self.subTest(wall_offset=wall_offset):
                run, player, _others, barrier, _vitals = self.arena(deploy=False)
                eye = run.match.combat._eye(player)
                fixtures.SigmaCombatTests().wall(run, sideways=False, offset=wall_offset)
                run.step({player: (yaw, 0, 0)})
                for _ in range(60):
                    run.step({player: (yaw, 0, SECONDARY)})
                travel = max(0, wall_offset / math.cos(angle) - 0.9)
                self.assertTrue(barrier.active)
                self.assertAlmostEqual(barrier.position[0] - eye[0], travel * math.sin(angle), delta=0.002)
                self.assertAlmostEqual(barrier.position[2] - eye[2], travel * math.cos(angle), delta=0.002)
                stopped = barrier.position
                for _ in range(15):
                    run.step({player: (yaw, 0, 0)})
                self.assertEqual(barrier.phase, 3)
                self.assertEqual(barrier.position, stopped)

    def test_oblique_deployment_past_wall_edge_keeps_travelling(self):
        run, player, _others, barrier, _vitals = self.arena(deploy=False)
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=8)
        yaw = 8192
        run.step({player: (yaw, 0, 0)})
        for _ in range(65):
            run.step({player: (yaw, 0, SECONDARY)})
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.phase, 2)
        self.assertGreater(barrier.position[2] - player.mover.position[2], 8)
        before = barrier.position
        for _ in range(10):
            run.step({player: (yaw, 0, SECONDARY)})
        travel_per_axis = 10 * 0.016 * 16.5 / math.sqrt(2)
        self.assertAlmostEqual(barrier.position[0] - before[0], travel_per_axis)
        self.assertAlmostEqual(barrier.position[2] - before[2], travel_per_axis)

    def test_long_held_barrier_preserves_launch_heading_and_intercepts_after_release(self):
        run, player, (enemy,), barrier, vitals = self.arena(deploy=False)
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 45]
        for _ in range(20):
            run.step({player: (0, 0, SECONDARY)})
        started = barrier.position
        for _ in range(110):
            run.step({player: (16384, 0, SECONDARY)})
        self.assertEqual(barrier.phase, 2)
        self.assertTrue(barrier.active)
        self.assertAlmostEqual(barrier.position[0], x)
        self.assertAlmostEqual(barrier.position[2] - started[2], 110 * 0.016 * 16.5)
        self.assertGreater(barrier.position[2] - z, 30)
        for _ in range(10):
            run.step({player: (16384, 0, 0)})
        stopped = barrier.position
        self.assertEqual(barrier.phase, 3)
        yaw, pitch = aim(run.match.combat._eye(enemy), run.match.combat._eye(player))
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(barrier.position, stopped)
        self.assertLess(barrier.vitals.total, 700)
        self.assertEqual(vitals.total, 400)
        durability = barrier.vitals.total
        run.step({player: (16384, 0, SECONDARY)})
        run.step({})
        self.assertFalse(barrier.active)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(barrier.vitals.total, durability)
        self.assertLess(vitals.total, 400)

    def test_recalled_recharge_waits_two_seconds_then_restores_at_authored_rate_and_caps(self):
        run, player, others, actor, _vitals = self.arena()
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertEqual(actor.vitals.total, 640)
        for _ in range(200):
            run.step({})
        self.assertEqual(actor.vitals.total, 640)  # deployed shields cannot recharge
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertAlmostEqual(actor.recharge_at - run.match.combat.now, 2)
        for _ in range(120):
            run.step({})
        self.assertEqual(actor.vitals.total, 640)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(actor.vitals.total, 672, places=4)
        for _ in range(80):
            run.step({})
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(actor.instance.vars[586].value(), 700)

    def test_redeploy_stops_recharge_and_a_later_recall_restarts_its_delay(self):
        run, player, others, actor, _vitals = self.arena()
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        for _ in range(140):
            run.step({})
        self.assertGreater(actor.vitals.total, 640)
        self.assertLess(actor.vitals.total, 700)
        for frame in range(30):
            run.step({player: (0, 0, SECONDARY if frame < 8 else 0)})
        self.assertTrue(actor.active)
        self.assertIsNone(actor.recharge_at)
        before = actor.vitals.total
        for _ in range(150):
            run.step({})
        self.assertEqual(actor.vitals.total, before)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        for _ in range(120):
            run.step({})
        self.assertEqual(actor.vitals.total, before)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(actor.vitals.total - before, 32, places=4)

    def test_owner_ruleset_recharge_multiplier_is_used_instead_of_a_constant(self):
        run, player, others, actor, _vitals = self.arena()
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        actor.instance.component.set(0x591A, 0.5)
        self.assertEqual(sigma.barrier_recharge_rate(actor.instance), 40)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        for _ in range(150):
            run.step({})
        self.assertAlmostEqual(actor.vitals.total, 656, places=4)

    def test_recharge_integrates_skipped_ticks_and_does_not_repeat_for_duplicate_or_old_ticks(self):
        run, player, others, actor, _vitals = self.arena()
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        fight = run.match.combat
        tick, now = fight.tick + 150, fight.now + 2.4
        fight.update(now, tick, frames=False)
        self.assertAlmostEqual(actor.vitals.total, 672, places=4)
        fight.update(now, tick, frames=False)
        fight.update(now - 0.016, tick - 1, frames=False)
        fight.update(now, tick, frames=False)
        self.assertAlmostEqual(actor.vitals.total, 672, places=4)
        fight.update(now + 0.16, tick + 10, frames=False)
        self.assertAlmostEqual(actor.vitals.total, 684.8, places=4)

    def test_nearer_body_wins_before_barrier(self):
        run, player, others, actor, vitals = self.arena(names=("Sigma", "Attacker", "Victim"), deploy=False)
        enemy, victim = others
        x, y, z = player.mover.position
        victim.mover.position = [x, y, z + 7]
        for frame in range(50):
            run.step({player: (0, 0, SECONDARY if frame < 20 else 0)})
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        victim_health = run.match.combat.vitals_of(target)
        yaw, pitch = self.facing(run, enemy, victim)
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(victim_health.total, 180)
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(vitals.total, 400)

    def test_moving_barrier_loses_cover_after_passing_the_rifle_shooter(self):
        run, player, (enemy,), barrier, vitals = self.arena(deploy=False)
        z = player.mover.position[2]
        enemy_z = enemy.mover.position[2]
        yaw, pitch = self.facing(run, enemy, player)
        for _ in range(18):
            run.step({player: (0, 0, SECONDARY)})
        self.assertTrue(barrier.active)
        self.assertGreater(barrier.position[2], z + 2)
        self.assertLess(barrier.position[2], enemy_z - 1)
        run.step({player: (0, 0, SECONDARY), enemy: (yaw, pitch, FIRE)})
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(vitals.total, 400)

        for _ in range(40):
            run.step({player: (0, 0, SECONDARY)})
            if barrier.position[2] > enemy_z + 2:
                break
        self.assertGreater(barrier.position[2], enemy_z + 2)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.phase, 2)
        run.step({player: (0, 0, SECONDARY), enemy: (yaw, pitch, FIRE)})
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(vitals.total, 380)
        for _ in range(12):
            run.step({})
        self.assertEqual(barrier.phase, 3)
        self.assertTrue(barrier.active)
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(vitals.total, 360)

    def test_helix_uses_barrier_position_during_flight_after_launch(self):
        for keep_moving in (False, True):
            with self.subTest(keep_moving=keep_moving):
                run, player, (enemy,), barrier, vitals = self.arena(deploy=False)
                fight = run.match.combat
                yaw, pitch = self.facing(run, enemy, player)
                for _ in range(36):
                    run.step({player: (0, 0, SECONDARY)})
                buttons = SECONDARY if keep_moving else 0
                run.step({player: (0, 0, buttons), enemy: (yaw, pitch, SECONDARY)})
                rocket, = fight.rockets
                self.assertIs(rocket.shooter, enemy)
                origin, direction = rocket.position, rocket.direction
                self.assertIsNotNone(barrier.intersection(origin, direction, 2))
                self.assertEqual(barrier.vitals.total, 700)
                run.step({player: (0, 0, buttons)})
                if keep_moving:
                    self.assertIn(rocket, fight.rockets)
                    self.assertIsNone(barrier.intersection(origin, direction, 2))
                for _ in range(40):
                    if rocket not in fight.rockets:
                        break
                    run.step({player: (0, 0, buttons)})
                self.assertNotIn(rocket, fight.rockets)
                self.assertTrue(barrier.active)
                self.assertEqual(barrier.phase, 2 if keep_moving else 3)
                self.assertEqual(barrier.vitals.total, 700 if keep_moving else 580)
                self.assertEqual(vitals.total, 280 if keep_moving else 400)

    def test_helix_breaks_nearest_barrier_and_next_rifle_hit_reaches_second_barrier(self):
        run, rear_owner, (enemy, front_owner), rear, rear_health = self.arena(
            names=("Rear", "Soldier", "Front"), deploy=False,
        )
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rear_owner.team = front_owner.team = 0
        enemy.team = 1
        run.match.switch_hero(front_owner, heroes()[sigma.HERO])
        x, y, z = rear_owner.mover.position
        front_owner.mover.position = [x, y, z + 2]
        fight = run.match.combat
        front_target = next(target for target in fight._targets() if target.owner is front_owner)
        front_health = fight.vitals_of(front_target)
        for frame in range(50):
            buttons = SECONDARY if frame < 16 else 0
            run.step({rear_owner: (0, 0, buttons), front_owner: (0, 0, buttons)})
        self.assertEqual(len(fight.barriers), 2)
        front = next(barrier for barrier in fight.barriers.values() if barrier.owner is front_owner)
        self.assertIs(next(iter(fight.barriers.values())), rear)
        self.assertGreater(front.position[2], rear.position[2])
        self.assertLess(front.position[2], enemy.mover.position[2])
        self.assertTrue(front.active and rear.active)
        front.vitals.damage(660)
        yaw, pitch = self.facing(run, enemy, front_owner)
        run.step({enemy: (yaw, pitch, SECONDARY)})
        for _ in range(40):
            run.step({})
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertFalse(front.active)
        self.assertEqual(front.vitals.total, 0)
        self.assertEqual(rear.vitals.total, 700)
        self.assertEqual((front_health.total, rear_health.total), (400, 400))
        for _ in range(45):
            run.step({enemy: (yaw, pitch, FIRE)})
            self.assertEqual((front_health.total, rear_health.total), (400, 400))
            if rear.vitals.total < 700:
                break
        self.assertEqual(rear.vitals.total, 680)
        run.step({rear_owner: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(rear.active)
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
            if front_health.total < 400:
                break
        self.assertEqual(rear.vitals.total, 680)
        self.assertEqual((front_health.total, rear_health.total), (380, 400))

    def test_friendly_fire_crosses_ally_barrier_and_hits_an_enemy(self):
        run, player, others, actor, _vitals = self.arena(names=("Sigma", "Ally", "Enemy"), deploy=False)
        ally, enemy = others
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        ally.team, enemy.team = player.team, 1 - player.team
        x, y, z = player.mover.position
        ally.mover.position = [x, y, z - 6]
        for frame in range(50):
            run.step({player: (0, 0, SECONDARY if frame < 20 else 0)})
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        health = run.match.combat.vitals_of(target)
        yaw, pitch = self.facing(run, ally, enemy)
        run.step({ally: (yaw, pitch, FIRE)})
        self.assertEqual(health.total, 180)
        self.assertEqual(actor.vitals.total, 700)

    def test_activation_intercepts_an_already_flying_helix(self):
        run, player, others, actor, vitals = self.arena(deploy=False)
        enemy, = others
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, SECONDARY)})
        self.assertTrue(run.match.combat.rockets)
        for frame in range(40):
            run.step({player: (0, 0, SECONDARY if frame < 10 else 0), enemy: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 400)
        self.assertEqual(actor.vitals.total, 580)

    def test_nearer_grasp_takes_precedence_over_barrier_for_bullets_and_helix(self):
        for button, amount in ((FIRE, 60), (SECONDARY, 120)):
            with self.subTest(button=button):
                run, player, others, actor, _vitals = self.arena(
                    names=("Sigma", "Attacker", "Grasp"), deploy=False,
                )
                enemy, defender = others
                run.match.switch_hero(defender, heroes()[sigma.HERO])
                x, y, z = player.mover.position
                defender.mover.position = [x, y, z + 4]
                for frame in range(50):
                    run.step({player: (0, 0, SECONDARY if frame < 20 else 0)})
                run.step({defender: (0, 0, 0x08)})
                grasp = next(i for i in defender.body_script.component.instances.values()
                             if i.graph.index == sigma.GRASP)
                yaw, pitch = self.facing(run, enemy, player)
                run.step({enemy: (yaw, pitch, button)})
                for frame in range(30):
                    run.step({enemy: (yaw, pitch, FIRE if button == FIRE and frame < 19 else 0)})
                self.assertEqual(grasp.vars[21312].value(), amount)
                self.assertEqual(actor.vitals.total, 700)

    def test_six_nearby_helix_hits_break_barrier_without_leaking_breaking_explosion(self):
        run, player, enemies, actor, vitals = self.arena(
            names=tuple(["Sigma"] + [f"Enemy{i}" for i in range(6)]), deploy=False,
        )
        x, y, z = player.mover.position
        for index, enemy in enumerate(enemies):
            enemy.mover.position = [x + (index - 2.5) * 1.2, y, z + 10]
        for frame in range(50):
            run.step({player: (0, 0, SECONDARY if frame < 8 else 0)})
        self.assertLess(actor.position[2] - z, 3)
        shots = {e: (*self.facing(run, e, player), SECONDARY) for e in enemies}
        released = {e: (*self.facing(run, e, player), 0) for e in enemies}
        run.step(shots)
        for _ in range(40):
            run.step(released)
        self.assertEqual(actor.vitals.total, 0)
        self.assertFalse(actor.active)
        self.assertEqual(vitals.total, 400)
        self.assertGreater(actor.instance.states[111].remaining(), 4)
        run.step({player: (0, 0, SECONDARY)})
        run.step({})
        self.assertFalse(actor.active)
        for _ in range(350):
            run.step({})
        self.assertEqual(actor.instance.states[111].remaining(), 0)
        self.assertGreater(actor.vitals.total, 0)
        self.assertLess(actor.vitals.total, 700)
        for frame in range(30):
            run.step({player: (0, 0, SECONDARY if frame < 8 else 0)})
        self.assertTrue(actor.active)
        health = actor.vitals.total
        enemy = enemies[0]
        yaw, pitch = self.facing(run, enemy, player)
        run.step({enemy: (yaw, pitch, FIRE)})
        run.step({enemy: (yaw, pitch, 0)})
        self.assertAlmostEqual(actor.vitals.total, health - 20)
        self.assertEqual(vitals.total, 400)

    def test_death_and_body_replacement_retire_owned_actor(self):
        run, player, others, actor, vitals = self.arena()
        enemy, = others
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        run.step({})
        self.assertTrue(vitals.dead)
        self.assertNotIn(actor.entity, run.match.combat.barriers)
        self.assertEqual(actor.instance.component.entity_var(512).value(), expr.Entity(0))
        run, player, _others, actor, _vitals = self.arena()
        run.match.switch_hero(player, heroes()[SOLDIER])
        self.assertNotIn(actor.entity, run.match.combat.barriers)


if __name__ == "__main__":
    unittest.main()
