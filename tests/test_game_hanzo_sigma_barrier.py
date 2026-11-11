"""Verified dynamic mesh input for Hanzo versus Sigma Experimental Barrier."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim, in_world
from test_game_hanzo_dragons import prepared_profile
import test_game_hanzo_matrix as matrix_tests
from test_game_hanzo_sonic import profile
from test_game_mover import box, wall

from ow174.game import arrow_deflection, combat, matrix, mover, props, sigma_barrier
from ow174.game.collision import ALL_CATEGORIES, MOVER, World, flat_world
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph


class HanzoSigmaBarrierTests(unittest.TestCase):
    def test_sigma_barrier_uses_verified_dynamic_model_without_static_map_admission(self):
        path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
        if not path:
            self.skipTest("manifest-verified Sigma C015 model required")
        path = Path(path)
        raw = path.read_bytes()
        self.assertEqual(props.model_shapes(raw, ALL_CATEGORIES), [])
        shape, = props.model_shapes(raw, ALL_CATEGORIES, include_dynamic=True)
        self.assertEqual((shape[0], len(shape[2]), len(shape[3])), ("mesh", 62, 120))
        geometry = sigma_barrier.load_geometry(path)
        self.assertEqual((len(geometry.triangles), len(geometry.masks)), (120, 120))
        world = sigma_barrier.mesh(geometry)
        self.assertIsNotNone(world.cast_ray((0, 0, -2), (0, 0, 1), 4, MOVER))
        self.assertIsNone(world.cast_ray((8, 0, -2), (0, 0, 1), 4, MOVER))

    def arena(self, *, allied=False, collision=None, deploy=True):
        if not os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"):
            self.skipTest("manifest-verified Sigma C015 model required")
        run = Run(names=("Hanzo", "Sigma"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = collision if collision is not None else flat_world(0)
        match.game_map = replace(match.game_map, free_for_all=False)
        hanzo, sigma = run.players
        for player, hero, team, z in ((hanzo, 0x02E0000000000005, 0, 0), (sigma, sigma_barrier.HERO, 0 if allied else 1, 9)):
            self.assertTrue(match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position[:] = (0, 0, z)
            player.mover.body = None
        for _ in range(20):
            run.step({})
        fight = match.combat
        actor, = fight.sigma_barriers.actors.values()
        self.assertTrue(actor.child_requested)
        self.assertEqual(actor.phase, 1)
        self.assertFalse(actor.active())
        self.assertEqual(actor.vitals.total, 700)
        if not deploy:
            return run, hanzo, sigma, actor
        for _ in range(16):
            run.step({sigma: (-32768, 0, 2)})
        self.assertEqual(actor.phase, 2)
        self.assertTrue(actor.active())
        for _ in range(3):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(actor.phase, 3)
        self.assertGreater(actor.position[2], 0)
        self.assertLess(actor.position[2], 8)
        return run, hanzo, sigma, actor

    def draw(self, run, hanzo, button=0):
        if button:
            run.step({hanzo: (0, 0, button)})
            run.step({})
        for _ in range(140 if button == 16 else 70):
            run.step({hanzo: (0, 0, 1)})
        if button != 16:
            for _ in range(12):
                run.step({})

    def test_actual_sigma_deploys_dynamic_barrier_and_stops_full_draw(self):
        run, hanzo, sigma, actor = self.arena()
        fight = run.match.combat
        deployed = actor.position
        self.draw(run, hanzo)
        self.assertEqual(actor.position, deployed)
        self.assertEqual(actor.vitals.total, 575)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)

    def test_sigma_barrier_blocks_sonic_and_storm_without_body_effects(self):
        for button, damage in ((8, 125), (16, 350)):
            with self.subTest(button=button):
                run, hanzo, sigma, actor = self.arena()
                fight = run.match.combat
                if button == 8:
                    fight.configure_sonic(profile())
                self.draw(run, hanzo, button)
                self.assertEqual(actor.vitals.total, 700 - damage)
                self.assertEqual(fight.vitals[sigma.body].total, 400)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.sonars)

    def test_sigma_barrier_break_absorbs_last_arrow_and_later_bow_hits_body(self):
        run, hanzo, sigma, actor = self.arena()
        fight = run.match.combat
        fight.sigma_barriers.damage(actor, 625)
        self.draw(run, hanzo)
        self.assertFalse(actor.active())
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.draw(run, hanzo)
        self.assertEqual(fight.vitals[sigma.body].total, 275)

    def test_allied_sigma_barrier_passes_arrow_and_recall_recovers_health(self):
        run, hanzo, sigma, actor = self.arena(allied=True)
        match = run.match
        fight = match.combat
        enemy = match.add_player(3, "Enemy beyond shield", SOLDIER, 1, False)
        in_world(match, enemy)
        run.players.append(enemy)
        self.assertTrue(match.switch_hero(enemy, heroes()[SOLDIER]))
        enemy.team = 1
        enemy.mover.position[:] = (0, 0, 12)
        enemy.mover.body = None
        self.draw(run, hanzo)
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(fight.vitals[enemy.body].total, 75)
        fight.sigma_barriers.damage(actor, 300)
        run.step({sigma: (-32768, 0, 2)})
        run.step({})
        self.assertEqual(actor.phase, 1)
        self.assertFalse(actor.active())
        for _ in range(40):
            run.step({})
        self.assertEqual(actor.vitals.total, 400)
        for _ in range(180):
            run.step({})
        self.assertGreater(actor.vitals.total, 400)
        self.assertLess(actor.vitals.total, 700)
        self.assertTrue(match.switch_hero(sigma, heroes()[SOLDIER]))
        self.assertFalse(fight.sigma_barriers.actors)

    def test_sigma_barrier_takes_quick_melee_and_helix_without_body_damage(self):
        run, hanzo, sigma, actor = self.arena()
        match = run.match
        fight = match.combat
        hanzo.mover.position[2] = actor.position[2] - 1.8
        hanzo.mover.body = None
        run.step({hanzo: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(actor.vitals.total, 670)
        attacker = match.add_player(3, "Soldier", SOLDIER, 0, False)
        in_world(match, attacker)
        run.players.append(attacker)
        self.assertTrue(match.switch_hero(attacker, heroes()[SOLDIER]))
        attacker.team = 0
        attacker.mover.position[:] = (0, 0, 0)
        attacker.mover.body = None
        for _ in range(20):
            run.step({})
        yaw, pitch = aim(fight._eye(attacker), actor.position)
        run.step({attacker: (yaw, pitch, 2)})
        rocket, = fight.rockets
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(actor.vitals.total, 550)
        self.assertEqual(fight.vitals[sigma.body].total, 400)

    def test_sigma_barrier_stops_initial_dragonstrike_and_moving_phase_reaches_sigma(self):
        run, hanzo, sigma, actor = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(prepared_profile())
        hanzo.body_script.ult_seconds = None
        hanzo.body_script.set_ult_charge(1680)
        run.step({hanzo: (0, 0, 32)})
        for _ in range(100):
            run.step({})
            if fight.dragons:
                break
        flight, = fight.dragons
        self.assertEqual(actor.vitals.total, 575)
        self.assertEqual(flight.source_entity, hanzo.body)
        self.assertLess(abs(flight.origin[2] - actor.position[2]), 1)
        for _ in range(75):
            run.step({})
            if fight.vitals[sigma.body].total < 400:
                break
        self.assertLess(fight.vitals[sigma.body].total, 400)
        self.assertEqual(actor.vitals.total, 575)

    def add_player(self, run, name, hero, team, z, *, body=None):
        match = run.match
        player = match.add_player(3, name, hero, team, False)
        in_world(match, player)
        run.players.append(player)
        selection = heroes()[hero]
        self.assertTrue(match.switch_hero(player, selection if body is None else replace(selection, body=body)))
        player.team = team
        player.mover.position[:] = (0, 0, z)
        player.mover.body = None
        return player

    def test_sigma_barrier_orders_bow_and_sonic_against_deflect_grasp_and_matrix(self):
        graphs, movers, tags = (os.environ.get("ORPHEUS_MATRIX_" + name) for name in ("GRAPHS", "MOVERS", "TAGS"))
        if not all((graphs, movers, tags)):
            self.skipTest("prepared 0407 graphs, movers and projectile tags required")
        self.addCleanup(matrix_tests.HanzoMatrixTests.reset_catalogs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", Path(graphs)))
        self.enterContext(patch.object(mover, "MOVERS_PATH", Path(movers)))
        matrix_tests.HanzoMatrixTests.reset_catalogs()
        admitted = matrix.load_admitted_definitions(Path(tags))
        for guid, activation, base_hp in ((arrow_deflection.GENJI, 16, 200),
                                          (sigma_barrier.HERO, 8, 400), (matrix.DVA, 2, 600)):
            for sonic in (False, True):
                for defense_first in (False, True):
                    with self.subTest(hero=guid, sonic=sonic, defense_first=defense_first):
                        run, hanzo, sigma, actor = self.arena()
                        fight = run.match.combat
                        fight.matrix_definitions = admitted
                        far_offset = 17 if guid == matrix.DVA else 5
                        z = actor.position[2] + (-1 if defense_first else far_offset)
                        defender = self.add_player(
                            run, "Defender", guid, sigma.team, z, body=matrix.MECH if guid == matrix.DVA else None,
                        )
                        if sonic:
                            fight.configure_sonic(profile())
                            run.step({hanzo: (0, 0, 8)})
                            run.step({})
                        for _ in range(70):
                            run.step({hanzo: (0, 0, 1)})
                        if guid == matrix.DVA:
                            run.step({hanzo: (0, 0, 1), defender: (-32768, 0, activation)})
                        run.step({defender: (-32768, 0, activation)})
                        arrow, = fight.rockets
                        self.assertTrue(arrow.matrix_absorbable)
                        ray = arrow.position, arrow.direction, 20
                        shield_distance = fight.sigma_barriers.intersect(actor, *ray)
                        targets = {t.owner: t for t in fight._targets()}
                        target = targets[defender]
                        pools = {p: fight.vitals_of(targets[p]) for p in (hanzo, sigma, defender)}
                        grasp = None
                        if guid == arrow_deflection.GENJI:
                            contact = arrow_deflection.trace(target, *ray)
                        elif guid == sigma_barrier.HERO:
                            contact, grasp = fight.grasp.trace(target, *ray)
                        else:
                            contact = matrix.trace(target, *ray)
                        self.assertIsNotNone(shield_distance)
                        self.assertIsNotNone(contact)
                        self.assertEqual(contact < shield_distance, defense_first, (contact, shield_distance))
                        for _ in range(20):
                            if not fight.rockets:
                                break
                            run.step({defender: (-32768, 0, activation if guid == matrix.DVA else 0)})
                        self.assertFalse(fight.rockets)
                        self.assertEqual(actor.vitals.total, 700 if defense_first else 575)
                        reflected = defense_first and guid == arrow_deflection.GENJI
                        self.assertEqual(pools[hanzo].total, 75 if reflected else 200)
                        self.assertEqual(pools[sigma].total, 400)
                        self.assertEqual(pools[defender].total, base_hp)
                        self.assertEqual(arrow.source_entity, defender.body if reflected else hanzo.body)
                        if grasp is not None:
                            self.assertEqual(grasp.instance.find_var(0, 21312).value(), 125 if defense_first else 0)
                            for _ in range(160):
                                if not grasp.active:
                                    break
                                run.step({defender: (-32768, 0, 0)})
                            self.assertFalse(grasp.active)
                            self.assertAlmostEqual(pools[defender].bonus_shields, 75 if defense_first else 0, places=4)
                        if sonic and reflected:
                            beacon, = fight.sonars
                            self.assertEqual((beacon.source_entity, beacon.attached), (defender.body, hanzo.body))
                        else:
                            self.assertFalse(fight.sonars)

    def test_incoming_flashbang_respects_sigma_barrier_faction_and_same_blast_break(self):
        for initial, allied_to_caster, barrier_hp, hanzo_hp in (
            (700, False, 675, 200), (15, False, 0, 200), (700, True, 700, 175),
        ):
            with self.subTest(initial=initial, allied_to_caster=allied_to_caster):
                run, hanzo, sigma, actor = self.arena(allied=not allied_to_caster)
                fight = run.match.combat
                service = fight.sigma_barriers
                hanzo.mover.position[2] = actor.position[2] + 1.5
                hanzo.mover.body = None
                caster = self.add_player(run, "McCree", 0x02E0000000000042, 1, actor.position[2] - 2)
                if initial < 700:
                    service.damage(actor, 700 - initial)
                bow = aim(fight._eye(hanzo), fight._eye(caster))
                flash = aim(fight._eye(caster), fight._eye(hanzo))
                draw = {hanzo: (*bow, 1)}
                for _ in range(70):
                    run.step(draw)
                run.step({**draw, caster: (*flash, 16)})
                for _ in range(35):
                    if fight.rockets:
                        break
                    run.step(draw)
                grenade, = fight.rockets
                self.assertEqual((grenade.blast.amount, grenade.blast.effect), (25, combat.FLASHBANG_EFFECT))
                for _ in range(25):
                    if grenade not in fight.rockets:
                        break
                    run.step(draw)
                self.assertFalse(fight.rockets)
                self.assertEqual(actor.vitals.total, barrier_hp)
                self.assertEqual(fight.vitals[hanzo.body].total, hanzo_hp)
                self.assertEqual(fight.is_stunned(hanzo), allied_to_caster)
                run.step({hanzo: (*bow, 0)})
                if allied_to_caster:
                    self.assertFalse(fight.rockets)
                else:
                    arrow, = fight.rockets
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertEqual(arrow.direct.amount, 125)

    def test_static_wall_stops_deploy_motion_and_takes_sonic_before_sigma_barrier(self):
        world = World(box(-30, 30, -1, 0, -30, 30) + wall(-8, 6.5, 8, 6.5))
        run, hanzo, sigma, actor = self.arena(collision=world)
        fight = run.match.combat
        fight.configure_sonic(profile())
        self.assertAlmostEqual(actor.position[2], 7.4, places=5)
        self.draw(run, hanzo, 8)
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        beacon, = fight.sonars
        self.assertIsNone(beacon.attached)
        self.assertAlmostEqual(beacon.position[2], 6.5, places=4)

    def test_actual_bow_kills_sigma_behind_his_shield_and_retires_old_pet(self):
        run, hanzo, sigma, actor = self.arena()
        fight = run.match.combat
        body = sigma.body
        pool = fight.vitals[sigma.body]
        pool.damage(pool.total - 1)
        hanzo.mover.position[2] = 7
        hanzo.mover.body = None
        with patch.object(combat, "respawn_seconds", return_value=120 * combat.TICK):
            self.draw(run, hanzo)
            self.assertTrue(pool.dead)
            self.assertEqual(pool.killer, hanzo.body)
            self.assertNotIn(actor.entity, fight.sigma_barriers.actors)
            self.assertEqual(actor.vitals.total, 700)
            for _ in range(120):
                if sigma.body != body:
                    break
                run.step({})
        self.assertNotEqual(sigma.body, body)
        fresh, = fight.sigma_barriers.actors.values()
        self.assertNotEqual(fresh.entity, actor.entity)
        self.assertEqual((fresh.phase, fresh.vitals.total, fresh.child_requested), (1, 700, True))
        self.assertFalse(fresh.active())

    def test_advancing_sigma_barrier_orders_inflight_arrows_across_scheduler_jump(self):
        for sonic in (False, True):
            for facing, shield_hp, body_hp in ((-32768, 575, 400), (0, 700, 275)):
                outcomes = []
                for skipped in (False, True):
                    with self.subTest(sonic=sonic, facing=facing, skipped=skipped):
                        run, hanzo, sigma, actor = self.arena(deploy=False)
                        match = run.match
                        fight = match.combat
                        if sonic:
                            fight.configure_sonic(profile())
                            run.step({hanzo: (0, 0, 8)})
                            run.step({})
                        for _ in range(70):
                            run.step({hanzo: (0, 0, 1)})
                        for _ in range(4):
                            run.step({hanzo: (0, 0, 1), sigma: (facing, 0, 2)})
                        run.step({sigma: (facing, 0, 2)})
                        arrow, = fight.rockets
                        self.assertEqual(actor.phase, 2)
                        run.step({sigma: (facing, 0, 2)})
                        self.assertIn(arrow, fight.rockets)
                        self.assertEqual(arrow.last_tick, run.frame)
                        for advance in ((40,) if skipped else (1,) * 40):
                            run.frame += advance
                            match.update(run.frame * combat.TICK, run.frame)
                        self.assertFalse(fight.rockets)
                        self.assertEqual(actor.vitals.total, shield_hp)
                        self.assertEqual(fight.vitals[sigma.body].total, body_hp)
                        if facing == -32768:
                            self.assertLess(actor.position[2], 0)
                            self.assertGreater(arrow.position[2], 0)
                        if sonic and body_hp < 400:
                            beacon, = fight.sonars
                            self.assertEqual((beacon.source_entity, beacon.attached), (hanzo.body, sigma.body))
                        else:
                            self.assertFalse(fight.sonars)
                        outcomes.append((actor.position, arrow.position))
                for continuous, jumped in zip(*outcomes, strict=True):
                    for a, b in zip(continuous, jumped, strict=True):
                        self.assertAlmostEqual(a, b, places=6)

    def test_break_blocks_early_deploy_and_fresh_press_uses_recharged_pet(self):
        run, hanzo, sigma, actor = self.arena()
        fight = run.match.combat
        service = fight.sigma_barriers
        service.damage(actor, 625)
        for _ in range(70):
            run.step({hanzo: (0, 0, 1)})
        run.step({})
        for _ in range(12):
            run.step({})
            if actor.vitals.total == 0:
                break
        self.assertEqual(actor.vitals.total, 0)
        broken_at = run.frame
        deploy_input = actor.instance.graph.node(18).state
        for _ in range(298):
            run.step({})
        self.assertEqual(actor.phase, 1)
        self.assertGreater(actor.vitals.total, 0)
        self.assertFalse(actor.instance.states[deploy_input].active)
        for _ in range(3):
            run.step({sigma: (-32768, 0, 2)})
        for _ in range(10):
            run.step({})
        self.assertEqual(run.frame - broken_at, 311)
        self.assertEqual(actor.phase, 1)
        self.assertFalse(actor.instance.states[deploy_input].active)
        run.step({})
        self.assertEqual(actor.phase, 1)
        self.assertTrue(actor.instance.states[deploy_input].active)
        for _ in range(16):
            run.step({sigma: (-32768, 0, 2)})
            if actor.phase == 2:
                break
        self.assertEqual(actor.phase, 2)
        self.assertIs(service.actors[actor.entity], actor)
        self.assertGreater(actor.vitals.total, 230)
        self.assertLess(actor.vitals.total, 250)
        self.assertEqual(fight.vitals[sigma.body].total, 400)

    def test_recall_blocks_early_deploy_without_refilling_pet(self):
        run, _, sigma, actor = self.arena()
        service = run.match.combat.sigma_barriers
        service.damage(actor, 300)
        for _ in range(20):
            run.step({})
        run.step({sigma: (-32768, 0, 2)})
        for _ in range(20):
            run.step({})
            if actor.phase == 1:
                break
        self.assertEqual(actor.phase, 1)
        deploy_input = actor.instance.graph.node(18).state
        for _ in range(111):
            run.step({})
        for _ in range(3):
            run.step({sigma: (-32768, 0, 2)})
        for _ in range(9):
            run.step({})
        self.assertEqual(actor.phase, 1)
        self.assertEqual(actor.vitals.total, 400)
        self.assertFalse(actor.instance.states[deploy_input].active)
        run.step({})
        self.assertTrue(actor.instance.states[deploy_input].active)
        self.assertEqual(actor.phase, 1)
        for _ in range(16):
            run.step({sigma: (-32768, 0, 2)})
            if actor.phase == 2:
                break
        self.assertEqual(actor.phase, 2)
        self.assertIs(service.actors[actor.entity], actor)
        self.assertGreaterEqual(actor.vitals.total, 400)
        self.assertLess(actor.vitals.total, 410)
