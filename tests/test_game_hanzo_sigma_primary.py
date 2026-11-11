"""Actual Sigma Hyperspheres versus Hanzo and hosted defenses."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo_matrix as matrix_tests
import test_game_hanzo_sigma_accretion as accretion_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import (
    arrow_deflection,
    combat,
    matrix,
    mover,
    sigma_barrier,
    sigma_grasp,
    sonic,
    zarya_barrier,
)
from ow174.game.collision import World
from ow174.game.commands import JUMP
from ow174.game.content import heroes
from ow174.game.script import graph, runtime
from ow174.game.server_snapshot import snapshot_digest


class HanzoHypersphereTests(unittest.TestCase):
    def pair(self, *, feet, wall=None, allied=False, aimed=False, skipped=False):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=feet, wall_z=wall, allied=allied)
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        angles = aim(fight._eye(sigma), fight._eye(hanzo)) if aimed else (0, 0)
        seen = []
        bounced = []
        for frame in range(14):
            run.step({sigma: (*angles, 1 if frame == 0 else 0)})
            for rocket in fight.rockets:
                if rocket not in seen:
                    seen.append(rocket)
                    self.assertIsNotNone(rocket.hypersphere)
                if rocket.direction[2] < 0 and rocket not in bounced:
                    bounced.append(rocket)
                    self.assertEqual(rocket.hypersphere.bounces_left, 99)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(r.source_entity == sigma.body and r.last_tick is not None for r in seen))
        for delta in ((70,) if skipped else (1,) * 70):
            run.frame += delta
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.is_stunned(hanzo))
        hits = [m["+0x78"]["+0x4"] for m in sigma.client.messages(combat.HIT)]
        return pool.total, hits, seen, bounced

    def test_authored_pair_hits_body_or_radius_edge(self):
        for feet, aimed in (((0, 0, 6), True), ((0.68, 0, 6), False)):
            with self.subTest(feet=feet):
                health, hits, rockets, _ = self.pair(feet=feet, aimed=aimed)
                self.assertEqual((health, hits), (90, [25, 30, 25, 30]))
                for rock in rockets:
                    self.assertAlmostEqual(rock.hypersphere.body_radius, 0.2, places=6)
                    self.assertLess(rock.position[2], 6)

    def test_wall_reflects_pair_to_hanzo_and_continuous_or_skipped_ticks_agree(self):
        outcomes = []
        for skipped in (False, True):
            health, hits, rockets, bounced = self.pair(feet=(0, 0, -2), wall=6, skipped=skipped)
            self.assertEqual((health, hits), (90, [25, 30, 25, 30]))
            self.assertTrue(bounced)
            self.assertTrue(all(r.hypersphere.bounces_left == 99 and r.position[2] < 0 for r in rockets))
            outcomes.append([r.position for r in rockets])
        for first, second in zip(*outcomes, strict=True):
            for a, b in zip(first, second, strict=True):
                self.assertAlmostEqual(a, b, places=6)

    def test_airburst_hits_once_each_and_rejects_distant_or_allied_hanzo(self):
        for feet, allied, hp in (((1, 0, 22), False, 140), ((4, 0, 22), False, 200),
                                 ((1, 0, 22), True, 200)):
            with self.subTest(feet=feet, allied=allied):
                health, hits, rockets, bounced = self.pair(feet=feet, allied=allied)
                self.assertEqual(health, hp)
                self.assertEqual(hits, [30, 30] if hp == 140 else [])
                self.assertFalse(bounced)
                for rocket in rockets:
                    self.assertAlmostEqual(rocket.flown, 0.44, places=6)
                    self.assertAlmostEqual(rocket.position[2], 22, places=5)

    def test_static_cover_occludes_a_near_airburst_without_stopping_the_pair(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(1, 0, 22))
        fight = run.match.combat
        run.match.collision_world = World(box(-30, 30, -1, 0, -30, 30) + wall(0.5, 19, 0.5, 25))
        run.step({sigma: (0, 0, 1)})
        seen = []
        for _ in range(60):
            run.step({})
            for rocket in fight.rockets:
                if rocket not in seen:
                    seen.append(rocket)
        self.assertEqual(len(seen), 2)
        self.assertFalse(fight.rockets)
        for rocket in seen:
            self.assertAlmostEqual(rocket.flown, 0.44, places=6)
            self.assertEqual(rocket.hypersphere.bounces_left, 100)
        self.assertEqual(self.pool(fight, hanzo).total, 200)
        self.assertFalse(hanzo.mover.external_impulse)
        self.assertFalse(self.hit_amounts(sigma, hanzo))

    def test_projected_barrier_blocks_airburst_without_exposing_hanzo_when_it_breaks(self):
        for initial in (200, 25):
            with self.subTest(initial=initial):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(1.8, 0, 22))
                fight = run.match.combat
                ally = self.defender(run, hanzo, "Zarya", zarya_barrier.HERO, (4, 0, 22))
                pool = self.pool(fight, hanzo)
                cast = aim(fight._eye(ally), fight._eye(hanzo))
                run.step({ally: (*cast, 16), sigma: (0, 0, 1)})
                service = fight.zarya_barriers
                self.assertEqual(service.projected[ally.body][1:], (200, hanzo.body))
                if initial < 200:
                    service.damage(hanzo, 200 - initial, sigma.body)
                first, = fight.rockets
                for _ in range(45):
                    if first not in fight.rockets:
                        break
                    run.step({})
                self.assertNotIn(first, fight.rockets)
                self.assertAlmostEqual(first.flown, 0.44, places=6)
                self.assertEqual(pool.total, 200)
                self.assertFalse(hanzo.mover.external_impulse)
                second, = fight.rockets
                for _ in range(45):
                    run.step({})
                self.assertAlmostEqual(second.flown, 0.44, places=6)
                self.assertFalse(fight.rockets)
                if initial == 200:
                    self.assertEqual(service.projected[ally.body][1:], (140, hanzo.body))
                    self.assertEqual(pool.total, 200)
                    self.assertFalse(hanzo.mover.external_impulse)
                else:
                    self.assertNotIn(ally.body, service.projected)
                    self.assertAlmostEqual(pool.total, 173.403, places=3)
                self.assertEqual(self.hit_amounts(sigma, hanzo), [] if initial == 200 else [27])

    def test_airburst_applies_authored_inward_impulse_during_real_player_poses(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(1, 0, 22))
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        run.step({sigma: (0, 0, 1)})
        for _ in range(80):
            run.step({})
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 170)
        self.assertTrue(hanzo.mover.external_impulse)
        self.assertLess(hanzo.mover.velocity[0], 0)
        self.assertGreater(hanzo.mover.velocity[1], 0)
        self.assertFalse(fight.is_stunned(hanzo))
        for _ in range(60):
            run.step({})
        self.assertEqual(pool.total, 140)
        self.assertLess(hanzo.mover.position[0], 1)
        self.assertFalse(fight.rockets)

    def defender(self, run, hanzo, name, hero, position):
        match = run.match
        player = match.add_player(len(run.players) + 1, name, hero, hanzo.team, False)
        in_world(match, player)
        run.players.append(player)
        character = heroes()[hero]
        if hero == matrix.DVA:
            character = replace(character, body=matrix.MECH)
        self.assertTrue(match.switch_hero(player, character))
        player.team = hanzo.team
        player.mover.position[:] = position
        player.mover.body = None
        for _ in range(20):
            run.step({})
        return player

    def pool(self, fight, player):
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player))

    def hit_amounts(self, player, target=None):
        hits = (msg["+0x78"] for msg in player.client.messages(combat.HIT))
        return [hit["+0x4"] for hit in hits if target is None or hit["+0x0"]["+0x0"] == target.body]

    def admit_absorption(self, fight):
        fight.matrix_definitions = matrix.load_admitted_definitions(Path(os.environ["ORPHEUS_MATRIX_TAGS"]))

    def test_projected_barrier_keeps_each_sphere_atomic_and_preserves_the_bow(self):
        for initial in (200, 45):
            with self.subTest(initial=initial):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                fight = run.match.combat
                ally = self.defender(run, hanzo, "Zarya", zarya_barrier.HERO, (2, 0, 6))
                pool = self.pool(fight, hanzo)
                bow = aim(fight._eye(hanzo), fight._eye(sigma))
                forward = aim(fight._eye(sigma), fight._eye(hanzo))
                cast = aim(fight._eye(ally), fight._eye(hanzo))
                for _ in range(70):
                    run.step({hanzo: (*bow, 1)})
                run.step({hanzo: (*bow, 1), ally: (*cast, 16), sigma: (*forward, 1)})
                service = fight.zarya_barriers
                self.assertEqual(service.projected[ally.body][1:], (200, hanzo.body))
                if initial < 200:
                    service.damage(hanzo, 200 - initial, sigma.body)
                health = []
                for _ in range(40):
                    run.step({hanzo: (*bow, 1), sigma: (*forward, 0)})
                    health.append(pool.total)
                if initial == 200:
                    self.assertEqual(service.projected[ally.body][1:], (90, hanzo.body))
                    self.assertEqual(set(health), {200})
                    self.assertFalse(hanzo.mover.external_impulse)
                    self.assertEqual(self.hit_amounts(sigma, hanzo), [])
                else:
                    self.assertFalse(service.projected.get(ally.body))
                    self.assertEqual(set(health), {200, 145})
                    self.assertEqual(self.hit_amounts(sigma, hanzo), [25, 30])
                self.assertFalse(fight.is_stunned(hanzo))
                run.step({hanzo: (*bow, 0)})
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)

    def test_genji_reflects_both_spheres_before_they_reach_hanzo(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        genji = self.defender(run, hanzo, "Genji", arrow_deflection.GENJI, (0, 0, 4))
        forward = aim(fight._eye(sigma), fight._eye(genji))
        reply = aim(fight._eye(genji), fight._eye(sigma))
        run.step({genji: (*reply, 16)})
        effect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(effect.states[10].active)
        reflected = []
        for frame in range(60):
            run.step({genji: (*reply, 0), sigma: (*forward, 1 if frame == 0 else 0)})
            for rocket in fight.rockets:
                if rocket.shooter is genji and rocket not in reflected:
                    reflected.append(rocket)
                    self.assertEqual(rocket.source_entity, genji.body)
        self.assertEqual(len(reflected), 2)
        self.assertEqual(self.pool(fight, sigma).total, 290)
        self.assertEqual(self.pool(fight, genji).total, 200)
        self.assertEqual(self.pool(fight, hanzo).total, 200)
        self.assertEqual(self.hit_amounts(genji), [25, 30, 25, 30])
        self.assertFalse(self.hit_amounts(sigma))

    @unittest.skipUnless(os.environ.get("ORPHEUS_MATRIX_TAGS"), "prepared projectile tags required")
    def test_grasp_absorbs_both_spheres_without_a_later_airburst(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        self.admit_absorption(fight)
        guard = self.defender(run, hanzo, "Guard", sigma_grasp.SIGMA, (0, 0, 4))
        forward = aim(fight._eye(sigma), fight._eye(guard))
        reply = aim(fight._eye(guard), fight._eye(sigma))
        run.step({guard: (*reply, 8)})
        grasp = next(
            i for i in guard.body_script.component.instances.values() if i.graph.index == sigma_grasp.GRAPH
        )
        for frame in range(60):
            run.step({guard: (*reply, 0), sigma: (*forward, 1 if frame == 0 else 0)})
        self.assertEqual(grasp.vars[21312].value(), 110)
        self.assertEqual(self.pool(fight, guard).total, 400)
        self.assertEqual(self.pool(fight, hanzo).total, 200)
        self.assertFalse(self.hit_amounts(sigma))
        self.assertFalse(fight.rockets)

    @unittest.skipUnless(
        all(os.environ.get("ORPHEUS_MATRIX_" + part) for part in ("GRAPHS", "MOVERS", "TAGS")),
        "prepared 0407 graphs, movers and projectile tags required",
    )
    def test_matrix_consumes_both_spheres_before_they_reach_hanzo(self):
        self.load_matrix_assets()
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        self.admit_absorption(fight)
        mech = self.defender(run, hanzo, "D.Va", matrix.DVA, (0, 0, 4))
        forward = aim(fight._eye(sigma), fight._eye(mech))
        reply = aim(fight._eye(mech), fight._eye(sigma))
        for _ in range(55):
            run.step({mech: (*reply, 0)})
        run.step({mech: (*reply, 2)})
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(instance.states[3].active)
        launched = []
        for frame in range(60):
            run.step({mech: (*reply, 2), sigma: (*forward, 1 if frame == 0 else 0)})
            for rocket in fight.rockets:
                if rocket not in launched:
                    launched.append(rocket)
                    self.assertTrue(rocket.matrix_absorbable)
        self.assertEqual(len(launched), 2)
        self.assertEqual(self.pool(fight, mech).total, 600)
        self.assertEqual(self.pool(fight, hanzo).total, 200)
        self.assertFalse(self.hit_amounts(sigma))
        self.assertFalse(fight.rockets)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "prepared Sigma C015 required")
    def test_experimental_barrier_stops_a_sphere_even_when_it_breaks(self):
        for initial in (700, 45):
            with self.subTest(initial=initial):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                fight = run.match.combat
                ally = self.defender(run, hanzo, "Guard", sigma_barrier.HERO, (0, 0, 9))
                actor = next(a for a in fight.sigma_barriers.actors.values() if a.owner is ally)
                for _ in range(16):
                    run.step({ally: (-32768, 0, 2)})
                for _ in range(3):
                    run.step({ally: (-32768, 0, 0)})
                self.assertTrue(actor.active())
                self.assertLess(actor.position[2], 6)
                actor.vitals.damage(700 - initial)
                forward = aim(fight._eye(sigma), fight._eye(hanzo))
                health = []
                pool = self.pool(fight, hanzo)
                for frame in range(40):
                    run.step({sigma: (*forward, 1 if frame == 0 else 0)})
                    health.append(pool.total)
                self.assertEqual(set(health), {200} if initial == 700 else {200, 145})
                self.assertEqual(actor.vitals.total, 590 if initial == 700 else 0)
                self.assertEqual(actor.active(), initial == 700)
                self.assertEqual(self.hit_amounts(sigma, hanzo), [] if initial == 700 else [25, 30])
                self.assertFalse(fight.rockets)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL") and os.environ.get("ORPHEUS_MATRIX_TAGS"),
        "prepared Sigma C015 and projectile tags required",
    )
    def test_experimental_barrier_break_or_recall_routes_second_sphere_to_grasp(self):
        for defense in ("intact", "break", "recall"):
            with self.subTest(defense=defense):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(0, 0, 9))
                fight = run.match.combat
                self.admit_absorption(fight)
                guard = self.defender(run, hanzo, "Guard", sigma_barrier.HERO, (0, 0, 7))
                actor = next(a for a in fight.sigma_barriers.actors.values() if a.owner is guard)
                reply = aim(fight._eye(guard), fight._eye(sigma))
                forward = aim(fight._eye(sigma), fight._eye(guard))
                for _ in range(16):
                    run.step({guard: (*reply, 2)})
                for _ in range(3):
                    run.step({guard: (*reply, 0)})
                self.assertTrue(actor.active())
                self.assertLess(actor.position[2], 4)
                for _ in range(20):
                    run.step({guard: (*reply, 0)})
                if defense == "break":
                    actor.vitals.damage(655)
                run.step({guard: (*reply, 8)})
                grasp = next(
                    i for i in guard.body_script.component.instances.values()
                    if i.graph.index == sigma_grasp.GRAPH
                )
                self.assertTrue(grasp.states[sigma_grasp.STATE].active)
                run.step({guard: (*reply, 0), sigma: (*forward, 1)})
                first, = fight.rockets
                fired_at = run.frame
                for _ in range(10):
                    if first not in fight.rockets:
                        break
                    run.step({guard: (*reply, 0), sigma: (*forward, 0)})
                self.assertFalse(fight.rockets)
                self.assertEqual(actor.vitals.total, 0 if defense == "break" else 645)
                self.assertEqual(grasp.vars[21312].value(), 0)
                if defense == "recall":
                    run.step({guard: (*reply, 2), sigma: (*forward, 0)})
                    run.step({guard: (*reply, 0), sigma: (*forward, 0)})
                    self.assertLess(run.frame - fired_at, 12)
                    self.assertFalse(actor.active())
                    self.assertTrue(grasp.states[sigma_grasp.STATE].active)
                for _ in range(45):
                    run.step({guard: (*reply, 0), sigma: (*forward, 0)})
                self.assertEqual(actor.vitals.total, {"intact": 590, "break": 0, "recall": 645}[defense])
                self.assertEqual(grasp.vars[21312].value(), 0 if defense == "intact" else 55)
                self.assertEqual(self.pool(fight, guard).total, 400)
                self.assertEqual(self.pool(fight, hanzo).total, 200)
                self.assertFalse(fight.rockets)
                self.assertFalse(self.hit_amounts(sigma))

    @unittest.skipUnless(os.environ.get("ORPHEUS_MATRIX_TAGS"), "prepared projectile tags required")
    def test_nearer_genji_or_grasp_wins_for_both_spheres(self):
        for genji_near in (False, True):
            with self.subTest(genji_near=genji_near):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(0, 0, 12))
                fight = run.match.combat
                self.admit_absorption(fight)
                genji_z, guard_z = (4, 8) if genji_near else (8, 4)
                genji = self.defender(run, hanzo, "Genji", arrow_deflection.GENJI, (0, 0, genji_z))
                guard = self.defender(run, hanzo, "Guard", sigma_grasp.SIGMA, (0, 0, guard_z))
                genji_aim = aim(fight._eye(genji), fight._eye(sigma))
                guard_aim = aim(fight._eye(guard), fight._eye(sigma))
                forward = aim(fight._eye(sigma), fight._eye(hanzo))
                run.step({genji: (*genji_aim, 16), guard: (*guard_aim, 8)})
                deflect = next(
                    i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3
                )
                grasp = next(
                    i for i in guard.body_script.component.instances.values()
                    if i.graph.index == sigma_grasp.GRAPH
                )
                self.assertTrue(deflect.states[10].active)
                self.assertTrue(grasp.states[sigma_grasp.STATE].active)
                for frame in range(60):
                    run.step({
                        genji: (*genji_aim, 0), guard: (*guard_aim, 0),
                        sigma: (*forward, 1 if frame == 0 else 0),
                    })
                self.assertEqual(grasp.vars[21312].value(), 0 if genji_near else 110)
                self.assertEqual(self.pool(fight, sigma).total, 290 if genji_near else 400)
                self.assertEqual(self.pool(fight, genji).total, 200)
                self.assertEqual(self.pool(fight, guard).total, 400)
                self.assertEqual(self.pool(fight, hanzo).total, 200)
                self.assertEqual(self.hit_amounts(genji, sigma), [25, 30, 25, 30] if genji_near else [])
                self.assertFalse(self.hit_amounts(sigma))
                self.assertFalse(fight.rockets)

    def load_matrix_assets(self):
        # The standalone fixture supplies complete tables of its own.
        self.enterContext(patch.object(graph, "GRAPH_SUPPLEMENT_PATH", None))
        self.enterContext(patch.object(mover, "MOVERS_SUPPLEMENT_PATH", None))
        self.enterContext(patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_MATRIX_GRAPHS"])))
        self.enterContext(patch.object(mover, "MOVERS_PATH", Path(os.environ["ORPHEUS_MATRIX_MOVERS"])))
        self.addCleanup(matrix_tests.HanzoMatrixTests.reset_catalogs)
        matrix_tests.HanzoMatrixTests.reset_catalogs()

    @unittest.skipUnless(
        all(os.environ.get("ORPHEUS_MATRIX_" + part) for part in ("GRAPHS", "MOVERS", "TAGS")),
        "prepared 0407 graphs, movers and projectile tags required",
    )
    def test_first_absorber_wins_between_matrix_and_grasp(self):
        self.load_matrix_assets()
        for matrix_near in (False, True):
            with self.subTest(matrix_near=matrix_near):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(0, 0, 19))
                fight = run.match.combat
                self.admit_absorption(fight)
                guard = self.defender(run, hanzo, "Guard", sigma_grasp.SIGMA, (0, 0, 4))
                mech = self.defender(run, hanzo, "D.Va", matrix.DVA, (0, 0, 2 if matrix_near else 16))
                guard_aim = aim(fight._eye(guard), fight._eye(sigma))
                mech_aim = aim(fight._eye(mech), fight._eye(sigma))
                forward = aim(fight._eye(sigma), fight._eye(guard))
                for _ in range(55):
                    run.step({mech: (*mech_aim, 0)})
                run.step({mech: (*mech_aim, 2), guard: (*guard_aim, 8)})
                matrix_instance = next(
                    i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC
                )
                grasp = next(
                    i for i in guard.body_script.component.instances.values()
                    if i.graph.index == sigma_grasp.GRAPH
                )
                self.assertTrue(matrix_instance.states[3].active)
                self.assertTrue(grasp.states[sigma_grasp.STATE].active)
                for frame in range(60):
                    run.step({
                        mech: (*mech_aim, 2), guard: (*guard_aim, 0),
                        sigma: (*forward, 1 if frame == 0 else 0),
                    })
                self.assertEqual(grasp.vars[21312].value(), 0 if matrix_near else 110)
                self.assertEqual(self.pool(fight, sigma).total, 400)
                self.assertEqual(self.pool(fight, mech).total, 600)
                self.assertEqual(self.pool(fight, guard).total, 400)
                self.assertEqual(self.pool(fight, hanzo).total, 200)
                self.assertFalse(self.hit_amounts(sigma))
                self.assertFalse(fight.rockets)

    def test_pending_sphere_motion_is_causal_snapshot_state(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner = players[1]
        peers[1].message(21616, snapshot_tests.pick(owner.entity, sigma_grasp.SIGMA), now=100.8)
        peers[1].commands(owner.body_script.frame + 1, 20, 0)
        peers[1].commands(owner.body_script.frame + 1, 1, 1)
        rocket, = owner.match.combat.rockets
        motion = rocket.hypersphere
        self.assertEqual(motion.bounces_left, 100)
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran VM")):
            before = snapshot_digest(fixture.capture(session))
            rocket.hypersphere = replace(motion, bounces_left=99)
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
            rocket.hypersphere = motion
            self.assertEqual(snapshot_digest(fixture.capture(session)), before)

    def test_spheres_preserve_held_sonic_and_its_release_starts_cooldown(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        fight.configure_sonic(accretion_tests.profile())
        selected = hanzo.body_script.component.entity_var(627)
        ability = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x00C1)
        x, y, z = sigma.mover.position
        reply = aim(fight._eye(hanzo), (x, y + 1, z))
        forward = aim(fight._eye(sigma), fight._eye(hanzo))
        run.step({hanzo: (*reply, 8)})
        run.step({})
        for _ in range(70):
            run.step({hanzo: (*reply, 1)})
        run.step({hanzo: (*reply, 1), sigma: (*forward, 1)})
        for _ in range(40):
            run.step({hanzo: (*reply, 1), sigma: (*forward, 0)})
        self.assertEqual(self.pool(fight, hanzo).total, 90)
        self.assertFalse(fight.is_stunned(hanzo))
        self.assertEqual(selected.value(), 1)
        self.assertEqual(ability.vars[61].value(), 0)
        self.assertFalse(fight.sonars)
        x, y, z = sigma.mover.position
        reply = aim(fight._eye(hanzo), (x, y + 1, z))
        run.step({hanzo: (*reply, 0)})
        arrow, = fight.rockets
        self.assertIn(arrow.shot.definition, sonic.DEFINITIONS)
        self.assertEqual(arrow.direct.amount, 125)
        self.assertGreater(ability.vars[61].value(), 11.9)
        for _ in range(20):
            run.step({})
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (hanzo.body, sigma.body))
        self.assertEqual(self.pool(fight, sigma).total, 275)

    def test_spheres_hit_during_storm_without_canceling_the_rest_of_the_volley(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        storm = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x146A)
        pool = self.pool(fight, hanzo)
        x, y, z = sigma.mover.position
        reply = aim(fight._eye(hanzo), (x, y + 1, z))
        run.step({hanzo: (*reply, 16)})
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(40):
                run.step({hanzo: (*reply, 1)})
                if fired.called:
                    break
            self.assertTrue(fired.called)
            self.assertGreater(storm.find_var(0, 215).value(), 0)
            forward = aim(fight._eye(sigma), fight._eye(hanzo))
            remaining_at_contact = None
            cooldown_at_start = None
            for frame in range(110):
                x, y, z = sigma.mover.position
                reply = aim(fight._eye(hanzo), (x, y + 1, z))
                run.step({hanzo: (*reply, 1), sigma: (*forward, 1 if frame == 0 else 0)})
                if pool.total < 200 and remaining_at_contact is None:
                    remaining_at_contact = storm.find_var(0, 215).value()
                remaining_cooldown = storm.find_var(0, 61).value()
                if cooldown_at_start is None and remaining_cooldown > 0:
                    cooldown_at_start = remaining_cooldown
                if not storm.states[0].flags & 1 and not fight.rockets:
                    break
        self.assertEqual(pool.total, 90)
        self.assertGreater(remaining_at_contact, 0)
        self.assertEqual(storm.find_var(0, 215).value(), 0)
        self.assertIsNotNone(cooldown_at_start)
        self.assertGreater(cooldown_at_start, 9.95)
        self.assertEqual([call.args[3] for call in fired.call_args_list if call.args[0] is hanzo],
                         [0x040000000000270F] * 5)
        self.assertEqual(self.pool(fight, sigma).total, 50)
        self.assertFalse(fight.is_stunned(hanzo))

    def test_sphere_hits_hanzo_during_an_active_wall_climb_without_stunning(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(wall_z=6.7)
        fight = run.match.combat
        pool = self.pool(fight, hanzo)
        climb = hanzo.body_script.world.hanzo_climb
        jump = {hanzo: (0, 0, JUMP)}
        for _ in range(3):
            run.step(jump)
        self.assertTrue(climb.active)
        x, y, z = hanzo.mover.position
        forward = aim(fight._eye(sigma), (x, y + 1.4, z))
        run.step({**jump, sigma: (*forward, 1)})
        active_at_contact = []
        damage = fight._damage

        def observe(shooter, target, *args, **kwargs):
            if target.owner is hanzo:
                active_at_contact.append(climb.active)
            return damage(shooter, target, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=observe):
            for _ in range(45):
                run.step(jump)
                if pool.total < 200:
                    break
        self.assertEqual(pool.total, 145)
        self.assertEqual(active_at_contact, [True, True])
        self.assertFalse(fight.is_stunned(hanzo))
        self.assertTrue(hanzo.mover.external_impulse)
        self.assertLess(hanzo.mover.position[2], 6.7)

    def test_side_lunge_evades_the_pair_after_the_first_sphere_launches(self):
        for lunge in (False, True):
            with self.subTest(lunge=lunge):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(feet=(0, 0, 12))
                fight = run.match.combat
                run.step({hanzo: (16384, 0, JUMP)})
                for _ in range(13):
                    run.step({})
                forward = aim(fight._eye(sigma), (0, 2.3, 12))
                run.step({sigma: (*forward, 1)})
                first, = fight.rockets
                run.step({hanzo: (16384, 0, JUMP if lunge else 0), sigma: (*forward, 0)})
                self.assertIn(first, fight.rockets)
                movement = next(
                    i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x010C
                )
                for _ in range(65):
                    run.step({sigma: (*forward, 0)})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.pool(fight, hanzo).total, 200 if lunge else 90)
                self.assertFalse(fight.is_stunned(hanzo))
                self.assertEqual(self.hit_amounts(sigma, hanzo), [] if lunge else [25, 30, 25, 30])
                if lunge:
                    self.assertGreater(hanzo.mover.position[0], 2)
                    self.assertGreater(movement.vars[61].value(), 3.8)

    def test_nonlethal_spheres_during_paid_dragonstrike_do_not_cancel_its_launch(self):
        fixture = accretion_tests.HanzoAccretionTests()
        run, hanzo, sigma = fixture.arena()
        fight = run.match.combat
        target = fixture.downrange_soldier(run, sigma.team)
        fight.configure_dragonstrike(accretion_tests.prepared_profile())
        script = hanzo.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)
        forward = aim(fight._eye(sigma), fight._eye(hanzo))
        run.step({hanzo: (0, 0, 32), sigma: (*forward, 1)})
        self.assertLess(script.component.entity_var(342).value(), 1)
        damage_tick = launch_tick = None
        pool = self.pool(fight, hanzo)
        for _ in range(155):
            run.step({sigma: (*forward, 0)})
            if damage_tick is None and pool.total < 200:
                damage_tick = run.frame
            if launch_tick is None and any(r.continuation is not None for r in fight.rockets):
                launch_tick = run.frame
            if self.pool(fight, target).total < 200:
                break
        self.assertEqual(pool.total, 90)
        self.assertIsNotNone(damage_tick)
        self.assertIsNotNone(launch_tick)
        self.assertLess(damage_tick, launch_tick)
        self.assertLess(self.pool(fight, target).total, 200)
        self.assertEqual(fight.dragons[0].source_entity, hanzo.body)
        self.assertFalse(fight.is_stunned(hanzo))

    def test_lethal_sphere_during_paid_dragonstrike_retires_the_unlaunched_arrow(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        fight.configure_dragonstrike(accretion_tests.prepared_profile())
        pool = self.pool(fight, hanzo)
        pool.damage(180)
        script = hanzo.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)
        forward = aim(fight._eye(sigma), fight._eye(hanzo))
        run.step({hanzo: (0, 0, 32), sigma: (*forward, 1)})
        self.assertLess(script.component.entity_var(342).value(), 1)
        for _ in range(100):
            run.step({sigma: (*forward, 0)})
            self.assertFalse(any(r.continuation is not None for r in fight.rockets))
            self.assertFalse(fight.dragons)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, sigma.body)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_lethal_sphere_preserves_committed_dragonstrike_and_original_source(self):
        for transformed in (False, True):
            with self.subTest(transformed=transformed):
                fixture = accretion_tests.HanzoAccretionTests()
                run, hanzo, sigma = fixture.arena()
                fight = run.match.combat
                target = fixture.downrange_soldier(run, sigma.team)
                fight.configure_dragonstrike(accretion_tests.prepared_profile())
                victim = self.pool(fight, target)
                pool = self.pool(fight, hanzo)
                pool.damage(180)
                script = hanzo.body_script
                original_hanzo = hanzo.body
                script.ult_seconds = 0
                script.set_ult_charge(1680)
                run.step({hanzo: (0, 0, 32)})
                arrow = flight = None
                for _ in range(120):
                    run.step({})
                    if arrow is None:
                        arrow = next((r for r in fight.rockets if r.continuation is not None), None)
                    if fight.dragons:
                        flight, = fight.dragons
                    if flight is not None or (arrow is not None and not transformed):
                        break
                self.assertIsNotNone(arrow)
                self.assertEqual(flight is not None, transformed)
                forward = aim(fight._eye(sigma), fight._eye(hanzo))
                run.step({sigma: (*forward, 1)})
                for _ in range(30):
                    run.step({sigma: (*forward, 0)})
                    if pool.dead:
                        break
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, sigma.body)
                self.assertEqual(arrow in fight.rockets, not transformed)
                self.assertEqual(victim.total, 200)
                for _ in range(140):
                    run.step({})
                    if victim.total < 200:
                        break
                self.assertLess(victim.total, 200)
                active, = fight.dragons
                self.assertEqual(active.source_entity, original_hanzo)
                if transformed:
                    self.assertIs(active, flight)

    def test_hanzo_kill_retires_the_second_launch_and_first_sphere_keeps_credit(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        sigma_pool = self.pool(fight, sigma)
        sigma_pool.now[combat.HEALTH_POOL] = 20
        sigma_pool.now[combat.SHIELDS_POOL] = 0
        hanzo_pool = self.pool(fight, hanzo)
        hanzo_pool.now[combat.HEALTH_POOL] = 55
        original_sigma = sigma.body
        forward = aim(fight._eye(sigma), fight._eye(hanzo))
        reply = aim(fight._eye(hanzo), fight._eye(sigma))
        for _ in range(70):
            run.step({hanzo: (*reply, 1)})
        run.step({hanzo: (*reply, 1), sigma: (*forward, 1)})
        first, = fight.rockets
        self.assertEqual(first.source_entity, original_sigma)
        run.step({hanzo: (*reply, 0), sigma: (*forward, 0)})
        for _ in range(50):
            run.step({})
            self.assertTrue(all(r is first or r.hypersphere is None for r in fight.rockets))
        self.assertTrue(sigma_pool.dead)
        self.assertEqual(sigma_pool.killer, hanzo.body)
        self.assertTrue(hanzo_pool.dead)
        self.assertEqual(hanzo_pool.killer, original_sigma)
        self.assertLess(sigma_pool.death_tick, hanzo_pool.death_tick)
        self.assertFalse(fight.rockets)
