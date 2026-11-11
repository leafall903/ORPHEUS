import os
import struct
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_matrix as matrix_tests
import test_game_hanzo_sonic as sonic_tests
from test_game_combat import Run, aim, in_world

from ow174.game import (
    arrow_deflection, barrier_mesh, bodytrace, combat, matrix, movelog, mover, reinhardt_barrier, sigma_grasp,
)
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.world import POOLS, SHIELDS_POOL


class HanzoSigmaGraspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            raise unittest.SkipTest("prepared build 104319 projectile tags required")
        cls.admitted = matrix.load_admitted_definitions(Path(tags))

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def encounter(self, attack, *, facing=0, active=True, wall=False, sustain=False,
                  paired=False, genji_at=None, allied_sigma=False, barrier_at=None, matrix_at=None,
                  tied_absorbers=False, reverse_absorbers=False, distance=8, sigma_at=0, grasp_in_flight=False,
                  draw_frames=75):
        names = ("Hanzo", "Sigma", "Second Hanzo") if paired else ("Hanzo", "Sigma")
        run = Run(names=names)
        hanzo, sigma, *partners = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        self.assertTrue(run.match.switch_hero(hanzo, heroes()[0x02E0000000000005]))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        for player, position in ((hanzo, [0, 0, distance]), (sigma, [0, 0, sigma_at])):
            player.mover.position = position
            player.mover.body = None
        if tied_absorbers:
            sigma.mover.position[2] = 6
        if allied_sigma:
            sigma.team = hanzo.team
            sigma.mover.position[2] = 6
        for partner in partners:
            self.assertTrue(run.match.switch_hero(partner, hanzo.hero))
            partner.team = hanzo.team
            partner.mover.position = [0.35, 0, 8]
            partner.mover.body = None
        genji = None
        if genji_at is not None:
            genji = run.match.add_player(3, "Genji", arrow_deflection.GENJI, 1 - hanzo.team, False)
            in_world(run.match, genji)
            run.players.append(genji)
            self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
            genji.team = 1 - hanzo.team
            genji.mover.position = [0, 0, genji_at]
            genji.mover.body = None
        fight = run.match.combat
        fight.matrix_definitions = self.admitted
        rein = None
        if barrier_at is not None:
            path = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
            if not path:
                self.skipTest("prepared build 104319 barrier mesh required")
            fight.barriers.geometry = barrier_mesh.load_geometry(Path(path))
            rein = run.match.add_player(3, "Reinhardt", reinhardt_barrier.HERO, sigma.team, False)
            in_world(run.match, rein)
            run.players.append(rein)
            self.assertTrue(run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO]))
            rein.team = sigma.team
            rein.mover.position = [0, 0, barrier_at]
            rein.mover.body = None
        mech = None
        if matrix_at is not None:
            mech = run.match.add_player(3, "D.Va", matrix.DVA, sigma.team, False)
            in_world(run.match, mech)
            run.players.append(mech)
            self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
            mech.team = sigma.team
            mech.mover.position = [0, 0, matrix_at]
            mech.mover.body = None
        if attack == "sonic":
            fight.configure_sonic(sonic_tests.profile())
        if attack == "dragon":
            fight.configure_dragonstrike(dragon_tests.prepared_profile())
            hanzo.body_script.ult_seconds = 0
            hanzo.body_script.set_ult_charge(1680)

        def step(attack_button=0, grasp_button=0, extra=None, deflect_button=0, matrix_button=0):
            angles = aim(fight._eye(hanzo), (0, 1.2, 0))
            commands = {hanzo: (*angles, attack_button), sigma: (facing, 0, grasp_button)}
            for partner in partners:
                angles = aim(fight._eye(partner), (0, 1.2, 0))
                commands[partner] = (*angles, attack_button)
            if genji is not None:
                commands[genji] = (0, 0, deflect_button)
            if rein is not None:
                commands[rein] = (0, 0, 2)
            if mech is not None:
                commands[mech] = (0, 0, matrix_button)
            if extra is not None:
                commands.update(extra)
            run.step(commands)

        for _ in range(75):
            step()
        if attack == "sonic":
            step(8)
            step()
        if attack in ("bow", "sonic"):
            for _ in range(draw_frames - 5):
                step(1)
            step(1, 8 if active and not grasp_in_flight else 0, deflect_button=16 if genji is not None else 0,
                 matrix_button=2)
            for _ in range(4):
                step(1, matrix_button=2)
            step(matrix_button=2)
        else:
            step(16 if attack == "storm" else 32, 8 if active else 0)
            step()
            for _ in range(100):
                step(1 if attack == "storm" else 0)
                if fight.rockets:
                    break
        self.assertEqual(len(fight.rockets), 1 + len(partners))
        arrow = fight.rockets[0]
        self.assertTrue(arrow.matrix_absorbable)
        lease = arrow.shot
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        state = instance.states.get(1)
        self.assertEqual(bool(state and state.active), active and not grasp_in_flight)
        if grasp_in_flight:
            step()
            self.assertTrue(arrow in fight.rockets)
            step(grasp_button=8)
            self.assertTrue(instance.states[1].active)
            self.assertTrue(arrow in fight.rockets)
            self.assertEqual(instance.find_var(0, 21312).value(), 0)
        if genji is not None:
            target = next(t for t in fight._targets() if t.owner is genji)
            self.assertIsNotNone(arrow_deflection.active_volume(target, 0x03A3, 10))
        if rein is not None:
            self.assertTrue(fight.barriers.states[rein.body].active)
        if wall:
            fight.match.collision_world = World([
                (-5, -1, 5, 5, -1, 5, 5, 5, 5),
                (-5, -1, 5, 5, 5, 5, -5, 5, 5),
                (5, 5, 5, 5, -1, 5, -5, -1, 5),
                (-5, 5, 5, 5, 5, 5, -5, -1, 5),
            ])
        if mech is not None:
            target = next(t for t in fight._targets() if t.owner is mech)
            matrix_contact = matrix.trace(target, arrow.position, arrow.direction, 32)
            self.assertIsNotNone(matrix_contact)
            target = next(t for t in fight._targets() if t.owner is sigma)
            grasp_contact = fight.grasp.trace(target, arrow.position, arrow.direction, 32)
            self.assertIsNotNone(grasp_contact)
            if tied_absorbers:
                self.assertEqual((matrix_contact, grasp_contact[0]), (0, 0))
                if reverse_absorbers:
                    # Only the target iteration order changes after launch.
                    fight.match.players[1:] = [mech, sigma]
                owners = [target.owner for target in fight._enemies(hanzo)]
                self.assertEqual(owners, [mech, sigma] if reverse_absorbers else [sigma, mech])
            else:
                self.assertEqual(matrix_contact < grasp_contact[0], matrix_at > 0)
        for _ in range(42):
            step(1 if sustain else 0, matrix_button=2)
        return fight, hanzo, sigma, arrow, lease, instance, step, run

    def test_grasp_consumes_actual_arrows_and_converts_at_end(self):
        for attack, amount in (("bow", 125), ("sonic", 125), ("storm", 70), ("dragon", 125)):
            with self.subTest(attack=attack):
                fight, hanzo, sigma, arrow, lease, instance, step, run = self.encounter(attack)
                vitals = fight.vitals[sigma.body]
                self.assertEqual(vitals.total, 400)
                self.assertEqual(instance.find_var(0, 21312).value(), amount)
                self.assertEqual(vitals.bonus_shields, 0)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.dragons)
                self.assertIsNone(arrow.shot)
                if lease is not None:
                    self.assertNotIn(lease, fight.projectiles.shots)
                for _ in range(160):
                    step()
                    if not instance.states[1].active:
                        break
                self.assertFalse(instance.states[1].active)
                step()
                self.assertAlmostEqual(vitals.bonus_shields, amount * .6, places=4)

    def test_grasp_activated_in_flight_absorbs_and_converts_actual_bow_and_sonic(self):
        for attack, draw_frames in (("bow", 75), ("sonic", 75), ("bow", 5), ("sonic", 5)):
            with self.subTest(attack=attack, draw_frames=draw_frames):
                fight, hanzo, sigma, arrow, lease, instance, step, _ = self.encounter(
                    attack, distance=12 if draw_frames == 5 else 24, grasp_in_flight=True, draw_frames=draw_frames,
                )
                vitals = fight.vitals[sigma.body]
                self.assertEqual(vitals.total, 400)
                amount = 25 + 100 * 1.375 * 0.079 if draw_frames == 5 else 125
                self.assertAlmostEqual(instance.find_var(0, 21312).value(), amount, delta=0.12)
                self.assertEqual(vitals.bonus_shields, 0)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.revealed_to(hanzo, sigma))
                self.assertFalse(arrow in fight.rockets)
                self.assertIsNone(arrow.shot)
                self.assertIsNotNone(lease)
                self.assertNotIn(lease, fight.projectiles.shots)
                if draw_frames == 5:
                    definition = 0x020D if attack == "sonic" else 0x020B
                    self.assertEqual(lease.definition, 0x0400000000000000 | definition)
                for _ in range(160):
                    if not instance.states[1].active:
                        break
                    step()
                self.assertFalse(instance.states[1].active)
                step()
                self.assertAlmostEqual(vitals.bonus_shields, amount * 0.6, delta=0.12)

    def test_rear_and_inactive_grasp_allow_bow_damage(self):
        for facing, active in ((-32768, True), (0, False)):
            with self.subTest(facing=facing, active=active):
                fight, hanzo, sigma, _, _, _, _, _ = self.encounter("bow", facing=facing, active=active)
                self.assertEqual(fight.vitals[sigma.body].total, 275)
                self.assertEqual(len(hanzo.client.messages(combat.HIT)), 1)


    def test_nearer_wall_prevents_grasp_accumulation(self):
        fight, hanzo, sigma, arrow, _, instance, _, _ = self.encounter("bow", wall=True)
        self.assertAlmostEqual(arrow.position[2], 5)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertEqual(instance.find_var(0, 21312).value(), 0)
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_grasp_waits_then_decays_only_the_temporary_pool(self):
        fight, _, sigma, _, _, instance, step, run = self.encounter("bow")
        vitals = fight.vitals[sigma.body]
        for _ in range(160):
            step()
            if vitals.bonus_shields:
                break
        self.assertAlmostEqual(vitals.bonus_shields, 75, places=4)
        extra = vitals.component((SHIELDS_POOL,))[51][SHIELDS_POOL * POOLS + 1]
        self.assertEqual(struct.unpack("<IffI", extra[:16]), (4, 75, 75, SHIELDS_POOL))
        self.assertEqual(extra[17], 0)
        decay = instance.states[28]
        for _ in range(200):
            if decay.active:
                break
            self.assertAlmostEqual(vitals.bonus_shields, 75, places=4)
            step()
        self.assertTrue(decay.active)
        step()
        before = vitals.bonus_shields
        for _ in range(60):
            step()
        self.assertAlmostEqual(before - vitals.bonus_shields, 4.8, places=3)
        self.assertEqual(sum(vitals.now.values()), 400)
        once = vitals.bonus_shields
        fight.grasp.update()
        self.assertEqual(vitals.bonus_shields, once)

    def test_rear_flashbang_interrupts_grasp_without_later_conversion(self):
        fight, hanzo, sigma, _, _, instance, step, run = self.encounter("bow")
        self.assertEqual(instance.find_var(0, 21312).value(), 125)
        caster, angles = self.cast_rear_flashbang(fight, hanzo, step, run)
        for _ in range(50):
            if fight.is_stunned(sigma):
                break
            step(extra={caster: (*angles, 0)})
        self.assertTrue(fight.is_stunned(sigma))
        self.assertFalse(instance.states[1].active)
        for _ in range(160):
            step()
        self.assertEqual(fight.vitals[sigma.body].bonus_shields, 0)

    def cast_rear_flashbang(self, fight, hanzo, step, run):
        match = fight.match
        guid = 0x02E0000000000042
        caster = match.add_player(3, "McCree", guid, hanzo.team, False)
        in_world(match, caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, heroes()[guid]))
        caster.team = hanzo.team
        caster.mover.position[:] = [0, 0, -3]
        caster.mover.body = None
        angles = aim(fight._eye(caster), (0, 1, 0))
        for _ in range(4):
            step(extra={caster: (*angles, 0)})
        step(extra={caster: (*angles, 16)})
        return caster, angles

    def test_grasp_cannot_convert_after_lethal_flashbang_or_hero_selection(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                fight, hanzo, sigma, _, _, instance, step, run = self.encounter("bow")
                self.assertTrue(instance.states[1].active)
                self.assertEqual(instance.find_var(0, 21312).value(), 125)
                old = sigma.body
                vitals = fight.vitals[old]
                if replacement:
                    self.assertTrue(fight.match.switch_hero(sigma, sigma.hero))
                else:
                    # Seed 1 HP so a real rear Flashbang is lethal.
                    vitals.damage(399)
                    self.assertEqual(vitals.total, 1)
                    self.cast_rear_flashbang(fight, hanzo, step, run)
                    for _ in range(50):
                        step()
                        if vitals.dead:
                            break
                    self.assertTrue(vitals.dead)
                for _ in range(160):
                    step()
                self.assertEqual(vitals.bonus_shields, 0)
                self.assertEqual(fight.vitals[sigma.body].bonus_shields, 0)
                self.assertFalse(any(body == old for body, _ in fight.grasp.conversions))
                if not replacement:
                    limit = round(combat.respawn_seconds(fight.match) / combat.TICK) + 120
                    for _ in range(limit):
                        if sigma.body != old:
                            break
                        step()
                self.assertNotEqual(sigma.body, old)
                self.assertNotIn(old, fight.vitals)
                target = next(t for t in fight._targets() if t.owner is sigma)
                fresh = fight.vitals_of(target)
                self.assertEqual(fresh.total, 400)
                self.assertEqual(fresh.bonus_shields, 0)
                self.assertFalse(fresh.dead)


    def test_actual_storm_arrows_accumulate_and_respect_grasp_cap(self):
        for paired, absorbed, shields in ((False, 350, 210), (True, 700, 400)):
            with self.subTest(paired=paired):
                fight, _, sigma, _, _, instance, step, run = self.encounter(
                    "storm", sustain=True, paired=paired,
                )
                vitals = fight.vitals[sigma.body]
                for _ in range(160):
                    if not instance.states[1].active:
                        break
                    step(1 if instance.find_var(0, 21312).value() < absorbed else 0)
                self.assertFalse(instance.states[1].active)
                step()
                self.assertEqual(instance.find_var(0, 21312).value(), absorbed)
                self.assertAlmostEqual(vitals.bonus_shields, shields, places=4)
                self.assertEqual(sum(vitals.now.values()), 400)
                for player in run.players:
                    self.assertFalse(player.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)


    def test_nearest_grasp_or_deflect_wins_for_actual_bow(self):
        for genji_at, reflected in ((5, True), (-2, False)):
            with self.subTest(genji_at=genji_at):
                fight, hanzo, sigma, arrow, lease, instance, _, run = self.encounter(
                    "bow", genji_at=genji_at,
                )
                genji = run.players[2]
                self.assertEqual(fight.vitals[sigma.body].total, 400)
                target = next(t for t in fight._targets() if t.owner is genji)
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertEqual(instance.find_var(0, 21312).value(), 0 if reflected else 125)
                self.assertIs(arrow.shooter, genji if reflected else hanzo)
                target = next(t for t in fight._targets() if t.owner is hanzo)
                pool = fight.vitals_of(target)
                self.assertEqual(pool.total, 0 if reflected else 200)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(reflected))
                if reflected:
                    self.assertEqual(pool.killer, genji.body)
                    self.assertEqual(hits[0]["+0x78"]["+0x4"], 200)
                    self.assertEqual(hits[0]["+0x78"]["+0x8"], 3)  # critical and lethal
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.assertNotIn(lease, fight.projectiles.shots)


    def test_allied_grasp_accepts_bow_only_after_enemy_deflect(self):
        fight, hanzo, sigma, arrow, lease, instance, _, run = self.encounter(
            "bow", facing=-32768, genji_at=3, allied_sigma=True,
        )
        genji = run.players[2]
        self.assertIs(arrow.shooter, genji)
        self.assertEqual(arrow.source_entity, genji.body)
        self.assertEqual(instance.find_var(0, 21312).value(), 125)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        for player in run.players:
            self.assertFalse(player.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)
        self.assertNotIn(lease, fight.projectiles.shots)


    def test_nearest_grasp_or_barrier_wins_for_actual_bow_and_sonic(self):
        for attack in ("bow", "sonic"):
            for barrier_at, barrier_first in ((5, True), (-2, False)):
                with self.subTest(attack=attack, barrier_at=barrier_at):
                    fight, hanzo, sigma, arrow, lease, instance, _, run = self.encounter(
                        attack, barrier_at=barrier_at,
                    )
                    rein = run.players[2]
                    self.assertEqual(fight.vitals[sigma.body].total, 400)
                    target = next(t for t in fight._targets() if t.owner is rein)
                    self.assertEqual(fight.vitals_of(target).total, 500)
                    self.assertEqual(instance.find_var(0, 21312).value(), 0 if barrier_first else 125)
                    self.assertEqual(fight.barriers.states[rein.body].current, 1475 if barrier_first else 1600)
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertFalse(hanzo.client.messages(combat.HIT))
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.sonars)
                    self.assertNotIn(lease, fight.projectiles.shots)


    def test_grasp_and_matrix_order_for_actual_bow_and_sonic(self):
        graph_path = os.environ.get("ORPHEUS_MATRIX_GRAPHS")
        mover_path = os.environ.get("ORPHEUS_MATRIX_MOVERS")
        additive = graph.GRAPH_SUPPLEMENT_PATH is not None and mover.MOVERS_SUPPLEMENT_PATH is not None
        if not additive:
            if not graph_path or not mover_path:
                self.skipTest("prepared build 104319 0407 graphs and movers required")
            self.enterContext(patch.object(graph, "GRAPHS_PATH", Path(graph_path)))
            self.enterContext(patch.object(mover, "MOVERS_PATH", Path(mover_path)))
        self.addCleanup(matrix_tests.HanzoMatrixTests.reset_catalogs)
        matrix_tests.HanzoMatrixTests.reset_catalogs()
        cases = ((4, False, 0), (-15, False, 125), (6, False, 125), (6, True, 0))
        for attack in ("bow", "sonic"):
            for matrix_at, reverse, absorbed in cases:
                with self.subTest(attack=attack, matrix_at=matrix_at, reversed_order=reverse):
                    fight, hanzo, sigma, arrow, lease, instance, _, run = self.encounter(
                        attack, matrix_at=matrix_at, tied_absorbers=matrix_at == 6,
                        reverse_absorbers=reverse,
                    )
                    mech = run.players[2]
                    target = next(t for t in fight._targets() if t.owner is mech)
                    self.assertEqual(fight.vitals_of(target).total, 600)
                    self.assertEqual(fight.vitals[sigma.body].total, 400)
                    self.assertEqual(instance.find_var(0, 21312).value(), absorbed)
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertFalse(hanzo.client.messages(combat.HIT))
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.sonars)
                    self.assertNotIn(lease, fight.projectiles.shots)

    def test_actual_melee_bypasses_grasp_only_on_enemy_in_reach(self):
        for distance, allied, expected in ((1.8, False, 30), (4, False, 0), (1.8, True, 0)):
            with self.subTest(distance=distance, allied=allied):
                run = Run(names=("Hanzo", "Sigma"))
                hanzo, sigma = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.collision_world = flat_world(0)
                self.assertTrue(run.match.switch_hero(hanzo, heroes()[0x02E0000000000005]))
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
                sigma.team = hanzo.team if allied else 1 - hanzo.team
                for player, position in ((hanzo, [0, 0, distance]), (sigma, [0, 0, 0])):
                    player.mover.position = position
                    player.mover.body = None
                fight = run.match.combat

                def step(button=0, grasp=0):
                    angles = aim(fight._eye(hanzo), (0, 1.2, 0))
                    run.step({hanzo: (*angles, button), sigma: (0, 0, grasp)})

                for _ in range(75):
                    step()
                step(grasp=8)
                for _ in range(4):
                    step()
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == sigma_grasp.GRAPH)
                self.assertTrue(instance.states[1].active)
                step(0x800)
                for _ in range(12):
                    step()
                self.assertTrue(instance.states[1].active)
                self.assertEqual(instance.find_var(0, 21312).value(), 0)
                target = next(t for t in fight._targets() if t.owner is sigma)
                self.assertEqual(fight.vitals_of(target).total, 400 - expected)
                hits = hanzo.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [30] if expected else [])
                self.assertFalse(fight.rockets)


    def test_lethal_melee_prevents_conversion_of_an_already_absorbed_arrow(self):
        fight, hanzo, sigma, _, _, instance, step, run = self.encounter("bow", distance=1.8)
        old = sigma.body
        vitals = fight.vitals[old]
        self.assertTrue(instance.states[1].active)
        self.assertEqual(instance.find_var(0, 21312).value(), 125)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.bonus_shields, 0)
        vitals.damage(380)  # Seed a lethal quick melee after the absorption.
        step(0x800)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, hanzo.body)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hanzo.client.messages(combat.HIT)], [20])

        limit = round(combat.respawn_seconds(run.match) / combat.TICK) + 120
        for _ in range(limit):
            self.assertEqual(vitals.bonus_shields, 0)
            self.assertFalse(any(body == old for body, _ in fight.grasp.conversions))
            if sigma.body != old:
                break
            step()
        self.assertNotEqual(sigma.body, old)
        target = next(t for t in fight._targets() if t.owner is sigma)
        fresh = fight.vitals_of(target)
        self.assertEqual(fresh.total, 400)
        self.assertEqual(fresh.bonus_shields, 0)
        self.assertNotIn(old, fight.vitals)

    def test_actual_melee_spends_converted_shields_before_base_pools(self):
        fight, hanzo, sigma, _, _, instance, step, _ = self.encounter("bow", distance=1.8)
        vitals = fight.vitals[sigma.body]
        for _ in range(160):
            if vitals.bonus_shields:
                break
            step()
        self.assertFalse(instance.states[1].active)
        self.assertAlmostEqual(vitals.bonus_shields, 75, places=4)
        step(0x800)
        for _ in range(10):
            step()
        self.assertAlmostEqual(vitals.bonus_shields, 45, places=4)
        self.assertEqual(sum(vitals.now.values()), 400)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hanzo.client.messages(combat.HIT)], [30])


    def test_moving_dragonstrike_damages_through_actual_grasp(self):
        fight, hanzo, sigma, arrow, _, instance, step, _ = self.encounter(
            "dragon", active=False, sigma_at=-12,
        )
        flight, = fight.dragons
        vitals = fight.vitals[sigma.body]
        self.assertNotIn(sigma.body, arrow.hit_entities)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(flight.source_entity, hanzo.body)
        step(grasp_button=8)
        for _ in range(4):
            step()
        self.assertTrue(instance.states[1].active)
        for _ in range(80):
            if vitals.total < 400:
                break
            step()
        self.assertTrue(instance.states[1].active)
        self.assertLess(vitals.total, 400)
        self.assertIn(flight, fight.dragons)
        self.assertFalse(fight.rockets)
        self.assertEqual(instance.find_var(0, 21312).value(), 0)
        for _ in range(160):
            if not instance.states[1].active:
                break
            step()
        step()
        self.assertFalse(instance.states[1].active)
        self.assertEqual(vitals.bonus_shields, 0)
