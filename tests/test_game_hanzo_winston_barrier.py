"""Actual Hanzo and Winston commands against the reconstructed barrier sphere."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_sonic as sonic_tests
from test_game_combat import Run, aim, in_world

from ow174.game import arrow_deflection, bodytrace, combat, matrix, movelog, sigma_grasp, winston_barrier
from ow174.game.collision import flat_world
from ow174.game.content import SOLDIER, heroes


class HanzoWinstonBarrierTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, *, allied=False, deploy=True):
        run = Run(names=("Hanzo", "Winston"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = flat_world(0)
        match.game_map = replace(match.game_map, free_for_all=False)
        hanzo, winston = run.players
        self.assertTrue(match.switch_hero(hanzo, heroes()[0x02E0000000000005]))
        self.assertTrue(match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        hanzo.team, winston.team = 0, 0 if allied else 1
        for actor, z in ((hanzo, 0), (winston, 9)):
            actor.mover.position[:] = (0, 0, z)
            actor.mover.body = None
        for _ in range(20):
            run.step({})
        if not deploy:
            return run, hanzo, winston, None
        run.step({winston: (0, 0, 16)})
        service = match.combat.winston_barriers
        self.assertNotIn(winston.body, service.actors)
        for _ in range(8):
            run.step({})
            if winston.body in service.actors:
                break
        bubble = service.actors[winston.body]
        service.fight.vitals_of(next(target for target in service.fight._targets() if target.owner is winston))
        self.assertEqual(bubble.health, 700)
        self.assertAlmostEqual((bubble.expires - bubble.started) * combat.TICK, 9)
        return run, hanzo, winston, bubble

    def draw(self, run, hanzo, button=0):
        if button:
            run.step({hanzo: (0, 0, button)})
            run.step({})
        for _ in range(140 if button == 16 else 70):
            run.step({hanzo: (0, 0, 1)})
        if button != 16:
            run.step({hanzo: (0, 0, 0)})
            for _ in range(12):
                run.step({})

    def incoming(self, attacker_guid, *, pass_shell=False):
        run, hanzo, winston, bubble = self.arena()
        match = run.match
        team = winston.team if pass_shell else 1 - winston.team
        hanzo.team = 1 - team
        attacker = match.add_player(3, "Enemy", attacker_guid, team, False)
        in_world(match, attacker)
        run.players.append(attacker)
        self.assertTrue(match.switch_hero(attacker, heroes()[attacker_guid]))
        attacker.team = team
        for actor, z in ((hanzo, 5.5), (attacker, 1)):
            actor.mover.position[:] = (0, 0, z)
            actor.mover.body = None
        for _ in range(20):
            run.step({})
        return run, hanzo, winston, bubble, attacker

    def test_actual_winston_barrier_blocks_bow_sonic_and_five_storm_arrows(self):
        for button, damage in ((0, 125), (8, 125), (16, 350)):
            with self.subTest(button=button):
                run, hanzo, winston, bubble = self.arena()
                fight = run.match.combat
                if button == 8:
                    fight.configure_sonic(sonic_tests.profile())
                self.draw(run, hanzo, button)
                self.assertEqual(bubble.health, 700 - damage)
                self.assertEqual(fight.vitals[winston.body].total, 500)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.rockets)

    def test_winston_barrier_break_has_no_spill_and_allied_shell_passes_bow(self):
        run, hanzo, winston, bubble = self.arena()
        fight = run.match.combat
        fight.winston_barriers.damage(bubble, 625)
        self.draw(run, hanzo)
        self.assertEqual(bubble.health, 0)
        self.assertEqual(fight.vitals[winston.body].total, 500)
        self.draw(run, hanzo)
        self.assertEqual(fight.vitals[winston.body].total, 380)

        run, hanzo, winston, bubble = self.arena(allied=True)
        match = run.match
        fight = match.combat
        enemy = match.add_player(3, "Enemy behind shell", SOLDIER, 1, False)
        in_world(match, enemy)
        run.players.append(enemy)
        self.assertTrue(match.switch_hero(enemy, heroes()[SOLDIER]))
        enemy.team = 1
        enemy.mover.position[:] = (0, 0, 12)
        enemy.mover.body = None
        self.draw(run, hanzo)
        self.assertEqual(bubble.health, 700)
        self.assertEqual(fight.vitals[winston.body].total, 500)
        self.assertEqual(fight.vitals[enemy.body].total, 75)
        self.assertFalse(fight.rockets)

    def test_winston_barrier_quick_melee_contacts_sphere_without_body(self):
        run, hanzo, winston, bubble = self.arena()
        fight = run.match.combat
        hanzo.mover.position[2] = 2.9
        hanzo.mover.body = None
        run.step({hanzo: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(bubble.health, 670)
        self.assertEqual(fight.vitals[winston.body].total, 500)
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_winston_barrier_blocks_direct_and_ground_helix_before_hanzo(self):
        for ground, initial, pass_shell, shield_hp, hanzo_hp in (
            (False, 700, False, 580, 200), (False, 100, False, 0, 200),
            (False, 700, True, 700, 80), (True, 700, False, 620, 200),
            (True, 60, False, 0, 200),
        ):
            with self.subTest(ground=ground, initial=initial, pass_shell=pass_shell):
                run, hanzo, winston, bubble, soldier = self.incoming(SOLDIER, pass_shell=pass_shell)
                fight = run.match.combat
                service = fight.winston_barriers
                if initial < 700:
                    service.damage(bubble, 700 - initial)
                point = (0, 0, 3.8) if ground else (0, 1, 5.5)
                origin = fight._eye(soldier)
                run.step({soldier: (*aim(origin, point), 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.shot.definition, 0x040000000000072C)
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                distance = service.intersect(bubble, origin, rocket.direction, 20)
                for _ in range(30):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(bubble.health, shield_hp)
                self.assertEqual(fight.vitals[hanzo.body].total, hanzo_hp)
                self.assertEqual(fight.vitals[winston.body].total, 500)
                if ground:
                    self.assertAlmostEqual(rocket.position[1], 0, places=4)
                    self.assertAlmostEqual(rocket.position[2], point[2], places=2)
                elif not pass_shell:
                    self.assertAlmostEqual(math.dist(origin, rocket.position), distance, places=5)
                    self.assertFalse(soldier.client.messages(combat.HIT))

    def test_winston_barrier_blocks_flashbang_and_preserves_draw_even_on_break(self):
        for initial, pass_shell, shield_hp, hanzo_hp in (
            (700, False, 675, 200), (15, False, 0, 200), (700, True, 700, 175),
        ):
            with self.subTest(initial=initial, pass_shell=pass_shell):
                run, hanzo, winston, bubble, caster = self.incoming(
                    0x02E0000000000042, pass_shell=pass_shell,
                )
                fight = run.match.combat
                if initial < 700:
                    fight.winston_barriers.damage(bubble, 700 - initial)
                bow = aim(fight._eye(hanzo), (0, 1, 1))
                flash = aim(fight._eye(caster), (0, 1, 5.5))
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
                self.assertNotIn(grenade, fight.rockets)
                self.assertEqual(bubble.health, shield_hp)
                self.assertEqual(fight.vitals[hanzo.body].total, hanzo_hp)
                self.assertEqual(fight.is_stunned(hanzo), pass_shell)
                self.assertEqual(fight.vitals[winston.body].total, 500)
                run.step({hanzo: (*bow, 0)})
                if pass_shell:
                    self.assertFalse(fight.rockets)
                else:
                    arrow, = fight.rockets
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertEqual(arrow.direct.amount, 125)

    def test_winston_barrier_expiry_orders_inflight_arrows_across_tick_jump(self):
        for sonic in (False, True):
            for shooter_z, shield_hp, body_hp in ((0, 575, 500), (-20, 700, 380)):
                for skipped in (False, True):
                    with self.subTest(sonic=sonic, shooter_z=shooter_z, skipped=skipped):
                        run, hanzo, winston, bubble = self.arena()
                        match = run.match
                        fight = match.combat
                        if sonic:
                            fight.configure_sonic(sonic_tests.profile())
                        hanzo.mover.position[2] = shooter_z
                        hanzo.mover.body = None
                        expiry = math.ceil(bubble.expires)
                        run.frame = expiry - (83 if sonic else 81)
                        match.update(run.frame * combat.TICK, run.frame)
                        if sonic:
                            run.step({hanzo: (0, 0, 8)})
                            run.step({})
                        for _ in range(70):
                            run.step({hanzo: (0, 0, 1)})
                        run.step({hanzo: (0, 0, 0)})
                        arrow, = fight.rockets
                        self.assertEqual(run.frame, expiry - 10)
                        run.frame += 1
                        match.update(run.frame * combat.TICK, run.frame)
                        self.assertEqual(arrow.last_tick, run.frame)
                        self.assertIs(fight.winston_barriers.actors[winston.body], bubble)
                        for advance in ((60,) if skipped else (1,) * 60):
                            run.frame += advance
                            match.update(run.frame * combat.TICK, run.frame)
                        self.assertNotIn(winston.body, fight.winston_barriers.actors)
                        self.assertFalse(fight.rockets)
                        self.assertEqual((bubble.health, fight.vitals[winston.body].total), (shield_hp, body_hp))
                        if sonic and body_hp < 500:
                            beacon, = fight.sonars
                            self.assertEqual((beacon.source_entity, beacon.attached), (hanzo.body, winston.body))
                        else:
                            self.assertFalse(fight.sonars)

    def test_winston_barrier_deploys_without_further_input_and_orders_inflight_bow(self):
        for shooter_z, shield_hp, body_hp in ((3.5, 700, 380), (-12, 575, 500)):
            for skipped in (False, True):
                with self.subTest(shooter_z=shooter_z, skipped=skipped):
                    run, hanzo, winston, _ = self.arena(deploy=False)
                    match = run.match
                    fight = match.combat
                    service = fight.winston_barriers
                    hanzo.mover.position[2] = shooter_z
                    hanzo.mover.body = None
                    for _ in range(70):
                        run.step({hanzo: (0, 0, 1)})
                    run.step({hanzo: (0, 0, 0), winston: (0, 0, 16)})
                    self.assertIn(winston.body, service.pending)
                    self.assertNotIn(winston.body, service.actors)
                    arrow, = fight.rockets
                    cast = run.frame
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame)
                    self.assertIn(arrow, fight.rockets)
                    for advance in ((60,) if skipped else (1,) * 60):
                        run.frame += advance
                        match.update(run.frame * combat.TICK, run.frame)
                    bubble = service.actors[winston.body]
                    self.assertEqual(bubble.started, cast + 3)
                    self.assertEqual((bubble.health, fight.vitals[winston.body].total), (shield_hp, body_hp))
                    self.assertNotIn(winston.body, service.pending)
                    self.assertFalse(fight.rockets)

    def test_winston_barrier_initial_dragonstrike_transforms_at_sphere(self):
        run, hanzo, winston, bubble = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hanzo.body_script.ult_seconds = None
        hanzo.body_script.set_ult_charge(1680)
        run.step({hanzo: (0, 0, 32)})
        for _ in range(100):
            run.step({})
            if fight.dragons:
                break
        flight, = fight.dragons
        self.assertEqual(bubble.health, 575)
        self.assertEqual(flight.source_entity, hanzo.body)
        self.assertAlmostEqual(
            sum((p - c) ** 2 for p, c in zip(flight.origin, bubble.centre, strict=True)),
            winston_barrier.RADIUS ** 2,
        )
        for _ in range(75):
            run.step({})
            if fight.vitals[winston.body].total < 500:
                break
        self.assertLess(fight.vitals[winston.body].total, 500)
        self.assertEqual(bubble.health, 575)

    def test_winston_sphere_orders_initial_dragonstrike_against_deflect_and_grasp(self):
        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            self.skipTest("prepared projectile admission tags required")
        admitted = matrix.load_admitted_definitions(Path(tags))
        profile = dragon_tests.prepared_profile()
        for guid, activation in ((arrow_deflection.GENJI, 16), (sigma_grasp.SIGMA, 8)):
            for defender_z, defense_first in ((3, True), (8, False)):
                with self.subTest(guid=guid, defense_first=defense_first):
                    run, hanzo, winston, bubble = self.arena()
                    match = run.match
                    fight = match.combat
                    fight.configure_dragonstrike(profile)
                    fight.matrix_definitions = admitted
                    defender = match.add_player(3, "Defender", guid, winston.team, False)
                    in_world(match, defender)
                    run.players.append(defender)
                    self.assertTrue(match.switch_hero(defender, heroes()[guid]))
                    defender.team = winston.team
                    defender.mover.position[:] = (0, 0, defender_z)
                    defender.mover.body = None
                    hanzo.body_script.ult_seconds = None
                    hanzo.body_script.set_ult_charge(1680)
                    run.step({hanzo: (0, 0, 32)})
                    for frame in range(1, 80):
                        run.step({defender: (-32768, 0, activation if frame == 53 else 0)})
                        if fight.rockets:
                            break
                    arrow, = fight.rockets
                    self.assertIs(arrow.continuation, profile)
                    self.assertTrue(arrow.matrix_absorbable)
                    target = next(t for t in fight._targets() if t.owner is defender)
                    pool = fight.vitals_of(target)
                    shell = fight.winston_barriers.intersect(bubble, arrow.position, arrow.direction, 10)
                    grasp = None
                    if guid == arrow_deflection.GENJI:
                        contact = arrow_deflection.trace(target, arrow.position, arrow.direction, 10)
                    else:
                        contact, grasp = fight.grasp.trace(target, arrow.position, arrow.direction, 10)
                    self.assertIsNotNone(shell)
                    self.assertIsNotNone(contact)
                    self.assertEqual(contact < shell, defense_first)

                    for _ in range(35):
                        run.step({defender: (-32768, 0, 0)})
                        if arrow not in fight.rockets:
                            break
                    self.assertNotIn(arrow, fight.rockets)
                    self.assertEqual(bubble.health, 700 if defense_first else 575)
                    if grasp is not None:
                        self.assertEqual(grasp.instance.find_var(0, 21312).value(), 125 if defense_first else 0)
                    if defense_first and grasp is not None:
                        self.assertFalse(fight.dragons)
                        self.assertEqual(pool.total, 400)
                        for _ in range(160):
                            run.step({defender: (-32768, 0, 0)})
                            if not grasp.active:
                                break
                        self.assertFalse(grasp.active)
                        self.assertEqual(pool.bonus_shields, 75)
                        self.assertEqual(bubble.health, 700)
                        self.assertEqual(fight.vitals[winston.body].total, 500)
                        continue

                    flight, = fight.dragons
                    self.assertEqual(flight.source_entity, defender.body if defense_first else hanzo.body)
                    self.assertEqual(flight.team, defender.team if defense_first else hanzo.team)
                    if defense_first:
                        self.assertIn(hanzo.body, arrow.hit_entities)
                        self.assertEqual(fight.vitals[winston.body].total, 500)
                    else:
                        self.assertFalse(arrow.hit_entities)
                        self.assertAlmostEqual(math.dist(flight.origin, bubble.centre), winston_barrier.RADIUS)
                        for _ in range(75):
                            run.step({defender: (-32768, 0, 0)})
                            if fight.vitals[winston.body].total < 500:
                                break
                        self.assertLess(fight.vitals[winston.body].total, 500)
                        self.assertEqual(bubble.health, 575)


    def test_winston_sphere_orders_actual_arrows_against_deflect_and_grasp(self):
        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            self.skipTest("prepared projectile admission tags required")
        admitted = matrix.load_admitted_definitions(Path(tags))
        for guid, activation in ((arrow_deflection.GENJI, 16), (sigma_grasp.SIGMA, 8)):
            for sonic in (False, True):
                for defender_z, defense_first in ((3, True), (8, False)):
                    with self.subTest(guid=guid, sonic=sonic, defense_first=defense_first):
                        run, hanzo, winston, bubble = self.arena()
                        match = run.match
                        fight = match.combat
                        fight.matrix_definitions = admitted
                        defender = match.add_player(3, "Defender", guid, winston.team, False)
                        hanzo_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
                        in_world(match, defender)
                        run.players.append(defender)
                        self.assertTrue(match.switch_hero(defender, heroes()[guid]))
                        defender.team = winston.team
                        defender.mover.position[:] = (0, 0, defender_z)
                        defender.mover.body = None
                        if sonic:
                            fight.configure_sonic(sonic_tests.profile())
                            run.step({hanzo: (0, 0, 8)})
                            run.step({})
                        for _ in range(70):
                            run.step({hanzo: (0, 0, 1)})
                        run.step({hanzo: (0, 0, 0), defender: (-32768, 0, activation)})
                        arrow, = fight.rockets
                        self.assertTrue(arrow.matrix_absorbable)
                        service = fight.winston_barriers
                        shell = service.intersect(bubble, arrow.position, arrow.direction, 12)
                        target = next(t for t in fight._targets() if t.owner is defender)
                        defender_pool = fight.vitals_of(target)
                        if guid == arrow_deflection.GENJI:
                            contact = arrow_deflection.trace(target, arrow.position, arrow.direction, 12)
                        else:
                            contact, grasp = fight.grasp.trace(target, arrow.position, arrow.direction, 12)
                        self.assertIsNotNone(shell)
                        self.assertIsNotNone(contact)
                        self.assertEqual(contact < shell, defense_first)
                        for _ in range(20):
                            run.step({defender: (-32768, 0, 0)})
                            if arrow not in fight.rockets:
                                break
                        self.assertFalse(fight.rockets)
                        self.assertEqual(bubble.health, 700 if defense_first else 575)
                        self.assertEqual(fight.vitals[winston.body].total, 500)
                        reflected = defense_first and guid == arrow_deflection.GENJI
                        self.assertEqual(hanzo_pool.total, 75 if reflected else 200)
                        self.assertEqual(defender_pool.total, 400 if guid == sigma_grasp.SIGMA else 200)
                        self.assertEqual(arrow.source_entity, defender.body if reflected else hanzo.body)
                        if guid == sigma_grasp.SIGMA:
                            self.assertEqual(grasp.instance.find_var(0, 21312).value(), 125 if defense_first else 0)
                            for _ in range(160):
                                run.step({defender: (-32768, 0, 0)})
                                if not grasp.active:
                                    break
                            self.assertFalse(grasp.active)
                            self.assertAlmostEqual(defender_pool.bonus_shields, 75 if defense_first else 0, places=4)
                        if reflected and sonic:
                            beacon, = fight.sonars
                            self.assertEqual((beacon.source_entity, beacon.attached), (defender.body, hanzo.body))
                        else:
                            self.assertFalse(fight.sonars)

    def test_winston_barrier_expiry_does_not_clear_cooldown_or_refill_on_early_press(self):
        run, hanzo, winston, bubble = self.arena()
        service = run.match.combat.winston_barriers
        instance = next(i for i in winston.body_script.component.instances.values()
                        if i.graph.index == winston_barrier.GRAPH)
        ability = instance.states[2]
        self.draw(run, hanzo)
        self.assertEqual(bubble.health, 575)
        run.step({winston: (0, 0, 16)})
        run.step({})
        self.assertIs(service.actors[winston.body], bubble)
        self.assertEqual(bubble.health, 575)
        run.frame = math.ceil(bubble.expires)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(winston.body, service.actors)
        run.step({})
        self.assertGreater(ability.remaining(), 3.9)
        run.step({winston: (0, 0, 16)})
        run.step({})
        for _ in range(300):
            self.assertNotIn(winston.body, service.actors)
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertNotIn(winston.body, service.actors)
        run.step({winston: (0, 0, 16)})
        for _ in range(8):
            run.step({})
            if winston.body in service.actors:
                break
        fresh = service.actors[winston.body]
        self.assertIsNot(fresh, bubble)
        self.assertEqual(fresh.health, 700)

    def test_winston_death_cancels_pending_cast_but_deployed_sphere_lasts_until_respawn(self):
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                run, hanzo, winston, bubble = self.arena(deploy=deployed)
                fight = run.match.combat
                service = fight.winston_barriers
                body = winston.body
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
                pool.damage(pool.total - 1)
                hanzo.mover.position[2] = 7.5
                hanzo.mover.body = None
                for _ in range(70):
                    run.step({hanzo: (0, 0, 1)})
                with patch.object(combat, "respawn_seconds", return_value=120 * combat.TICK):
                    run.step({hanzo: (0, 0, 0), winston: (0, 0, 0 if deployed else 16)})
                    self.assertEqual(body in service.pending, not deployed)
                    run.step({})
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, hanzo.body)
                self.assertNotIn(body, service.pending)
                if deployed:
                    self.assertIs(service.actors[body], bubble)
                    self.assertEqual(bubble.health, 700)
                    hanzo.mover.position[2] = 0
                    hanzo.mover.body = None
                    for _ in range(20):
                        run.step({})
                    self.draw(run, hanzo)
                    self.assertEqual(bubble.health, 575)
                    self.assertEqual(winston.body, body)
                else:
                    for _ in range(8):
                        run.step({})
                    self.assertNotIn(body, service.actors)
                for _ in range(120):
                    if winston.body != body:
                        break
                    run.step({})
                self.assertNotEqual(winston.body, body)
                self.assertNotIn(body, service.pending)
                self.assertNotIn(body, service.actors)
                self.assertNotIn(body, service.seen)

    def test_flashbang_interrupts_pending_winston_cast_without_removing_deployed_sphere(self):
        def flash_scene():
            run, _, winston, _ = self.arena(deploy=False)
            match = run.match
            fight = match.combat
            guid = 0x02E0000000000042
            caster = match.add_player(3, "McCree", guid, 0, False)
            in_world(match, caster)
            run.players.append(caster)
            self.assertTrue(match.switch_hero(caster, heroes()[guid]))
            caster.team = 0
            caster.mover.position[:] = (0, 0, 5.5)
            caster.mover.body = None
            for _ in range(20):
                run.step({})
            run.step({caster: (*aim(fight._eye(caster), (0, 1, 9)), 16)})
            return run, winston

        probe, winston = flash_scene()
        for delay in range(1, 61):
            probe.step({})
            if probe.match.combat.is_stunned(winston):
                break
        self.assertTrue(probe.match.combat.is_stunned(winston))
        self.assertGreater(delay, 5)
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                run, winston = flash_scene()
                fight = run.match.combat
                service = fight.winston_barriers
                press = delay - (5 if deployed else 1)
                bubble = None
                for frame in range(1, delay + 2):
                    run.step({winston: (0, 0, 16 if frame == press else 0)})
                    if frame == press:
                        self.assertIn(winston.body, service.pending)
                    if deployed and frame == delay - 1:
                        bubble = service.actors[winston.body]
                    if fight.is_stunned(winston):
                        break
                self.assertEqual(frame, delay)
                self.assertTrue(fight.is_stunned(winston))
                self.assertEqual(fight.vitals[winston.body].total, 480)
                self.assertNotIn(winston.body, service.pending)
                self.assertIs(service.actors.get(winston.body), bubble)
                for _ in range(75):
                    run.step({})
                self.assertFalse(fight.is_stunned(winston))
                self.assertNotIn(winston.body, service.pending)
                self.assertIs(service.actors.get(winston.body), bubble)
                if deployed:
                    self.assertEqual(bubble.health, 700)
