"""Actual raised Reinhardt Barrier Field against Echo using the external 104319 mesh."""

import math
import os
import unittest
from dataclasses import replace

import test_echo_combat as echo_tests
import test_echo_movement as movement
import test_server_snapshot as snapshots
from test_game_combat import aim, in_world

from ow174.game import echo_movement
from ow174.game.commands import JUMP
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.reinhardt_barrier import HERO


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires external Barrier Field mesh")
class EchoReinhardtBarrierTests(unittest.TestCase):
    def session(self, facing=32768):
        run, echo, rein, _, _ = echo_tests.EchoPrimaryTests().session()
        match = run.match
        match.switch_hero(rein, heroes()[HERO], preserve_pose=True)
        for _ in range(25):
            run.step({})
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is rein)
        x, y, z = target.feet
        direction = aim(fight._eye(echo), (x, y+1, z))
        for _ in range(8):
            run.step({rein: (facing, 0, 2)})
        shield = fight.barriers.reinhardt.actors[rein.body]
        self.assertTrue(shield.active)
        self.assertEqual(shield.health, 1600)
        return run, echo, rein, fight.vitals_of(target), shield, direction

    def test_primary_is_intercepted_only_by_raised_mesh_in_its_path(self):
        for facing in (32768, 0):
            with self.subTest(facing=facing):
                run, echo, rein, vitals, shield, direction = self.session(facing)
                before = vitals.total
                for frame in range(20):
                    run.step({echo: (*direction, 1 if frame == 0 else 0), rein: (facing, 0, 2)})
                self.assertEqual(vitals.total, before if facing else before - 36)
                self.assertEqual(shield.health, 1549 if facing else 1600)

    def test_flight_and_glide_cross_enemy_barrier_without_damaging_it(self):
        for button in (8, JUMP):
            with self.subTest(button=button):
                run, echo, _, vitals, shield, _ = self.session()
                initial = vitals.total
                if button == JUMP:
                    x, _, z = shield.centre()
                    echo.mover = Mover((x, echo.mover.position[1] + 1.5, z - 1.5),
                                       world=run.match.collision_world, data=mover_data(echo.hero.body))
                crossed = False
                for _ in range(55):
                    x, y, z = echo.mover.position
                    origin = (x, y + 1, z)
                    movement.EchoMovementTests.step(run, echo, button, forward=127)
                    x, y, z = echo.mover.position
                    delta = (x - origin[0], y + 1 - origin[1], z - origin[2])
                    length = math.hypot(*delta)
                    self.assertTrue(shield.active)
                    if length and shield.trace(
                        origin, tuple(axis / length for axis in delta), length
                    ) is not None:
                        mode = (echo_movement.flight(echo) if button == 8
                                else echo_movement.glide_properties(echo, JUMP))
                        self.assertIsNotNone(mode)
                        crossed = True
                        break
                self.assertTrue(crossed)
                self.assertEqual(shield.health, 1600)
                self.assertEqual(vitals.total, initial)

    def test_beam_and_sticky_damage_the_raised_mesh_without_spill(self):
        for button in (16, 2):
            with self.subTest(button=button):
                run, echo, rein, vitals, shield, direction = self.session()
                before = vitals.total
                for frame in range(110):
                    run.step({echo: (*direction, button if frame < 20 else 0), rein: (32768, 0, 2)})
                self.assertEqual(vitals.total, before)
                self.assertLess(shield.health, 1600)

    def test_actual_helix_splash_falls_off_with_distance_to_shield(self):
        losses = []
        for offset in (2.9, 3.8):
            run, shooter, rein, vitals, shield, _ = self.session()
            match = run.match
            fight = match.combat
            match.switch_hero(shooter, heroes()[SOLDIER], preserve_pose=True)
            for _ in range(25):
                run.step({rein: (32768, 0, 2)})
            x, y, z = rein.mover.position
            direction = aim(fight._eye(shooter), (x, y, z-offset))
            before = vitals.total
            for frame in range(60):
                run.step({shooter: (*direction, 2 if frame == 0 else 0), rein: (32768, 0, 2)})
            self.assertEqual(vitals.total, before)
            losses.append(shield.maximum - shield.health)
        self.assertGreater(losses[1], 0)
        self.assertGreater(losses[0], losses[1])
        self.assertLess(losses[0], 80)

    def test_release_regenerates_and_body_retirement_removes_the_mesh(self):
        run, echo, rein, _, shield, direction = self.session()
        fight = run.match.combat
        for frame in range(20):
            run.step({echo: (*direction, 1 if frame == 0 else 0), rein: (32768, 0, 2)})
        self.assertEqual(shield.health, 1549)
        for _ in range(110):
            run.step({rein: (32768, 0, 0)})
        self.assertFalse(shield.active)
        self.assertEqual(shield.health, 1549)
        for _ in range(65):
            run.step({})
        self.assertEqual(shield.health, 1600)
        old_body = rein.body
        run.match.switch_hero(rein, heroes()[echo_tests.ECHO], preserve_pose=True)
        self.assertNotIn(old_body, fight.barriers.reinhardt.actors)

    def test_actual_break_consumes_final_hit_and_blocks_early_recast(self):
        run, echo, rein, vitals, shield, direction = self.session()
        fight = run.match.combat
        before = vitals.total
        for _ in range(1500):
            run.step({echo: (*direction, 1), rein: (32768, 0, 2)})
            if shield.health == 0:
                break
        self.assertFalse(shield.active)
        self.assertEqual(shield.health, 0)
        self.assertEqual(vitals.total, before)
        self.assertAlmostEqual(shield.broken_until - fight.now, 5)
        for _ in range(60):
            run.step({rein: (32768, 0, 0)})
        for _ in range(20):
            run.step({rein: (32768, 0, 2)})
            self.assertFalse(shield.active)
        while fight.now <= shield.broken_until:
            run.step({})
        for _ in range(10):
            run.step({rein: (32768, 0, 2)})
        self.assertTrue(shield.active)
        self.assertGreater(shield.health, 0)

    def test_raised_mesh_and_pool_are_in_passive_server_capture(self):
        observer = snapshots.ServerSnapshotTests()
        session, peers, players = observer.scene(two=True)
        rein = players[0]
        session.apply("switch_hero", {"account": rein.account_lo, "hero": HERO})
        peers[0].commands(rein.body_script.frame+1, 25, 0)
        peers[0].commands(rein.body_script.frame+1, 10, 2)
        shield = rein.match.combat.barriers.reinhardt.actors[rein.body]
        self.assertTrue(shield.active)
        before = snapshots.snapshot_digest(observer.capture(session))
        shield.health -= 1  # Passive sensitivity only; damage uses real weapons in the other tests.
        self.assertNotEqual(snapshots.snapshot_digest(observer.capture(session)), before)

    def test_copied_barrier_protects_ally_and_allows_their_fire(self):
        run, caster, enemy, _, _, direction = self.session()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        caster.team, enemy.team = 0, 1
        fight = match.combat
        for _ in range(8):
            run.step({})
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        run.step({caster: (*direction, 32)})
        self.assertEqual(caster.hero.guid, HERO)
        self.assertTrue(fight.echo_duplicates.active(caster))
        match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        ally = match.add_player(3, "Ally", echo_tests.ECHO, caster.team, False)
        in_world(match, ally)
        match.switch_hero(ally, heroes()[echo_tests.ECHO])
        run.players.append(ally)
        x, y, z = caster.mover.position
        ally.mover = Mover((x+1.8, y, z), world=match.collision_world, data=mover_data(ally.hero.body))
        enemy.mover = Mover((x+1.8, y, z+5), world=match.collision_world, data=mover_data(enemy.hero.body))
        for _ in range(25):
            run.step({})
        for _ in range(8):
            run.step({caster: (0, 0, 2)})
        shield = fight.barriers.reinhardt.actors[caster.body]
        self.assertTrue(shield.active)
        allies = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        inbound = aim(fight._eye(enemy), (x+1.8, y+1, z))
        outbound = aim(fight._eye(ally), (x+1.8, y+1, z+5))
        for frame in range(20):
            run.step({caster: (0, 0, 2), enemy: (*inbound, 1 if frame == 0 else 0)})
        self.assertEqual(allies[ally].total, 200)
        self.assertEqual(shield.health, 1549)
        for frame in range(20):
            run.step({caster: (0, 0, 2), ally: (*outbound, 1 if frame == 0 else 0)})
        self.assertEqual(allies[enemy].total, 149)
        self.assertEqual(shield.health, 1549)
