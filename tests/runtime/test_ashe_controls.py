"""Real player controls against Ashe and B.O.B. under reconstructed server policy."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_ashe_bob as gun_fixtures
import test_ashe_bob_charge as charge_fixtures
import test_mccree_flashbang as flash_fixtures

from test_ashe_impulses import wall_scene
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, immunity, mover, stats, world
from ow174.game.collision import flat_world
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, nodes

ASHE = 0x02E0000000000200
MCCREE = 0x02E0000000000042
EXTRA = Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", "."))


class AsheDeflectTests(unittest.TestCase):
    arena = flash_fixtures.FlashbangTests.arena
    health = flash_fixtures.FlashbangTests.health
    step = flash_fixtures.FlashbangTests.step

    def setup_deflect(self, distance=8, **options):
        run, genji, ashe = self.arena(distance=distance, **options)
        for actor, hero, z in ((genji, 0x02E0000000000029, 0), (ashe, ASHE, distance)):
            run.match.switch_hero(actor, heroes()[hero])
            actor.mover.position[:] = (0, 1, z - 3 if options.get("wall") else z)
            actor.mover.body = None
        self.step(run, genji, ashe, cast=16)
        script = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        state = script.node_state(script.graph.node(18))
        self.assertTrue(state.active)
        return run, genji, ashe, state

    def test_authored_deflect_redirects_hip_and_ads_to_ashe(self):
        for ads, damage in ((False, 40), (True, 75)):
            with self.subTest(ads=ads):
                run, genji, ashe, state = self.setup_deflect()
                for _ in range(40 if ads else 3):
                    self.step(run, genji, ashe, target_buttons=2 if ads else 0)
                for _ in range(20):
                    self.step(run, genji, ashe, target_buttons=3 if ads else 1)
                self.assertTrue(state.active)
                self.assertEqual((self.health(run, genji), self.health(run, ashe)), (200, 200 - damage))

    def test_coach_deflect_redirects_pellet_damage_and_impulse(self):
        run, genji, ashe, state = self.setup_deflect(distance=3)
        for _ in range(20):
            self.step(run, genji, ashe, target_buttons=8)
            if self.health(run, ashe) < 200:
                break
        impulses = run.match.combat.impulses.pending
        self.assertNotIn(genji.body, impulses)
        # The owner recoil and the reflected pellet shove both reach Ashe.
        recoil, shove = sorted((impulse.vector for impulse in impulses[ashe.body]), key=lambda v: v[2])
        self.assertAlmostEqual(recoil[1], 18 * math.sin(math.radians(20)))
        self.assertAlmostEqual(recoil[2], 18 * math.cos(math.radians(20)))
        self.assertAlmostEqual(shove[1], 20 * math.sin(math.radians(20)))
        self.assertAlmostEqual(shove[2], 20 * math.cos(math.radians(20)))
        self.step(run, genji, ashe)
        self.step(run, genji, ashe)
        self.assertTrue(state.active)
        self.assertEqual((self.health(run, genji), self.health(run, ashe)), (200, 110))
        self.assertEqual(len(genji.client.messages(combat.HIT)), 15)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        self.assertGreater(ashe.mover.position[2], 3)
        self.assertAlmostEqual(genji.mover.position[2], 0)

    def test_thrown_dynamite_reflects_and_keeps_fuse_then_burns_ashe(self):
        run, genji, ashe, state = self.setup_deflect(distance=3)
        fight = run.match.combat
        world = ashe.body_script.component.world
        ruleset = world.ruleset
        with patch.object(world, "ruleset", side_effect=lambda key: 0.5 if key == 0x4402 else ruleset(key)):
            for _ in range(20):
                self.step(run, genji, ashe, target_buttons=16)
                if fight.dynamite.grenades:
                    break
        grenade, = fight.dynamite.grenades
        self.assertIs(grenade.shooter, ashe)
        self.assertEqual(grenade.fuse - grenade.born, math.ceil(1.0 / combat.TICK))
        clock = grenade.born, grenade.fuse
        for _ in range(20):
            self.step(run, genji, ashe)
            if grenade.shooter is genji:
                break
        self.assertTrue(state.active)
        self.assertIs(grenade.shooter, genji)
        self.assertEqual((grenade.body, grenade.team), (genji.body, genji.team))
        self.assertEqual((grenade.born, grenade.fuse), clock)
        self.assertGreater(grenade.velocity[2], 0)

        # Position Ashe by the reflected grenade just before the fuse expires.
        # The projectile itself follows unmodified flight for its full lifetime.
        while run.frame < grenade.fuse - 1:
            self.step(run, genji, ashe)
        self.assertEqual(self.health(run, ashe), 200)
        x, _, z = grenade.position
        ashe.mover.position[:] = (x, 1, z)
        ashe.mover.body = None
        self.step(run, genji, ashe)
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(self.health(run, ashe), 150)
        burn = fight.dynamite.burns[(genji.body, ashe.body)]
        self.assertIs(burn.shooter, genji)
        for _ in range(17):
            self.step(run, genji, ashe)
        self.assertEqual(self.health(run, ashe), 145)

    def confirmed_reflected_dynamite(self):
        run, genji, ashe, _state = self.setup_deflect(distance=3)
        engine = run.match.combat.dynamite
        for _ in range(20):
            self.step(run, genji, ashe, target_buttons=16)
            if engine.grenades:
                break
        grenade, = engine.grenades
        shot = grenade.shot
        self.assertIsNotNone(shot)
        observer, = shot.observers
        for _ in range(40):
            self.step(run, genji, ashe)
            if grenade.shooter is genji and shot.created and observer.launch_arrival.received:
                break
        self.assertIs(grenade.shooter, genji)
        self.assertIs(shot.player, ashe)
        self.assertTrue(shot.created)
        self.assertTrue(all(arrival.received for arrival in shot.created.values()))
        self.assertTrue(observer.launch_arrival.received)
        return run, genji, ashe, grenade, shot, observer

    def test_reflector_retirement_cancels_live_dynamite_delivery(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                run, genji, ashe, grenade, shot, observer = self.confirmed_reflected_dynamite()
                fight = run.match.combat

                if leave:
                    run.match.leave(genji)
                    run.players.remove(genji)
                else:
                    self.assertTrue(run.match.switch_hero(genji, heroes()[SOLDIER]))
                self.assertNotIn(grenade, fight.dynamite.grenades)
                self.assertNotIn(shot, fight.projectiles.shots)
                while run.frame <= grenade.fuse + 30:
                    run.step({})
                destroys = [update for update in ashe.client.entities
                            if update.op == world.OP_DESTROY and update.entity in shot.created]
                self.assertCountEqual([update.entity for update in destroys], list(shot.created))
                self.assertTrue(all(update.ch2 is None for update in destroys))
                if not leave:
                    copies = [update for update in genji.client.entities
                              if update.op == world.OP_DESTROY and update.entity == observer.record.ids[0]]
                    self.assertEqual(len(copies), 1)
                    self.assertIsNone(copies[0].ch2)
                self.assertEqual(fight.dynamite.burns, {})

    def test_thrower_replacement_retires_delivery_but_reflected_dynamite_still_burns(self):
        run, genji, ashe, grenade, shot, observer = self.confirmed_reflected_dynamite()
        fight = run.match.combat
        old_body = ashe.body
        deadline = grenade.fuse
        self.assertTrue(run.match.switch_hero(ashe, heroes()[SOLDIER]))
        self.assertNotEqual(ashe.body, old_body)
        self.assertIn(grenade, fight.dynamite.grenades)
        self.assertIs(grenade.shooter, genji)
        self.assertEqual(grenade.fuse, deadline)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertIsNone(grenade.shot)
        while run.frame < deadline - 1:
            run.step({})
        for client, ids in ((ashe.client, shot.created), (genji.client, observer.record.ids)):
            destroys = [update for update in client.entities
                        if update.op == world.OP_DESTROY and update.entity in ids]
            self.assertCountEqual([update.entity for update in destroys], list(ids))
            self.assertTrue(all(update.ch2 is None for update in destroys))
        self.assertNotIn(observer, fight.projectiles.observed)

        # Move the replacement body to the live grenade before the original fuse.
        x, _, z = grenade.position
        ashe.mover.position[:] = (x, 1, z)
        ashe.mover.body = None
        run.step({})
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(self.health(run, ashe), 150)
        burn = fight.dynamite.burns[(genji.body, ashe.body)]
        self.assertIs(burn.shooter, genji)
        first_pulse = burn.next_damage
        while run.frame < first_pulse:
            run.step({})
        self.assertEqual(self.health(run, ashe), 145)

    def test_wall_and_allied_genji_do_not_reflect_dynamite(self):
        for options in ({"wall": True}, {"ally": True}):
            with self.subTest(options=options):
                run, genji, ashe, state = self.setup_deflect(**options)
                for _ in range(45):
                    self.step(run, genji, ashe, target_buttons=16)
                grenade, = run.match.combat.dynamite.grenades
                self.assertTrue(state.active)
                self.assertIs(grenade.shooter, ashe)
                self.assertEqual((grenade.body, grenade.team), (ashe.body, ashe.team))
                if options.get("wall"):
                    self.assertGreater(grenade.bounces, 0)
                    self.assertGreater(grenade.position[2], 1)
                else:
                    self.assertLess(grenade.position[2], 0)

    def test_cancel_restores_direct_damage(self):
        run, genji, ashe, state = self.setup_deflect()
        for _ in range(18):
            self.step(run, genji, ashe)
        self.step(run, genji, ashe, cast=16)
        self.assertFalse(state.active)
        for _ in range(20):
            self.step(run, genji, ashe, target_buttons=1)
            if self.health(run, genji) < 200:
                break
        self.assertEqual((self.health(run, genji), self.health(run, ashe)), (120, 200))

    def test_wall_and_allied_genji_do_not_redirect(self):
        for options in ({"wall": True}, {"ally": True}):
            with self.subTest(options=options):
                run, genji, ashe, _ = self.setup_deflect(**options)
                for _ in range(20):
                    self.step(run, genji, ashe, target_buttons=1)
                self.assertEqual((self.health(run, genji), self.health(run, ashe)), (200, 200))


class AsheBarrierTests(unittest.TestCase):
    arena = flash_fixtures.FlashbangTests.arena
    health = flash_fixtures.FlashbangTests.health
    step = flash_fixtures.FlashbangTests.step

    def test_sigma_barrier_input_reaches_factory_through_live_possession(self):
        run, sigma, ashe = self.arena(distance=4)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        component = sigma.body_script.component
        barrier = next(i for i in component.instances.values() if i.graph.index == 0x1B55)
        gate = barrier.graph.node(64).field("m_condition")
        self.assertEqual(barrier.evaluate(gate["m_entity"]), expr.Entity(sigma.entity))
        self.assertIs(barrier.evaluate(gate), True)
        self.step(run, sigma, ashe, cast=2)
        self.assertTrue(barrier.states[4].active)
        if graph.entity_body(0x0400000000002D25) is None:
            self.assertIsNone(component.entity_var(512).value())
        run.match.ended = True
        self.assertIs(barrier.evaluate(gate), False)

    def test_dynamite_natural_flight_crosses_sigma_mesh_before_fuse(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        run, sigma, ashe = self.arena(distance=13)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        for player, z in ((sigma, 0), (ashe, 13)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        fight = run.match.combat
        shields = fight.sigma_barriers
        shield = shields.actors[sigma.body]
        for _ in range(12):
            self.step(run, sigma, ashe, cast=2)
        for _ in range(12):
            self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 3)
        yaw, pitch = aim(fight._eye(ashe), (0, 2, 0))
        for _ in range(20):
            run.step({ashe: (yaw, pitch, 16)})
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        crossed = False
        before = self.health(run, sigma)
        while run.frame < grenade.fuse - 1:
            position = grenade.position
            run.step({})
            length = math.dist(position, grenade.position)
            if length > 1e-9:
                direction = tuple((end - start) / length
                                  for end, start in zip(grenade.position, position, strict=True))
                crossed |= shields.ray(shield, position, direction, length) is not None
        self.assertTrue(crossed)
        self.assertGreater(grenade.bounces, 0)
        self.assertEqual(shield.health, 700)
        self.assertEqual(self.health(run, sigma), before)
        run.step({})
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertLess(shield.health, 700)
        self.assertLess(self.health(run, sigma), before)
        self.assertIn((ashe.body, sigma.body), fight.dynamite.burns)

    def test_dynamite_fuse_uses_sigma_mesh_for_blast_and_protection(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        for outside, pool in ((True, 700), (False, 700), (True, 30)):
            with self.subTest(outside=outside, pool=pool):
                run, sigma, ashe = self.arena(distance=8)
                self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
                self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
                for player, z in ((sigma, 0), (ashe, 8)):
                    player.mover.position[:] = (0, 1, z)
                    player.mover.body = None
                fight = run.match.combat
                shield = fight.sigma_barriers.actors[sigma.body]
                for _ in range(12):
                    self.step(run, sigma, ashe, cast=2)
                for _ in range(12):
                    self.step(run, sigma, ashe)
                self.assertEqual(shield.mode, 3)
                for _ in range(20):
                    self.step(run, sigma, ashe, target_buttons=16)
                    if fight.dynamite.grenades:
                        break
                grenade, = fight.dynamite.grenades
                while run.frame < grenade.fuse - 1:
                    self.step(run, sigma, ashe)
                before = self.health(run, sigma)
                x, y, z = shield.position
                # Isolate blast geometry at the actual fuse deadline.
                grenade.position = (x, y, z + (0.75 if outside else -0.75))
                grenade.velocity = (0, 0, 0)
                if pool < 700:
                    shield.set_health(pool)
                self.step(run, sigma, ashe)
                self.assertNotIn(grenade, fight.dynamite.grenades)
                self.assertEqual(shield.health, max(0, pool - 50))
                burned = (ashe.body, sigma.body) in fight.dynamite.burns
                self.assertEqual(burned, not outside)
                if outside:
                    self.assertEqual(self.health(run, sigma), before)
                else:
                    self.assertLess(self.health(run, sigma), before)

    def test_coach_drains_sigma_barrier_without_shoving_sigma(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        run, sigma, ashe = self.arena(distance=8)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        for player, z in ((sigma, 0), (ashe, 8)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        shield = run.match.combat.sigma_barriers.actors[sigma.body]
        for _ in range(12):
            self.step(run, sigma, ashe, cast=2)
        for _ in range(12):
            self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 3)
        before = self.health(run, sigma)
        for _ in range(20):
            self.step(run, sigma, ashe, target_buttons=8)
        self.assertEqual(shield.health, 610)
        self.assertEqual(self.health(run, sigma), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        self.assertEqual(sigma.mover.position[2], 0)
        self.assertGreater(ashe.mover.position[2], 8)

    def test_viper_drains_sigma_barrier_then_hits_body_after_break(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        run, sigma, ashe = self.arena(distance=8)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        for player, z in ((sigma, 0), (ashe, 8)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        shield = run.match.combat.sigma_barriers.actors[sigma.body]
        for _ in range(12):
            self.step(run, sigma, ashe, cast=2)
        for _ in range(12):
            self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 3)
        before = self.health(run, sigma)
        for _ in range(20):
            self.step(run, sigma, ashe, target_buttons=1)
        self.assertEqual(shield.health, 660)
        self.assertEqual(self.health(run, sigma), before)
        for index in range(1, 10):
            for _ in range(40):
                self.step(run, sigma, ashe, target_buttons=2)
            for _ in range(16):
                self.step(run, sigma, ashe, target_buttons=3)
            self.assertEqual(shield.health, max(0, 660 - 75 * index))
            self.assertEqual(self.health(run, sigma), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        for _ in range(40):
            self.step(run, sigma, ashe, target_buttons=2)
        for _ in range(16):
            self.step(run, sigma, ashe, target_buttons=3)
        self.assertLess(self.health(run, sigma), before)

    def test_sigma_barrier_recall_cooldown_repair_and_redeploy(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        run, sigma, ashe = self.arena(distance=8)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        for player, z in ((sigma, 0), (ashe, 8)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        shields = run.match.combat.sigma_barriers
        shield = shields.actors[sigma.body]
        for _ in range(12):
            self.step(run, sigma, ashe, cast=2)
        for _ in range(12):
            self.step(run, sigma, ashe)
        for _ in range(20):
            self.step(run, sigma, ashe, target_buttons=1)
        self.assertEqual(shield.health, 660)
        self.step(run, sigma, ashe, cast=2)
        self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 1)
        cooldown = shield.source.states[111]
        self.assertGreater(cooldown.remaining(), 1.9)
        self.step(run, sigma, ashe, cast=2)
        self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 1)
        for _ in range(120):
            self.step(run, sigma, ashe)
        self.assertEqual(shield.health, 660)
        for _ in range(80):
            self.step(run, sigma, ashe)
            if shield.health == 700:
                break
        self.assertEqual(shield.health, 700)
        self.assertIs(shields.actors[sigma.body], shield)
        self.assertEqual(cooldown.remaining(), 0)
        for _ in range(12):
            self.step(run, sigma, ashe, cast=2)
        for _ in range(12):
            self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 3)
        self.assertIs(shields.actors[sigma.body], shield)
        before = self.health(run, sigma)
        for _ in range(20):
            self.step(run, sigma, ashe, target_buttons=1)
        self.assertEqual(shield.health, 660)
        self.assertEqual(self.health(run, sigma), before)

    def test_sigma_barrier_cast_hosts_authored_child_and_moves(self):
        if graph.entity_body(0x0400000000002D25) is None:
            self.skipTest("requires explicit build 104319 Experimental Barrier entity")
        run, sigma, ashe = self.arena(distance=8)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        for player, z in ((sigma, 0), (ashe, 8)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        shields = run.match.combat.sigma_barriers
        self.step(run, sigma, ashe, cast=2)
        shield = shields.actors[sigma.body]
        child = shield.instance()
        self.assertEqual(child.graph.index, 0x1A2A)
        self.assertTrue(child.states[25].active)
        self.assertEqual(shield.maximum, 700)
        self.assertEqual(shield.health, 700)
        self.assertEqual(sigma.body_script.component.entity_var(512).value(), expr.Entity(shield.body))
        for _ in range(12):
            self.step(run, sigma, ashe, cast=2)
        self.assertEqual(shield.mode, 2)
        self.assertGreater(shield.position[2], 1)
        for _ in range(12):
            self.step(run, sigma, ashe)
        self.assertEqual(shield.mode, 3)
        position = shield.position
        self.step(run, sigma, ashe)
        self.assertEqual(shield.position, position)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[MCCREE]))
        self.assertFalse(shields.actors)
        self.assertFalse(shield.component.instances)

    def setup(self, *, distance=4):
        run, zarya, ashe = self.arena(distance=distance)
        for actor, hero, z in ((zarya, 0x02E0000000000068, 0), (ashe, ASHE, distance)):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
            actor.mover.position[:] = (0, 1, z)
            actor.mover.body = None
        return run, zarya, ashe

    def cast_barrier(self, run, zarya, target):
        self.step(run, zarya, target, cast=8)
        instance = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x0244)
        phase = instance.states[2]
        self.assertTrue(phase.active)
        return phase

    def activate(self):
        run, zarya, ashe = self.setup()
        return run, zarya, ashe, self.cast_barrier(run, zarya, ashe)

    def assert_energy_credit(self, zarya, amount, attacker, energy):
        component = zarya.body_script.component
        script = next(i for i in component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(script.find_var(0, 499).value(), amount)
        self.assertEqual(script.find_var(0, 392).value().id, attacker)
        self.assertAlmostEqual(component.entity_var(87).value(), energy, places=4)

    def test_scoped_round_breaks_personal_barrier_before_body_damage(self):
        run, zarya, ashe, phase = self.activate()
        before = self.health(run, zarya)
        pools = run.match.combat.personal_barriers
        for attack, remaining in ((8, 110), (1, 70)):
            for _ in range(20):
                self.step(run, zarya, ashe, target_buttons=attack)
            self.assertEqual(pools[zarya.body][2], remaining)
        for _ in range(40):
            self.step(run, zarya, ashe, target_buttons=2)
        self.assertTrue(phase.active)
        for _ in range(20):
            self.step(run, zarya, ashe, target_buttons=3)
            if not phase.active:
                break
        self.assertFalse(phase.active)
        self.assertNotIn(zarya.body, pools)
        self.assertEqual(self.health(run, zarya), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        self.assert_energy_credit(zarya, 70, ashe.body, 40)
        for _ in range(45):
            self.step(run, zarya, ashe, target_buttons=2)
        for _ in range(20):
            self.step(run, zarya, ashe, target_buttons=3)
        self.assertLess(self.health(run, zarya), before)

    def test_dynamite_fuse_breaks_barrier_without_body_damage_or_burn(self):
        run, zarya, ashe = self.setup()
        fight = run.match.combat
        for _ in range(20):
            self.step(run, zarya, ashe, target_buttons=16)
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        for _ in range(30):
            self.step(run, zarya, ashe)
        phase = self.cast_barrier(run, zarya, ashe)
        before = self.health(run, zarya)
        for attack, frames in ((8, 20), (1, 20), (0, 10), (1, 20)):
            for _ in range(frames):
                self.step(run, zarya, ashe, target_buttons=attack)
        self.assertEqual(fight.personal_barriers[zarya.body][2], 30)
        while run.frame < grenade.fuse - 1:
            self.step(run, zarya, ashe)
        # Isolate blast interception with an explicit pose before the real fuse.
        x, y, z = zarya.mover.position
        grenade.position, grenade.velocity = (x, y + 1, z), (0, 0, 0)
        self.assertTrue(phase.active)
        self.step(run, zarya, ashe)
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertFalse(phase.active)
        self.assertNotIn(zarya.body, fight.personal_barriers)
        self.assertEqual(self.health(run, zarya), before)
        self.assertFalse(any(burn.target == zarya.body for burn in fight.dynamite.burns.values()))
        self.assert_energy_credit(zarya, 30, ashe.body, 40)

    def test_dynamite_natural_flight_into_zarya_barrier_damages_pool_at_fuse(self):
        run, zarya, ashe = self.setup(distance=15.5)
        fight = run.match.combat
        for _ in range(20):
            self.step(run, zarya, ashe, target_buttons=16)
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        while run.frame < grenade.born + 12:
            self.step(run, zarya, ashe)
        phase = self.cast_barrier(run, zarya, ashe)
        target = next(item for item in fight._targets() if item.owner is zarya)
        x, y, z = target.feet
        centre = (x, y + target.height / 2, z)
        self.assertGreater(math.dist(grenade.position, centre), combat.PERSONAL_BARRIER_RADIUS)
        before = self.health(run, zarya)
        while run.frame < grenade.fuse - 1:
            self.step(run, zarya, ashe)
        self.assertTrue(phase.active)
        self.assertLess(math.dist(grenade.position, centre), combat.PERSONAL_BARRIER_RADIUS)
        self.assertNotIn(zarya.body, fight.personal_barriers)
        self.step(run, zarya, ashe)
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(fight.personal_barriers[zarya.body][2], 150)
        self.assertEqual(self.health(run, zarya), before)
        self.assertNotIn((ashe.body, zarya.body), fight.dynamite.burns)
        self.assert_energy_credit(zarya, 50, ashe.body, 10)

    def projected_arena(self):
        run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Ashe", "Zarya", "Target"))
        ashe, zarya, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = ashe.mover.position
        zarya.spawn = ((x + 4, y, z + 6), zarya.spawn[1])
        target.spawn = ((x, y, z + 6), target.spawn[1])
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        self.assertTrue(run.match.switch_hero(target, target.hero))
        zarya.team = target.team = 1 - ashe.team
        for _ in range(30):
            run.step({})
        return run, ashe, zarya, target

    @staticmethod
    def projected_input(run, zarya, target):
        fight = run.match.combat
        body = next(item for item in fight._targets() if item.owner is target)
        centre = (body.feet[0], body.feet[1] + body.height / 2, body.feet[2])
        cast = aim(fight._eye(zarya), centre)
        inst = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x025C)
        return cast, inst

    def test_projected_barrier_selects_ally_and_credits_zarya_until_expiry(self):
        run, ashe, zarya, target = self.projected_arena()
        target.team = ashe.team
        fight = run.match.combat
        cast, inst = self.projected_input(run, zarya, target)
        run.step({zarya: (*cast, 16)})
        self.assertFalse(inst.states.get(10) and inst.states[10].active)
        run.step({zarya: (*cast, 0)})
        target.team = zarya.team
        run.step({zarya: (*cast, 16)})
        phase = inst.states[10]
        self.assertTrue(phase.active)
        self.assertEqual(inst.vars[536].value().id, target.body)
        before = self.health(run, target)
        shot = aim(fight._eye(ashe), fight._eye(target))
        for _ in range(20):
            run.step({ashe: (*shot, 1)})
        self.assertEqual(fight.personal_barriers[target.body][2], 160)
        self.assertEqual(self.health(run, target), before)
        self.assert_energy_credit(zarya, 40, ashe.body, 8)
        self.assertIsNone(target.body_script.component.entity_var(87, create=False))
        for _ in range(125):
            run.step({})
        self.assertFalse(phase.active)
        self.assertNotIn(target.body, fight.personal_barriers)
        counter = phase.counter
        self.assertGreater(inst.vars[61].value(), 0)
        run.step({zarya: (*cast, 16)})
        self.assertEqual(phase.counter, counter)
        for _ in range(20):
            run.step({ashe: (*shot, 1)})
        self.assertLess(self.health(run, target), before)
        for _ in range(700):
            if inst.vars[61].value() <= 0:
                break
            run.step({})
        self.assertLessEqual(inst.vars[61].value(), 0)
        run.step({zarya: (*cast, 16)})
        self.assertTrue(phase.active)
        self.assertGreater(phase.counter, counter)
        for _ in range(20):
            run.step({ashe: (*shot, 1)})
        self.assertEqual(fight.personal_barriers[target.body][2], 160)
        self.assertTrue(run.match.switch_hero(zarya, heroes()[MCCREE]))
        self.assertNotIn(target.body, fight.personal_barriers)

    def test_dynamite_natural_flight_into_projected_barrier(self):
        run, ashe, zarya, target = self.projected_arena()
        run.match.collision_world = flat_world(1)
        for player, position in ((ashe, (0, 1, 13)), (zarya, (8, 1, 0)), (target, (0, 1, 0))):
            player.mover.position[:] = position
            player.mover.body = None
        run.step({})
        fight = run.match.combat
        body = next(item for item in fight._targets() if item.owner is target)
        x, y, z = body.feet
        centre = (x, y + body.height / 2, z)
        shot = aim(fight._eye(ashe), centre)
        for _ in range(20):
            run.step({ashe: (*shot, 16)})
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        while run.frame < grenade.born + 12:
            run.step({})
        cast, inst = self.projected_input(run, zarya, target)
        run.step({zarya: (*cast, 16)})
        self.assertTrue(inst.states[10].active)
        self.assertEqual(inst.vars[536].value().id, target.body)
        self.assertGreater(math.dist(grenade.position, centre), combat.PERSONAL_BARRIER_RADIUS)
        before = self.health(run, target)
        caster_before = self.health(run, zarya)
        while run.frame < grenade.fuse - 1:
            run.step({})
        self.assertTrue(inst.states[10].active)
        self.assertLess(math.dist(grenade.position, centre), combat.PERSONAL_BARRIER_RADIUS)
        self.assertNotIn(target.body, fight.personal_barriers)
        run.step({})
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(fight.personal_barriers[target.body][2], 150)
        self.assertEqual(self.health(run, target), before)
        self.assertEqual(self.health(run, zarya), caster_before)
        self.assertNotIn((ashe.body, target.body), fight.dynamite.burns)
        self.assert_energy_credit(zarya, 50, ashe.body, 10)

    def test_dynamite_blast_credits_projected_barrier_caster(self):
        run, ashe, zarya, target = self.projected_arena()
        fight = run.match.combat
        shot = aim(fight._eye(ashe), fight._eye(target))
        for _ in range(20):
            run.step({ashe: (*shot, 16)})
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        while run.frame < grenade.fuse - 2:
            run.step({})
        cast, inst = self.projected_input(run, zarya, target)
        run.step({zarya: (*cast, 16)})
        self.assertTrue(inst.states[10].active)
        before = self.health(run, target)
        caster_before = self.health(run, zarya)
        # Explicit blast pose at the protected ally on the real fuse frame.
        x, y, z = target.mover.position
        grenade.position, grenade.velocity = (x, y + 1, z), (0, 0, 0)
        run.step({})
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(fight.personal_barriers[target.body][2], 150)
        self.assertEqual(self.health(run, target), before)
        self.assertLess(self.health(run, zarya), caster_before)
        self.assertFalse(any(burn.target == target.body for burn in fight.dynamite.burns.values()))
        self.assert_energy_credit(zarya, 50, ashe.body, 10)

    def winston_arena(self, distance):
        run, winston, ashe = self.arena(distance=distance)
        for player, hero, z in ((winston, 0x02E0000000000009, 0), (ashe, ASHE, distance)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        barrier = self.deploy_winston(run, winston, ashe)
        return run, winston, ashe, barrier

    def deploy_winston(self, run, winston, target):
        self.step(run, winston, target, cast=16)
        for _ in range(12):
            self.step(run, winston, target)
        barrier, = run.match.combat.world_barriers.actors.values()
        self.assertEqual(barrier.health, 700)
        hosted = next(i for i in barrier.component.instances.values() if i.graph.index == 0x00FC)
        self.assertTrue(hosted.states[11].active)
        self.assertEqual(expr.to_float(hosted.vars[567].value()), 9)
        return barrier

    def test_winston_world_barrier_intercepts_crossing_but_allows_inside_hit(self):
        for distance in (2, 8):
            with self.subTest(distance=distance):
                run, winston, ashe, barrier = self.winston_arena(distance)
                manager = run.match.combat.world_barriers
                before = self.health(run, winston)
                for _ in range(20):
                    self.step(run, winston, ashe, target_buttons=1)
                if distance == 8:
                    self.assertEqual(barrier.health, 660)
                    self.assertEqual(self.health(run, winston), before)
                    self.assertEqual(ashe.client.messages(combat.HIT), [])
                else:
                    self.assertEqual(barrier.health, 700)
                    self.assertLess(self.health(run, winston), before)
                for _ in range(570):
                    if not manager.actors:
                        break
                    self.step(run, winston, ashe)
                self.assertFalse(manager.actors)

    def test_coach_and_scoped_shots_destroy_winston_barrier_before_body_damage(self):
        run, winston, ashe, barrier = self.winston_arena(8)
        before = self.health(run, winston)
        for _ in range(20):
            self.step(run, winston, ashe, target_buttons=8)
        self.assertEqual(barrier.health, 610)
        for round_number in range(1, 10):
            for _ in range(40):
                self.step(run, winston, ashe, target_buttons=2)
            for _ in range(16):
                self.step(run, winston, ashe, target_buttons=3)
            self.assertEqual(barrier.health, max(0, 610 - 75 * round_number))
            self.assertEqual(self.health(run, winston), before)
        self.assertFalse(run.match.combat.world_barriers.actors)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        for _ in range(40):
            self.step(run, winston, ashe, target_buttons=2)
        for _ in range(16):
            self.step(run, winston, ashe, target_buttons=3)
        self.assertLess(self.health(run, winston), before)

    def test_dynamite_natural_flight_crosses_winston_shell_and_resolves_real_fuse(self):
        for distance, inside_at_fuse in ((8, False), (15.5, True)):
            with self.subTest(distance=distance):
                run, winston, ashe, barrier = self.winston_arena(distance)
                if not inside_at_fuse:
                    # The shell stays deployed while Winston steps out of the throw line.
                    winston.mover.position[0] = 2.0
                    winston.mover.body = None
                engine = run.match.combat.dynamite
                before = self.health(run, winston)
                yaw, pitch = aim(run.match.combat._eye(ashe), (0, run.match.combat._eye(ashe)[1], 0))
                for _ in range(20):
                    run.step({ashe: (yaw, pitch, 16)})
                    if engine.grenades:
                        break
                grenade, = engine.grenades
                self.assertGreater(math.dist(grenade.position, barrier.point), barrier.radius)
                entered = False
                while run.frame < grenade.fuse - 1:
                    self.step(run, winston, ashe)
                    entered |= math.dist(grenade.position, barrier.point) < barrier.radius
                self.assertTrue(entered)
                self.assertGreater(grenade.bounces, 0)
                self.assertEqual(math.dist(grenade.position, barrier.point) < barrier.radius, inside_at_fuse)
                self.assertEqual(self.health(run, winston), before)
                self.assertEqual(barrier.health, 700)
                self.step(run, winston, ashe)
                self.assertNotIn(grenade, engine.grenades)
                self.assertEqual((ashe.body, winston.body) in engine.burns, inside_at_fuse)
                if inside_at_fuse:
                    self.assertEqual(self.health(run, winston), before - 45)  # 50 less five armor.
                else:
                    self.assertEqual(self.health(run, winston), before)
                    self.assertLess(barrier.health, 700)

    def test_dynamite_crossing_winston_shell_blocks_body_blast_and_burn(self):
        for z in (3.5, 5.5):
            with self.subTest(z=z):
                run, winston, ashe, barrier = self.winston_arena(8)
                fight = run.match.combat
                winston.mover.position[:] = (0, 1, 3)
                winston.mover.body = None
                for _ in range(20):
                    self.step(run, winston, ashe, target_buttons=16)
                    if fight.dynamite.grenades:
                        break
                grenade, = fight.dynamite.grenades
                while run.frame < grenade.fuse - 1:
                    self.step(run, winston, ashe)
                before = self.health(run, winston)
                # Explicit blast location before the real fuse fires.
                grenade.position, grenade.velocity = (0, 1.5, z), (0, 0, 0)
                self.step(run, winston, ashe)
                self.assertEqual(barrier.health, 650)
                self.assertNotIn(grenade, fight.dynamite.grenades)
                burns = [burn for burn in fight.dynamite.burns.values() if burn.target == winston.body]
                if z > barrier.radius:
                    self.assertEqual(self.health(run, winston), before)
                    self.assertFalse(burns)
                else:
                    self.assertLess(self.health(run, winston), before)
                    self.assertTrue(burns)

    def test_coach_and_hip_damage_only_personal_barrier(self):
        for attack, remaining in ((8, 110), (1, 160)):
            with self.subTest(attack=attack):
                run, zarya, ashe, phase = self.activate()
                before = self.health(run, zarya)
                for _ in range(20):
                    self.step(run, zarya, ashe, target_buttons=attack)
                self.assertTrue(phase.active)
                self.assertEqual(run.match.combat.personal_barriers[zarya.body][2], remaining)
                self.assertEqual(self.health(run, zarya), before)
                self.assertEqual(ashe.client.messages(combat.HIT), [])


class AsheWraithTests(unittest.TestCase):
    arena = flash_fixtures.FlashbangTests.arena
    health = flash_fixtures.FlashbangTests.health
    step = flash_fixtures.FlashbangTests.step

    def setup_wraith(self, defender_hero=immunity.REAPER, *, distance=4):
        run, reaper, ashe = self.arena(distance=distance)
        for player, hero, z in ((reaper, defender_hero, 0), (ashe, ASHE, distance)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        return run, reaper, ashe

    def test_real_wraith_prevents_rifle_damage_until_cancel(self):
        run, reaper, ashe = self.setup_wraith()
        before = self.health(run, reaper)
        self.step(run, reaper, ashe, cast=8)
        self.assertTrue(immunity.wraith_active(reaper))
        for _ in range(35):
            self.step(run, reaper, ashe, target_buttons=1)
        self.assertTrue(immunity.wraith_active(reaper))
        self.assertEqual(self.health(run, reaper), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        self.step(run, reaper, ashe, cast=8)
        self.assertFalse(immunity.wraith_active(reaper))
        for _ in range(20):
            self.step(run, reaper, ashe, target_buttons=1)
        self.assertLess(self.health(run, reaper), before)

    def test_wraith_excludes_scoped_viper_melee_and_coach(self):
        for attack, distance in (("scoped", 4), ("melee", 1), ("coach", 3)):
            with self.subTest(attack=attack):
                run, reaper, ashe = self.setup_wraith(distance=distance)
                before = self.health(run, reaper)
                instances = ashe.body_script.component.instances.values()
                self.step(run, reaper, ashe, cast=8)
                self.assertTrue(immunity.wraith_active(reaper))
                if attack == "scoped":
                    viper = next(item for item in instances if item.graph.index == 0x14FC)
                    ammo = viper.vars[53].value()
                    for _ in range(40):
                        self.step(run, reaper, ashe, target_buttons=2)
                    for _ in range(20):
                        self.step(run, reaper, ashe, target_buttons=3)
                    self.assertEqual(viper.vars[53].value(), ammo - 1)
                elif attack == "melee":
                    melee = next(item for item in instances if item.graph.index == 0x0043)
                    for _ in range(20):
                        self.step(run, reaper, ashe, target_buttons=0x800)
                    self.assertGreater(melee.states[4].counter, 0)
                else:
                    coach = next(item for item in instances if item.graph.index == 0x1513)
                    ability = next(state for state in coach.states.values() if isinstance(state, nodes.Ability))
                    for _ in range(60):
                        self.step(run, reaper, ashe, target_buttons=8)
                    self.assertGreater(ability.remaining(), 0)
                    self.assertGreater(ashe.mover.position[2], distance)
                    self.assertAlmostEqual(reaper.mover.position[2], 0)
                    self.assertEqual(reaper.mover.velocity, (0, 0, 0))
                self.assertTrue(immunity.wraith_active(reaper))
                self.assertEqual(self.health(run, reaper), before)
                self.assertEqual(ashe.client.messages(combat.HIT), [])

    def test_cryo_freeze_input_publishes_charge_filter_flag_until_exit(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, mei, ashe = self.setup_wraith(immunity.MEI)
                component = mei.body_script.component
                charge_filter = component.entity_var(3692)
                self.assertFalse(charge_filter.value())
                self.step(run, mei, ashe, cast=8)
                cryo = next(item for item in component.instances.values() if item.graph.index == 0x05FE)
                self.assertTrue(cryo.states[10].active)
                self.assertIs(charge_filter.value(), True)
                for _ in range(65):
                    self.step(run, mei, ashe)
                self.assertIs(charge_filter.value(), True)
                if cancel:
                    self.step(run, mei, ashe, cast=2)
                for _ in range(60 if cancel else 230):
                    if not charge_filter.value():
                        break
                    self.step(run, mei, ashe)
                self.assertFalse(charge_filter.value())
                self.assertFalse(cryo.states[10].active)

    def test_cryo_freeze_blocks_viper_damage_until_cancel(self):
        run, mei, ashe = self.setup_wraith(immunity.MEI)
        before = self.health(run, mei)
        self.step(run, mei, ashe, cast=8)
        self.assertTrue(immunity.cryo_active(mei, mei.body))
        for _ in range(65):
            self.step(run, mei, ashe, target_buttons=1)
        self.assertEqual(self.health(run, mei), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        self.step(run, mei, ashe, cast=2)
        self.assertFalse(immunity.cryo_active(mei, mei.body))
        for _ in range(45):
            self.step(run, mei, ashe, target_buttons=1)
        self.assertLess(self.health(run, mei), before)
        self.assertTrue(ashe.client.messages(combat.HIT))

    def test_cryo_freeze_blocks_coach_shove_until_cancel(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, mei, ashe = self.setup_wraith(immunity.MEI, distance=3)
                self.step(run, mei, ashe, cast=8)
                for _ in range(25):
                    self.step(run, mei, ashe)
                if cancel:
                    self.step(run, mei, ashe, cast=2)
                before = self.health(run, mei)
                for _ in range(20):
                    self.step(run, mei, ashe, target_buttons=8)
                self.assertEqual(immunity.cryo_active(mei, mei.body), not cancel)
                self.assertGreater(ashe.mover.position[2], 3)
                pellets = len(ashe.client.messages(combat.HIT))
                self.assertEqual(pellets > 0, cancel)
                self.assertEqual(self.health(run, mei), before - 6 * pellets)
                self.assertEqual(mei.mover.position[2] < -0.1, cancel)

    def test_cryo_freeze_cleanses_existing_dynamite_burn(self):
        run, mei, ashe = self.setup_wraith(immunity.MEI)
        engine, key, burn = self.start_burn(run, mei, ashe)
        before = self.health(run, mei)
        self.step(run, mei, ashe, cast=8)
        self.assertTrue(immunity.cryo_active(mei, mei.body))
        self.assertIs(mei.body_script.component.entity_var(8440).value(), True)
        for _ in range(35):
            self.step(run, mei, ashe)
        self.assertNotIn(key, engine.burns)
        self.step(run, mei, ashe, cast=2)
        self.assertFalse(immunity.cryo_active(mei, mei.body))
        self.assertFalse(mei.body_script.component.entity_var(8440).value())
        for _ in range(35):
            self.step(run, mei, ashe)
        self.assertLess(run.frame, burn.until)
        self.assertEqual(burn.pulses, 1)
        self.assertNotIn(key, engine.burns)
        self.assertGreaterEqual(self.health(run, mei), before)

    def test_fortify_blocks_coach_shove_until_expiry(self):
        for expire in (False, True):
            with self.subTest(expire=expire):
                run, orisa, ashe = self.setup_wraith(immunity.ORISA, distance=3)
                self.step(run, orisa, ashe, cast=8)
                self.assertTrue(immunity.fortify_active(orisa, orisa.body))
                if expire:
                    for _ in range(300):
                        if not immunity.fortify_active(orisa, orisa.body):
                            break
                        self.step(run, orisa, ashe)
                self.assertEqual(immunity.fortify_active(orisa, orisa.body), not expire)
                before = self.health(run, orisa)
                for _ in range(20):
                    self.step(run, orisa, ashe, target_buttons=8)
                pellets = len(ashe.client.messages(combat.HIT))
                self.assertGreater(pellets, 0)
                self.assertAlmostEqual(self.health(run, orisa), before - (3 if expire else 1.8) * pellets, places=4)
                self.assertGreater(ashe.mover.position[2], 3)
                self.assertEqual(orisa.mover.position[2] < -0.1, expire)

    def test_fortify_suppresses_viper_headshot_bonus_until_expiry(self):
        for scoped, protected, exposed in ((False, 19, 75), (True, 40, 145)):
            with self.subTest(scoped=scoped):
                run, orisa, ashe = self.setup_wraith(immunity.ORISA)
                fight = run.match.combat
                self.step(run, orisa, ashe, cast=8)
                self.assertTrue(immunity.blocks_headshot(orisa, orisa.body))
                yaw, pitch = aim(fight._eye(ashe), fight._eye(orisa))
                if scoped:
                    for _ in range(40):
                        run.step({ashe: (yaw, pitch, 2)})
                buttons = 3 if scoped else 1
                before = self.health(run, orisa)
                for _ in range(20):
                    run.step({ashe: (yaw, pitch, buttons)})
                self.assertAlmostEqual(self.health(run, orisa), before - protected, places=4)
                reports = ashe.client.messages(combat.HIT)
                self.assertEqual(len(reports), 1)
                self.assertFalse(reports[0]["+0x78"]["+0x8"] & combat.CRITICAL)
                for _ in range(300):
                    if not immunity.blocks_headshot(orisa, orisa.body):
                        break
                    run.step({ashe: (yaw, pitch, 2 if scoped else 0)})
                self.assertFalse(immunity.blocks_headshot(orisa, orisa.body))
                before = self.health(run, orisa)
                for _ in range(20):
                    run.step({ashe: (yaw, pitch, buttons)})
                self.assertAlmostEqual(self.health(run, orisa), before - exposed, places=4)
                reports = ashe.client.messages(combat.HIT)
                self.assertEqual(len(reports), 2)
                self.assertTrue(reports[-1]["+0x78"]["+0x8"] & combat.CRITICAL)

    def test_fortify_scales_natural_dynamite_fuse_and_live_burn_pulses(self):
        run, orisa, ashe = self.setup_wraith(immunity.ORISA, distance=13)
        fight = run.match.combat
        engine = fight.dynamite
        yaw, pitch = aim(fight._eye(ashe), (0, 2, 0))
        for _ in range(20):
            run.step({ashe: (yaw, pitch, 16)})
            if engine.grenades:
                break
        grenade, = engine.grenades
        while run.frame < grenade.fuse - 2:
            run.step({})
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(immunity.fortify_active(orisa, orisa.body))
        before = self.health(run, orisa)
        run.step({})
        self.assertNotIn(grenade, engine.grenades)
        self.assertAlmostEqual(self.health(run, orisa), before - 25, places=4)
        key = ashe.body, orisa.body
        burn = engine.burns[key]
        protected = exposed = 0
        while run.frame < burn.until:
            previous = burn.pulses
            before = self.health(run, orisa)
            run.step({})
            if burn.pulses == previous:
                self.assertEqual(self.health(run, orisa), before)
                continue
            self.assertEqual(burn.pulses, previous + 1)
            if immunity.fortify_active(orisa, orisa.body):
                protected += 1
                self.assertAlmostEqual(self.health(run, orisa), before - 1.5, places=4)
            else:
                exposed += 1
                self.assertAlmostEqual(self.health(run, orisa), before - 2.5, places=4)
        self.assertGreater(protected, 0)
        self.assertGreater(exposed, 0)
        self.assertEqual(protected + exposed, 20)
        self.assertNotIn(key, engine.burns)

    def test_fortify_scales_real_melee_before_armor_until_expiry(self):
        run, orisa, ashe = self.setup_wraith(immunity.ORISA, distance=1)
        self.step(run, orisa, ashe, cast=8)
        self.assertAlmostEqual(immunity.damage_scale(orisa, orisa.body), 0.6)
        before = self.health(run, orisa)
        for _ in range(20):
            self.step(run, orisa, ashe, target_buttons=0x800)
        self.assertAlmostEqual(self.health(run, orisa), before - 13, places=4)
        self.assertEqual(len(ashe.client.messages(combat.HIT)), 1)
        for _ in range(300):
            if immunity.damage_scale(orisa, orisa.body) == 1:
                break
            self.step(run, orisa, ashe)
        self.assertEqual(immunity.damage_scale(orisa, orisa.body), 1)
        before = self.health(run, orisa)
        for _ in range(20):
            self.step(run, orisa, ashe, target_buttons=0x800)
        self.assertAlmostEqual(self.health(run, orisa), before - 25, places=4)
        self.assertEqual(len(ashe.client.messages(combat.HIT)), 2)

    def start_burn(self, run, reaper, ashe):
        engine = run.match.combat.dynamite
        for _ in range(20):
            self.step(run, reaper, ashe, target_buttons=16)
            if engine.grenades:
                break
        grenade, = engine.grenades
        while run.frame < grenade.fuse - 1:
            self.step(run, reaper, ashe)
        x, y, z = reaper.mover.position
        grenade.position, grenade.velocity = (x, y + 1, z), (0, 0, 0)
        self.step(run, reaper, ashe)
        key = ashe.body, reaper.body
        burn = engine.burns[key]
        for _ in range(17):
            self.step(run, reaper, ashe)
        self.assertEqual(burn.pulses, 1)
        return engine, key, burn

    def test_wraith_removes_an_existing_dynamite_burn(self):
        run, reaper, ashe = self.setup_wraith()
        engine, key, burn = self.start_burn(run, reaper, ashe)
        before = self.health(run, reaper)
        self.step(run, reaper, ashe, cast=8)
        self.assertTrue(immunity.wraith_active(reaper))
        self.assertTrue(reaper.body_script.component.entity_var(530).value())
        for _ in range(35):
            self.step(run, reaper, ashe)
        self.assertNotIn(key, engine.burns)
        self.step(run, reaper, ashe, cast=8)
        self.assertFalse(immunity.wraith_active(reaper))
        for _ in range(35):
            self.step(run, reaper, ashe)
        self.assertLess(run.frame, burn.until)
        self.assertNotIn(key, engine.burns)
        self.assertEqual(self.health(run, reaper), before)

    def test_fade_retires_existing_burn_and_rifle_hits_resume_after_expiry(self):
        run, moira, ashe = self.setup_wraith(immunity.MOIRA)
        engine, key, burn = self.start_burn(run, moira, ashe)
        fight = run.match.combat
        component = moira.body_script.component
        fade = next(i for i in component.instances.values() if i.graph.index == 0x0F7C)
        weapon = next(i for i in ashe.body_script.component.instances.values() if i.graph.index == 0x14FC)
        before = self.health(run, moira)
        hit_count = len(ashe.client.messages(combat.HIT))
        self.step(run, moira, ashe, cast=8)
        self.assertTrue(fade.states[9].flags & nodes.Ability.RUNNING)
        self.assertTrue(fade.states[3].active)
        for slot, position in ((530, 10), (6604, 49)):
            value = component.entity_var(slot)
            self.assertIs(value.value(), True)
            self.assertTrue(any(link.state is fade.node_state(fade.graph.node(position)) for link in value.links))

        for frame in range(20):
            self.step(run, moira, ashe, target_buttons=1 if frame < 5 else 0)
        self.assertTrue(fade.states[3].active)
        self.assertEqual(weapon.vars[53].value(), 11)
        self.assertEqual(self.health(run, moira), before)
        self.assertEqual(len(ashe.client.messages(combat.HIT)), hit_count)
        self.assertNotIn(key, engine.burns)

        for _ in range(100):
            if not fade.states[3].active:
                break
            self.step(run, moira, ashe)
        self.assertFalse(fade.states[3].active)
        self.assertFalse(expr.truthy(component.entity_var(530).value()))
        self.assertFalse(expr.truthy(component.entity_var(6604).value()))
        self.assertLess(run.frame, burn.until)
        self.assertEqual(burn.pulses, 1)
        self.assertNotIn(key, engine.burns)
        self.assertEqual(self.health(run, moira), before)

        self.step(run, moira, ashe, target_buttons=1)
        for _ in range(20):
            self.step(run, moira, ashe)
        self.assertEqual(weapon.vars[53].value(), 10)
        self.assertLess(self.health(run, moira), before)
        self.assertEqual(len(ashe.client.messages(combat.HIT)), hit_count + 1)

    def test_transcendence_alone_sets_8440_and_removes_existing_burn(self):
        run, zen, ashe = self.setup_wraith(immunity.ZENYATTA)
        engine, key, burn = self.start_burn(run, zen, ashe)
        before = self.health(run, zen)
        component = zen.body_script.component
        cost = expr.to_float(component.entity_var(338).value())
        self.assertGreater(cost, 0)
        zen.body_script.set_ult_charge(cost)
        self.step(run, zen, ashe, cast=32)
        active = [flag for flag in (6604, 8440, 530, 531, 478)
                  if (var := component.entity_var(flag, create=False)) is not None and expr.truthy(var.value())]
        self.assertEqual(active, [8440])
        self.assertLess(expr.to_float(component.entity_var(342).value()), cost)
        for _ in range(35):
            self.step(run, zen, ashe)
        self.assertNotIn(key, engine.burns)
        self.assertEqual(burn.pulses, 1)
        self.assertEqual(self.health(run, zen), before)
        for _ in range(375):
            if not expr.truthy(component.entity_var(8440).value()):
                break
            self.step(run, zen, ashe)
        self.assertFalse(expr.truthy(component.entity_var(8440).value()))
        self.assertNotIn(key, engine.burns)

    def test_transcendence_blocks_rifle_damage_until_authored_phase_ends(self):
        run, zen, ashe = self.setup_wraith(immunity.ZENYATTA)
        fight = run.match.combat
        component = zen.body_script.component
        zen.body_script.set_ult_charge(expr.to_float(component.entity_var(338).value()))
        self.step(run, zen, ashe, cast=32)
        self.assertTrue(immunity.damage_immune(zen, zen.body))
        self.assertTrue(any(target.owner is zen for target in fight._enemies(ashe)))
        before = self.health(run, zen)
        for _ in range(20):
            self.step(run, zen, ashe, target_buttons=1)
        self.assertEqual(self.health(run, zen), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        for _ in range(375):
            if not immunity.damage_immune(zen, zen.body):
                break
            self.step(run, zen, ashe)
        self.assertFalse(immunity.damage_immune(zen, zen.body))
        for _ in range(20):
            self.step(run, zen, ashe, target_buttons=1)
        self.assertLess(self.health(run, zen), before)
        self.assertTrue(ashe.client.messages(combat.HIT))

    def test_active_immunity_blocks_real_fuse_blast_and_new_burn(self):
        cases = ((immunity.REAPER, 8), (immunity.ZENYATTA, 32), (immunity.MEI, 8), (immunity.MOIRA, 8))
        for hero, button in cases:
            with self.subTest(hero=hero):
                run, defender, ashe = self.setup_wraith(hero)
                engine = run.match.combat.dynamite
                for _ in range(20):
                    self.step(run, defender, ashe, target_buttons=16)
                    if engine.grenades:
                        break
                grenade, = engine.grenades
                while run.frame < grenade.fuse - 2:
                    self.step(run, defender, ashe)
                if button == 32:
                    cost = expr.to_float(defender.body_script.component.entity_var(338).value())
                    defender.body_script.set_ult_charge(cost)
                self.step(run, defender, ashe, cast=button)
                self.assertTrue(immunity.damage_immune(defender, defender.body))
                before = self.health(run, defender)
                x, y, z = defender.mover.position
                grenade.position, grenade.velocity = (x, y + 1, z), (0, 0, 0)
                self.step(run, defender, ashe)
                self.assertNotIn(grenade, engine.grenades)
                self.assertEqual(self.health(run, defender), before)
                self.assertFalse(any(burn.target == defender.body for burn in engine.burns.values()))
                for _ in range(380):
                    if not immunity.damage_immune(defender, defender.body):
                        break
                    self.step(run, defender, ashe)
                self.assertFalse(immunity.damage_immune(defender, defender.body))
                for _ in range(20):
                    self.step(run, defender, ashe, target_buttons=1)
                self.assertLess(self.health(run, defender), before)


class AsheMatrixTests(unittest.TestCase):
    arena = flash_fixtures.FlashbangTests.arena
    step = flash_fixtures.FlashbangTests.step
    health = flash_fixtures.FlashbangTests.health

    def matrix_arena(self, *, distance=4):
        mech = 0x0400000000000407
        record = graph.entity_body(mech)
        if record is None or graph.graph(0x02CC) is None:
            self.skipTest("requires explicit 104319 D.Va mech data")
        run, dva, ashe = self.arena(distance=distance)
        pilot = heroes()[0x02E000000000007A]
        bodies = {**graph.bodies(), pilot.body: record}
        with patch.object(graph, "bodies", return_value=bodies):
            self.assertTrue(run.match.switch_hero(dva, replace(pilot, body=mech)))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        for player, z in ((dva, 0), (ashe, distance)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        for _ in range(70):
            self.step(run, dva, ashe)
        instances = {i.graph.index: i for i in dva.body_script.component.instances.values()}
        # The fixture begins in the mech and has no native call-mech animation.
        instances[0x0276].states[30].end(False)
        return run, dva, ashe, instances[0x02CC]

    def test_matrix_absorbs_hip_ads_and_coach_without_reflection(self):
        for attack in (1, 3, 8):
            with self.subTest(attack=attack):
                run, dva, ashe, matrix = self.matrix_arena()
                before = self.health(run, dva), self.health(run, ashe)
                self.step(run, dva, ashe, cast=2)
                if attack == 3:
                    for _ in range(40):
                        self.step(run, dva, ashe, cast=2, target_buttons=2)
                weapon = next(i for i in ashe.body_script.component.instances.values() if i.graph.index == 0x14FC)
                ammo = weapon.vars[53].value()
                for _ in range(20):
                    self.step(run, dva, ashe, cast=2, target_buttons=attack)
                self.assertTrue(matrix.states[3].active)
                self.assertEqual((self.health(run, dva), self.health(run, ashe)), before)
                self.assertEqual(ashe.client.messages(combat.HIT), [])
                self.assertEqual(dva.client.messages(combat.HIT), [])
                if attack == 8:
                    self.assertGreater(ashe.mover.position[2], 4)
                    self.assertEqual(dva.mover.position[2], 0)
                else:
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertGreater(matrix.vars[6584].value(), 0)
                self.assertEqual(matrix.vars[41].value().id, ashe.body)
                for _ in range(65):
                    self.step(run, dva, ashe)
                    if not matrix.states[3].active:
                        break
                self.assertFalse(matrix.states[3].active)
                for _ in range(45):
                    self.step(run, dva, ashe, target_buttons=1)
                self.assertLess(self.health(run, dva), before[0])

    def test_matrix_rear_shot_hits_and_terrain_precedes_interception(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, dva, ashe, matrix = self.matrix_arena(distance=14 if wall else 4)
                fight = run.match.combat
                aim_dva = aim(fight._eye(dva), fight._eye(ashe))
                aim_ashe = aim(fight._eye(ashe), fight._eye(dva))
                if wall:
                    run.match.collision_world = wall_scene(12)
                else:
                    aim_dva = (aim_dva[0] + 32768, aim_dva[1])
                run.step({dva: (*aim_dva, 2)})
                self.assertTrue(matrix.states[3].active)
                weapon = next(i for i in ashe.body_script.component.instances.values() if i.graph.index == 0x14FC)
                ammo = weapon.vars[53].value()
                before = self.health(run, dva)
                for _ in range(20):
                    run.step({dva: (*aim_dva, 2), ashe: (*aim_ashe, 1)})
                self.assertTrue(matrix.states[3].active)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertFalse(matrix.vars.get(6584) and matrix.vars[6584].value())
                if wall:
                    self.assertEqual(self.health(run, dva), before)
                    self.assertEqual(ashe.client.messages(combat.HIT), [])
                    run.match.collision_world = flat_world(1)
                    for _ in range(40):
                        run.step({dva: (*aim_dva, 2)})
                    for _ in range(20):
                        run.step({dva: (*aim_dva, 2), ashe: (*aim_ashe, 1)})
                        if matrix.vars.get(6584) and matrix.vars[6584].value():
                            break
                    self.assertTrue(matrix.states[3].active)
                    self.assertEqual(weapon.vars[53].value(), ammo - 2)
                    self.assertEqual(matrix.vars[6584].value(), 40)
                    self.assertEqual(self.health(run, dva), before)
                else:
                    self.assertLess(self.health(run, dva), before)
                    self.assertEqual(len(ashe.client.messages(combat.HIT)), 1)

    def test_matrix_consumes_thrown_dynamite_before_fuse(self):
        run, dva, ashe, matrix = self.matrix_arena()
        engine = run.match.combat.dynamite
        before = self.health(run, dva), self.health(run, ashe)
        self.step(run, dva, ashe, cast=2)
        for _ in range(20):
            self.step(run, dva, ashe, cast=2, target_buttons=16)
            if engine.grenades:
                break
        grenade, = engine.grenades
        for _ in range(20):
            if grenade not in engine.grenades:
                break
            self.step(run, dva, ashe, cast=2)
        self.assertTrue(matrix.states[3].active)
        self.assertNotIn(grenade, engine.grenades)
        self.assertLess(run.frame, grenade.fuse)
        self.assertEqual(matrix.vars[6584].value(), 75)
        self.assertEqual(matrix.vars[41].value().id, ashe.body)
        while run.frame <= grenade.fuse:
            self.step(run, dva, ashe)
        self.assertFalse(engine.burns)
        self.assertEqual((self.health(run, dva), self.health(run, ashe)), before)
        self.assertEqual(ashe.client.messages(combat.HIT), [])

    @unittest.skipUnless(EXTRA.is_file(), "requires B.O.B. movement profiles")
    def test_bob_charge_launches_dva_then_downward_matrix_absorbs_followup(self):
        run, dva, ashe, matrix = self.matrix_arena(distance=8)
        fight = run.match.combat
        target = next(item for item in fight._targets() if item.owner is dva)
        vitals = fight.vitals_of(target)
        before = vitals.total
        uppercut = 115 if vitals.now[combat.ARMOUR_POOL] else 120
        ashe.body_script.set_ult_charge(2240)
        self.step(run, dva, ashe, cast=2, target_buttons=32)
        actor, = fight.companions.active.values()
        while actor.phase == "charge" and run.frame < actor.charge_until:
            self.step(run, dva, actor.bot, cast=2)
        self.assertTrue(matrix.states[3].active)
        self.assertEqual(actor.uppercut_target, dva.body)
        self.assertEqual(vitals.total, before - uppercut)
        for _ in range(5):
            self.step(run, dva, actor.bot, cast=2)
        self.assertGreater(dva.mover.velocity[1], 0)
        while run.frame < actor.ready + 20:
            self.step(run, dva, actor.bot, cast=2)
        self.assertTrue(matrix.states[3].active)
        self.assertGreater(actor.shots, 0)
        self.assertGreater(dva.mover.pitch, 0)
        self.assertEqual(vitals.total, before - uppercut)
        self.assertEqual(matrix.vars[6584].value(), 14)
        self.assertEqual(matrix.vars[41].value().id, actor.bot.entity)
        self.assertEqual(len(ashe.client.messages(combat.HIT)), 1)

    @unittest.skipUnless(EXTRA.is_file(), "requires B.O.B. movement profiles")
    def test_matrix_absorbs_bob_gun_until_release(self):
        run, dva, ashe, matrix = self.matrix_arena(distance=8)
        for player, z in ((ashe, 0), (dva, 8)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        fight = run.match.combat
        before = self.health(run, dva)
        run.match.collision_world = wall_scene(3)
        run.step({})
        ashe.body_script.set_ult_charge(2240)
        fixture = charge_fixtures.BobChargeTests()
        actor = fixture.begin(run, ashe)
        fixture.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "wall")
        run.match.collision_world = flat_world(1)
        while run.frame < actor.ready + 25:
            self.step(run, dva, actor.bot, cast=2)
        self.assertTrue(matrix.states[3].active)
        self.assertGreater(actor.shots, 0)
        self.assertEqual(self.health(run, dva), before)
        self.assertEqual(matrix.vars[6584].value(), 14)
        self.assertEqual(matrix.vars[41].value().id, actor.bot.entity)
        self.assertEqual(ashe.client.messages(combat.HIT), [])
        for _ in range(65):
            self.step(run, dva, actor.bot)
            if ashe.client.messages(combat.HIT):
                break
        self.assertFalse(matrix.states[3].active)
        self.assertLess(self.health(run, dva), before)

    def test_matrix_resource_exhaustion_exposes_frontal_target(self):
        run, dva, ashe, matrix = self.matrix_arena()
        self.step(run, dva, ashe, cast=2)
        state = matrix.states[3]
        self.assertTrue(state.active)
        for _ in range(160):
            if not state.active:
                break
            self.step(run, dva, ashe, cast=2)
        self.assertFalse(state.active)
        self.assertAlmostEqual(matrix.vars[87].value(), 0)
        before = self.health(run, dva)
        for _ in range(20):
            self.step(run, dva, ashe, cast=2, target_buttons=1)
            self.assertFalse(state.active)
        self.assertLess(self.health(run, dva), before)
        self.assertEqual(len(ashe.client.messages(combat.HIT)), 1)
        self.assertFalse(matrix.vars.get(6584) and matrix.vars[6584].value())

    def test_real_matrix_input_drains_resource_and_release_ends_interceptor(self):
        run, dva, ashe, matrix = self.matrix_arena()
        self.assertEqual(matrix.vars[87].value(), 100)
        for _ in range(20):
            self.step(run, dva, ashe, cast=2)
        state = matrix.states[3]
        self.assertTrue(state.active)
        remaining = matrix.vars[87].value()
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, 100)
        for _ in range(65):
            if not state.active:
                break
            self.step(run, dva, ashe)
        self.assertFalse(state.active)
        for _ in range(150):
            if matrix.vars[87].value() == 100:
                break
            self.step(run, dva, ashe)
        self.assertEqual(matrix.vars[87].value(), 100)


class AsheGraspTests(unittest.TestCase):
    arena = flash_fixtures.FlashbangTests.arena
    health = flash_fixtures.FlashbangTests.health
    step = flash_fixtures.FlashbangTests.step

    def setup_grasp(self):
        run, sigma, ashe = self.arena(distance=4)
        for actor, hero, z in ((sigma, 0x02E000000000023B, 0), (ashe, ASHE, 4)):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
            actor.mover.position[:] = (0, 1, z)
            actor.mover.body = None
        self.step(run, sigma, ashe, cast=8)
        script = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        state = script.node_state(script.graph.node(7))
        self.assertTrue(state.active)
        return run, sigma, ashe, state

    def test_grasp_absorbs_viper_and_coach_then_expires(self):
        for attack in (1, 3, 8):
            with self.subTest(attack=attack):
                run, sigma, ashe, state = self.setup_grasp()
                before = self.health(run, sigma)
                if attack == 3:
                    for _ in range(40):
                        self.step(run, sigma, ashe, target_buttons=2)
                for _ in range(20):
                    self.step(run, sigma, ashe, target_buttons=attack)
                self.assertTrue(state.active)
                accumulator = state.instance.find_var(0, 21312)
                absorbed = accumulator.value()
                expected = {1: 40, 3: 75, 8: 90}[attack]
                self.assertEqual(absorbed, expected)
                params = state.instance.graph.node(34).field("m_params")
                conversion = next(row["m_value"] for row in params
                                  if row["m_B5051BCE"] == "0x0A4000000000002A")
                self.assertAlmostEqual(state.instance.evaluate(conversion), expected * 0.6, places=4)
                self.assertEqual(self.health(run, sigma), before)
                self.assertEqual(self.health(run, ashe), 200)
                self.assertEqual(ashe.client.messages(combat.HIT), [])
                for _ in range(126):
                    if not state.active:
                        break
                    self.step(run, sigma, ashe)
                self.assertFalse(state.active)
                granted = before + expected * 0.6
                self.assertAlmostEqual(self.health(run, sigma), granted, places=4)
                for _ in range(20):
                    self.step(run, sigma, ashe, target_buttons=1)
                self.assertAlmostEqual(self.health(run, sigma), granted - 80, places=4)

    def test_grasp_shields_hold_then_decay_without_touching_base_pools(self):
        run, sigma, ashe, state = self.setup_grasp()
        before = self.health(run, sigma)
        for _ in range(20):
            self.step(run, sigma, ashe, target_buttons=1)
        for _ in range(126):
            if not state.active:
                break
            self.step(run, sigma, ashe)
        self.assertFalse(state.active)
        self.assertAlmostEqual(self.health(run, sigma), before + 24, places=4)
        vitals = run.match.combat.vitals[sigma.body]
        kind = combat.GRASP_SHIELDS
        base = {key: value for key, value in vitals.now.items() if key != kind}
        for _ in range(180):
            self.step(run, sigma, ashe)
        self.assertAlmostEqual(vitals.now[kind], 24, places=4)
        for _ in range(70):
            self.step(run, sigma, ashe)
        self.assertAlmostEqual(vitals.now[kind], 19, delta=0.2)
        for _ in range(280):
            self.step(run, sigma, ashe)
        self.assertEqual(vitals.now[kind], 0)
        self.assertEqual(vitals.most[kind], 0)
        self.assertEqual({key: value for key, value in vitals.now.items() if key != kind}, base)

    def test_grasp_consumes_thrown_dynamite_without_blast_or_burn(self):
        run, sigma, ashe, state = self.setup_grasp()
        before = self.health(run, sigma)
        fight = run.match.combat
        for _ in range(20):
            self.step(run, sigma, ashe, target_buttons=16)
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        shot = grenade.shot
        self.assertIsNotNone(shot)
        observer, = shot.observers
        for _ in range(20):
            self.step(run, sigma, ashe)
            if not fight.dynamite.grenades:
                break
        self.assertTrue(state.active)
        self.assertLess(run.frame, grenade.fuse)
        self.assertEqual(fight.dynamite.grenades, [])
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertEqual(grenade.absorb_damage, 75)
        self.assertEqual(state.instance.find_var(0, 21312).value(), 75)
        self.assertEqual(self.health(run, sigma), before)
        for _ in range(150):
            if not state.active and run.frame > grenade.fuse:
                break
            self.step(run, sigma, ashe)
        self.assertFalse(state.active)
        self.assertGreater(run.frame, grenade.fuse)
        self.assertTrue(observer.launch_arrival.received)
        destroys = [update for update in sigma.client.entities
                    if update.op == world.OP_DESTROY and update.entity == observer.record.ids[0]]
        self.assertEqual(len(destroys), 1)
        self.assertIsNone(destroys[0].ch2)
        self.assertEqual(fight.dynamite.burns, {})
        self.assertAlmostEqual(self.health(run, sigma), before + 45, places=4)
        self.assertEqual(self.health(run, ashe), 200)

    def test_grasp_retires_confirmed_owner_copy_without_explosion_release(self):
        run, sigma, ashe, state = self.setup_grasp()
        fight = run.match.combat
        sigma.mover.position[0] = 8
        sigma.mover.body = None
        for _ in range(20):
            run.step({ashe: (-32768, 0, 16)})
            if fight.dynamite.grenades:
                break
        grenade, = fight.dynamite.grenades
        shot = grenade.shot
        self.assertIsNotNone(shot)
        for _ in range(36):
            run.step({})
            if shot.created:
                break
        self.assertTrue(shot.created)
        self.assertTrue(all(arrival.received for arrival in shot.created.values()))
        self.assertTrue(state.active)
        # Bring the active absorber across the real throw path after confirmation.
        sigma.mover.position[:] = (grenade.position[0], 1, grenade.position[2] - 2)
        sigma.mover.body = None
        for _ in range(16):
            run.step({sigma: (0, 0, 0)})
            if grenade not in fight.dynamite.grenades:
                break
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertEqual(state.instance.find_var(0, 21312).value(), 75)
        while run.frame <= grenade.fuse:
            run.step({})
        destroys = [update for update in ashe.client.entities
                    if update.op == world.OP_DESTROY and update.entity in shot.created]
        self.assertCountEqual([update.entity for update in destroys], list(shot.created))
        self.assertTrue(all(update.ch2 is None for update in destroys))


class AsheFlashbangTests(unittest.TestCase):
    arena = flash_fixtures.FlashbangTests.arena
    health = flash_fixtures.FlashbangTests.health
    step = flash_fixtures.FlashbangTests.step
    cast = flash_fixtures.FlashbangTests.cast

    def test_ashe_cannot_attack_move_or_cast_during_real_stun_then_recovers(self):
        run, caster, ashe = self.arena(distance=3)
        run.match.switch_hero(ashe, heroes()[ASHE])
        ashe.mover.position[:] = (0, caster.mover.position[1], 3)
        ashe.mover.body = None  # explicit placement before the tested cast
        self.cast(run, caster, ashe)
        self.assertEqual(self.health(run, ashe), 175)
        fight = run.match.combat
        until = fight.stuns[ashe.body]
        position = tuple(ashe.mover.position)
        health = self.health(run, caster)
        ashe.body_script.set_ult_charge(2240)
        while run.frame < until - 1:
            self.step(run, caster, ashe, target_buttons=1 | 8 | 16 | 32, forward=127)
            self.assertEqual(tuple(ashe.mover.position), position)
            self.assertEqual(self.health(run, caster), health)
            self.assertFalse(ashe.body_script.component.held)
            self.assertFalse(fight.dynamite.grenades)
            self.assertFalse(fight.companions.active)
        for _ in range(30):
            self.step(run, caster, ashe, target_buttons=1, forward=127)
        self.assertNotIn(ashe.body, fight.stuns)
        self.assertNotEqual(tuple(ashe.mover.position), position)
        self.assertLess(self.health(run, caster), health)

    def test_flashbang_releases_ads_and_fresh_hip_or_scoped_fire_recovers(self):
        for scope_again, volley_pos, damage in ((False, 220, 80), (True, 14, 150)):
            with self.subTest(scope_again=scope_again):
                run, caster, ashe = self.arena(distance=3)
                self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
                ashe.mover.position[:] = (0, caster.mover.position[1], 3)
                ashe.mover.body = None
                for _ in range(45):
                    self.step(run, caster, ashe, target_buttons=2)
                fight = run.match.combat
                component = ashe.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x14FC)
                common = next(i for i in component.instances.values() if i.graph.index == 0x1C7)
                speed = (common.id, common.graph.node(152).state, 0)
                self.assertEqual(mover.statescript_mods(component)[speed], (mover.SPEED, mover.ADD, -0.25, 0))
                self.assertEqual(weapon.vars[53].value(), 12)
                self.cast(run, caster, ashe, target_buttons=2)
                self.assertEqual(self.health(run, ashe), 175)
                self.assertFalse(component.held)

                caster_health = self.health(run, caster)
                until = fight.stuns[ashe.body]
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    while run.frame < until - 1:
                        self.step(run, caster, ashe, target_buttons=3)
                        self.assertFalse(component.held)
                        self.assertEqual(weapon.vars[53].value(), 12)
                    self.assertTrue(fight.is_stunned(ashe))
                    self.assertEqual(mover.statescript_mods(component)[speed], (mover.SPEED, mover.ADD, 0, 0))
                    self.assertEqual(self.health(run, caster), caster_health)
                    for _ in range(40):
                        self.step(run, caster, ashe)
                    self.assertFalse(fight.is_stunned(ashe))
                    shots.assert_not_called()

                    ads = 2 if scope_again else 0
                    if scope_again:
                        for _ in range(40):
                            self.step(run, caster, ashe, target_buttons=ads)
                        self.assertEqual(mover.statescript_mods(component)[speed][2], -0.25)
                    self.step(run, caster, ashe, target_buttons=ads | 1)
                    for _ in range(25):
                        self.step(run, caster, ashe, target_buttons=ads)
                    shots.assert_called_once()
                    self.assertIs(shots.call_args.args[0], ashe)
                    self.assertIs(shots.call_args.args[2].instance, weapon)
                    self.assertEqual(shots.call_args.args[2].node.pos, volley_pos)
                self.assertEqual(weapon.vars[53].value(), 11)
                self.assertEqual(self.health(run, caster), caster_health - damage)

    def test_real_lethal_revolver_shot_ends_coach_and_dynamite_windups(self):
        for graph_id, button, interrupted_cooldown in ((0x1513, 8, 10), (0x14FD, 16, 0)):
            with self.subTest(graph_id=hex(graph_id)):
                run, caster, ashe = self.arena(distance=3)
                self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
                ashe.mover.position[:] = (0, caster.mover.position[1], 3)
                ashe.mover.body = None
                for _ in range(40):
                    self.step(run, caster, ashe)
                fight = run.match.combat
                ability, = (state for instance in ashe.body_script.component.instances.values()
                            if instance.graph.index == graph_id for state in instance.states.values()
                            if isinstance(state, nodes.Ability))
                for remaining in (130, 60):
                    self.step(run, caster, ashe, cast=1)
                    for _ in range(45):
                        self.step(run, caster, ashe)
                    self.assertEqual(self.health(run, ashe), remaining)

                caster_health = self.health(run, caster)
                position = tuple(ashe.mover.position)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    self.step(run, caster, ashe, target_buttons=button)
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(ability.remaining(), 0)
                    shots.assert_not_called()
                    self.step(run, caster, ashe, cast=1)
                    self.assertTrue(fight.is_dead(ashe))
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(ability.cur, interrupted_cooldown)
                    for _ in range(42):
                        self.step(run, caster, ashe, target_buttons=button)
                    shots.assert_called_once()
                    self.assertIs(shots.call_args.args[0], caster)
                self.assertTrue(fight.is_dead(ashe))
                self.assertEqual(self.health(run, caster), caster_health)
                self.assertEqual(tuple(ashe.mover.position), position)
                self.assertNotIn(ashe.body, fight.impulses.pending)
                self.assertFalse(fight.dynamite.grenades)

    def test_flashbang_ends_viper_reload_and_preserves_already_loaded_shells(self):
        for elapsed, expected_ammo in ((10, 10), (33, 11)):
            with self.subTest(elapsed=elapsed):
                run, caster, ashe = self.arena(distance=3)
                self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
                ashe.mover.position[:] = (0, caster.mover.position[1], 3)
                ashe.mover.body = None
                for _ in range(40):
                    self.step(run, caster, ashe)
                for _ in range(2):
                    self.step(run, caster, ashe, target_buttons=1)
                    for _ in range(79):
                        self.step(run, caster, ashe)
                component = ashe.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x14FC)
                reload = weapon.node_state(weapon.graph.node(65))
                self.assertEqual(weapon.vars[53].value(), 10)
                self.step(run, caster, ashe, target_buttons=0x400)
                for _ in range(elapsed - 1):
                    self.step(run, caster, ashe)
                self.assertTrue(reload.top)
                self.assertEqual(weapon.vars[53].value(), expected_ammo)

                self.cast(run, caster, ashe)
                self.assertEqual(self.health(run, ashe), 175)
                self.assertFalse(reload.active)
                self.assertFalse(weapon.vars[51].value())
                self.assertFalse(weapon.states[74].active)
                self.assertFalse(any(link.state is reload for link in component.entity_var(33).links))
                for _ in range(90):
                    self.step(run, caster, ashe)
                    self.assertEqual(weapon.vars[53].value(), expected_ammo)
                self.assertFalse(run.match.combat.is_stunned(ashe))
                self.assertFalse(reload.active)

                self.step(run, caster, ashe, target_buttons=0x400)
                for _ in range(100):
                    self.step(run, caster, ashe)
                self.assertEqual(weapon.vars[53].value(), 12)
                self.assertFalse(reload.active)
                self.assertIsNone(component.fault)

    def test_empty_viper_reload_waits_for_flashbang_expiry_then_restarts(self):
        run, caster, ashe = self.arena(distance=3)
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        ashe.mover.position[:] = (0, caster.mover.position[1], 3)
        ashe.mover.body = None
        for _ in range(40):
            self.step(run, caster, ashe)
        component = ashe.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x14FC)
        for shot in range(12):
            for _ in range(5):
                run.step({ashe: (0, 0, 1)})  # face away from the Flashbang caster
            if shot < 11:
                for _ in range(75):
                    run.step({})
        self.assertEqual(weapon.vars[53].value(), 0)
        for _ in range(12):
            self.step(run, caster, ashe)
        reload = weapon.node_state(weapon.graph.node(65))
        self.assertTrue(reload.top)
        self.assertTrue(weapon.states[75].active)
        self.cast(run, caster, ashe)
        fight = run.match.combat
        self.assertTrue(reload.under)
        self.assertFalse(weapon.states[75].active)
        until = fight.stuns[ashe.body]
        while run.frame < until - 1:
            self.step(run, caster, ashe)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.assertTrue(reload.under)
        self.step(run, caster, ashe)
        self.assertFalse(fight.is_stunned(ashe))
        self.assertTrue(reload.top)
        self.assertTrue(weapon.states[75].active)
        self.assertEqual(weapon.vars[53].value(), 0)
        for _ in range(40):
            self.step(run, caster, ashe)
        self.assertEqual(weapon.vars[53].value(), 1)
        self.assertIsNone(component.fault)

    def test_flashbang_interrupts_running_coach_and_dynamite_before_release(self):
        for graph_id, button, interrupted_cooldown in ((0x1513, 8, 10), (0x14FD, 16, 0)):
            with self.subTest(graph_id=hex(graph_id)):
                run, caster, ashe = self.arena(distance=3)
                self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
                ashe.mover.position[:] = (0, caster.mover.position[1], 3)
                ashe.mover.body = None
                for _ in range(40):
                    self.step(run, caster, ashe)
                fight = run.match.combat
                ability, = (state for instance in ashe.body_script.component.instances.values()
                            if instance.graph.index == graph_id for state in instance.states.values()
                            if isinstance(state, nodes.Ability))
                caster_health = self.health(run, caster)
                position = tuple(ashe.mover.position)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    self.step(run, caster, ashe, cast=16)
                    self.step(run, caster, ashe)
                    self.step(run, caster, ashe, target_buttons=button)
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertFalse(fight.is_stunned(ashe))
                    self.assertEqual(ability.remaining(), 0)
                    self.assertFalse(any(call.args[0] is ashe for call in shots.call_args_list))

                    for _ in range(20):
                        self.step(run, caster, ashe)
                        if fight.is_stunned(ashe):
                            break
                    self.assertTrue(fight.is_stunned(ashe))
                    self.assertEqual(self.health(run, ashe), 175)
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(ability.cur, interrupted_cooldown)
                    if interrupted_cooldown:
                        self.assertGreater(ability.remaining(), 9.9)
                    else:
                        self.assertEqual(ability.remaining(), 0)

                    until = fight.stuns[ashe.body]
                    while run.frame <= until:
                        self.step(run, caster, ashe)
                    self.assertFalse(any(call.args[0] is ashe for call in shots.call_args_list))
                    self.assertEqual(self.health(run, caster), caster_health)
                    self.assertEqual(tuple(ashe.mover.position), position)
                    self.assertFalse(fight.dynamite.grenades)

                    if interrupted_cooldown:
                        self.step(run, caster, ashe, target_buttons=button)
                        for _ in range(20):
                            self.step(run, caster, ashe)
                        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                        self.assertFalse(any(call.args[0] is ashe for call in shots.call_args_list))
                        for _ in range(math.ceil(ability.remaining() / combat.TICK) + 2):
                            self.step(run, caster, ashe)
                        self.assertEqual(ability.remaining(), 0)
                        self.assertFalse(any(call.args[0] is ashe for call in shots.call_args_list))

                    self.step(run, caster, ashe, target_buttons=button)
                    for _ in range(20):
                        self.step(run, caster, ashe)
                    reports = [call for call in shots.call_args_list if call.args[0] is ashe]
                    self.assertEqual(len(reports), 1)
                    self.assertEqual(reports[0].args[2].instance.graph.index, graph_id)
                    if interrupted_cooldown:
                        self.assertLess(self.health(run, caster), caster_health)
                        self.assertNotEqual(tuple(ashe.mover.position), position)
                    else:
                        self.assertEqual(len(fight.dynamite.grenades), 1)

    def test_existing_dynamite_burn_continues_while_owner_is_stunned(self):
        run, caster, ashe = self.arena(distance=3)
        run.match.switch_hero(ashe, heroes()[ASHE])
        ashe.mover.position[:] = (0, caster.mover.position[1], 3)
        ashe.mover.body = None
        self.step(run, caster, ashe, target_buttons=16)
        for _ in range(20):
            self.step(run, caster, ashe)
        engine = run.match.combat.dynamite
        grenade, = engine.grenades
        grenade.position = (caster.mover.position[0], caster.mover.position[1] + 1,
                            caster.mover.position[2])  # isolate established burn lifecycle
        self.assertTrue(engine.detonate(grenade))
        burn = engine.burns[(ashe.body, caster.body)]
        self.cast(run, caster, ashe)
        before = self.health(run, caster)
        pulses = burn.pulses
        until = run.match.combat.stuns[ashe.body]
        while run.frame < until - 1:
            self.step(run, caster, ashe)
        self.assertGreater(burn.pulses, pulses)
        self.assertLess(self.health(run, caster), before)
        self.assertEqual(burn.source, ashe.body)


@unittest.skipUnless(EXTRA.is_file(), "requires actual B.O.B. metadata")
class BobControlTests(unittest.TestCase):
    step = flash_fixtures.FlashbangTests.step
    cast_flash = flash_fixtures.FlashbangTests.cast

    def mccree(self, run, enemy, position):
        run.match.switch_hero(enemy, heroes()[MCCREE])
        enemy.mover.position[:] = position
        enemy.mover.body = None
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 10000)

    def bob_deflect(self):
        run, owner, genji, actor = gun_fixtures.BobTests().cast()
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        genji.mover.position[:] = (0, 1, 8)
        genji.mover.body = None
        self.step(run, genji, actor.bot, cast=16)
        script = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        state = script.node_state(script.graph.node(18))
        self.assertTrue(state.active)
        return run, owner, genji, actor, state

    def test_real_deflect_returns_bob_gun_damage_until_cancel(self):
        run, owner, genji, actor, state = self.bob_deflect()
        fight = run.match.combat
        for _ in range(40):
            self.step(run, genji, actor.bot)
        self.assertGreater(actor.shots, 0)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        bob_health = fight.vitals[actor.bot.entity].total
        self.assertEqual(bob_health, 1200 - 14 * actor.shots)
        self.assertEqual(len(genji.client.messages(combat.HIT)), actor.shots)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        self.step(run, genji, actor.bot, cast=16)
        self.assertFalse(state.active)
        for _ in range(20):
            self.step(run, genji, actor.bot)
        self.assertLess(fight.vitals[genji.body].total, 200)
        self.assertEqual(fight.vitals[actor.bot.entity].total, bob_health)
        self.assertTrue(owner.client.messages(combat.HIT))

    def test_bob_fires_at_transcendence_without_damage_then_hits_after_expiry(self):
        run, owner, zen, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(zen, heroes()[immunity.ZENYATTA]))
        zen.mover.position[:] = (0, 1, 8)
        zen.mover.body = None
        component = zen.body_script.component
        zen.body_script.set_ult_charge(expr.to_float(component.entity_var(338).value()))
        self.step(run, zen, actor.bot, cast=32)
        self.assertTrue(immunity.damage_immune(zen, zen.body))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is zen)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(65):
            self.step(run, zen, actor.bot)
        self.assertTrue(immunity.damage_immune(zen, zen.body))
        self.assertGreater(actor.shots, 0)
        self.assertEqual(actor.target, zen.body)
        self.assertEqual(vitals.total, before)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        for _ in range(375):
            if not immunity.damage_immune(zen, zen.body):
                break
            self.step(run, zen, actor.bot)
        self.assertFalse(immunity.damage_immune(zen, zen.body))
        for _ in range(20):
            self.step(run, zen, actor.bot)
        self.assertLess(vitals.total, before)
        self.assertTrue(owner.client.messages(combat.HIT))

    def test_bob_skips_wraith_target_then_acquires_after_cancel(self):
        run, owner, reaper, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(reaper, heroes()[immunity.REAPER]))
        reaper.mover.position[:] = (0, 1, 8)
        reaper.mover.body = None
        self.step(run, reaper, actor.bot, cast=8)
        self.assertTrue(immunity.wraith_active(reaper))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is reaper)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(45):
            self.step(run, reaper, actor.bot)
        self.assertGreater(run.frame, actor.ready)
        self.assertIsNone(actor.target)
        self.assertEqual(actor.shots, 0)
        self.assertEqual(vitals.total, before)
        self.step(run, reaper, actor.bot, cast=8)
        self.assertFalse(immunity.wraith_active(reaper))
        for _ in range(20):
            self.step(run, reaper, actor.bot)
        self.assertEqual(actor.target, reaper.body)
        self.assertLess(vitals.total, before)
        self.assertTrue(owner.client.messages(combat.HIT))

    def test_bob_charge_crosses_deployed_sigma_barrier(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        fixture = charge_fixtures.BobChargeTests()
        run, owner, sigma = fixture.setup(distance=10)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        sigma.mover.position[:] = (0, 1, 10)
        sigma.mover.body = None
        shields = run.match.combat.sigma_barriers
        shield = shields.actors[sigma.body]
        for _ in range(12):
            self.step(run, sigma, owner, cast=2)
        for _ in range(12):
            self.step(run, sigma, owner)
        self.assertEqual(shield.mode, 3)
        before = AsheBarrierTests.health(self, run, sigma)
        actor = fixture.begin(run, owner)
        x, y, z = actor.bot.position
        crossing = shields.ray(shield, (x, y + 1.55, z), (0, 0, 1), 10 - z)
        self.assertIsNotNone(crossing)
        for _ in range(35):
            if actor.bot.position[2] > z + crossing.distance + 0.3:
                break
            run.step({})
        self.assertGreater(actor.bot.position[2], z + crossing.distance + 0.3)
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(shield.health, 700)
        self.assertEqual(AsheBarrierTests.health(self, run, sigma), before)
        fixture.stop(run, actor)
        self.assertEqual(actor.uppercut_target, sigma.body)
        self.assertEqual(AsheBarrierTests.health(self, run, sigma), before - 120)
        self.assertEqual(shield.health, 700)
        for _ in range(5):
            run.step({})
        self.assertGreater(sigma.mover.velocity[1], 0)

    def test_bob_gun_drains_sigma_barrier_from_outside(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        run, owner, sigma, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        sigma.mover.position[:] = (0, 1, 8)
        sigma.mover.body = None
        shield = run.match.combat.sigma_barriers.actors[sigma.body]
        before = AsheBarrierTests.health(self, run, sigma)
        for _ in range(12):
            self.step(run, sigma, actor.bot, cast=2)
        while run.frame < actor.ready + 35:
            self.step(run, sigma, actor.bot)
        self.assertEqual(shield.mode, 3)
        self.assertGreater(actor.shots, 0)
        self.assertEqual(shield.health, 700 - 14 * actor.shots)
        self.assertEqual(AsheBarrierTests.health(self, run, sigma), before)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_bob_gun_hits_winston_world_barrier_from_outside(self):
        run, owner, winston, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        winston.mover.position[:] = (0, 1, 10)
        winston.mover.body = None
        barrier = AsheBarrierTests.deploy_winston(self, run, winston, actor.bot)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is winston)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(65):
            self.step(run, winston, actor.bot)
        self.assertGreater(actor.shots, 0)
        self.assertEqual(barrier.health, 700 - actor.shots * 14)
        self.assertEqual(vitals.total, before)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_bob_charges_through_winston_shell_then_fires_from_inside(self):
        fixture = charge_fixtures.BobChargeTests()
        run, owner, winston = fixture.setup(distance=10)
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        winston.mover.position[:] = (0, 1, 10)
        winston.mover.body = None
        barrier = AsheBarrierTests.deploy_winston(self, run, winston, owner)
        before = AsheBarrierTests.health(self, run, winston)
        actor = fixture.begin(run, owner)
        entry = barrier.point[2] - barrier.radius
        self.assertLess(actor.bot.position[2], entry)
        for _ in range(30):
            if actor.bot.position[2] > entry + 1:
                break
            run.step({})
        self.assertGreater(actor.bot.position[2], entry + 1)
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(barrier.health, 700)
        self.assertEqual(AsheBarrierTests.health(self, run, winston), before)

        fixture.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(actor.uppercut_target, winston.body)
        after_uppercut = before - 115  # Winston's armor absorbs five of the 120.
        self.assertEqual(AsheBarrierTests.health(self, run, winston), after_uppercut)
        for _ in range(5):
            run.step({})
        self.assertGreater(winston.mover.velocity[1], 0)
        self.assertEqual(barrier.health, 700)
        while actor.shots < 2 and run.frame < actor.ready + 20:
            run.step({})
        self.assertEqual(actor.phase, "gun")
        self.assertEqual(actor.shots, 2)
        self.assertEqual(AsheBarrierTests.health(self, run, winston), after_uppercut)
        self.assertEqual(barrier.health, 700 - 2 * 14)

        # The launch sends Winston outside the shell before the first shots.
        # Falling back through it lets later rounds reach his body.
        while run.frame < actor.ready + 40:
            run.step({})
        self.assertLess(AsheBarrierTests.health(self, run, winston), after_uppercut)
        self.assertEqual(barrier.health, 700 - 2 * 14)

    def test_bob_gun_breaks_projected_barrier_and_credits_its_caster(self):
        run, owner, zarya, target = AsheBarrierTests.projected_arena(self)
        fight = run.match.combat
        for player, position in ((owner, (0, 1, 0)), (zarya, (4, 1, 8)), (target, (0, 1, 8))):
            player.mover.position[:] = position
            player.mover.body = None
        run.match.collision_world = wall_scene(3)
        owner.body_script.set_ult_charge(2240)
        for frame in range(45):
            run.step({owner: (0, 0, 32 if frame < 5 else 0)})
        actor, = fight.companions.active.values()
        self.assertEqual(actor.stopped_reason, "wall")
        run.match.collision_world = flat_world(1)
        while run.frame < actor.ready - 2:
            run.step({})
        cast, inst = AsheBarrierTests.projected_input(run, zarya, target)
        run.step({zarya: (*cast, 16)})
        phase = inst.states[10]
        self.assertTrue(phase.active)
        body = next(item for item in fight._targets() if item.owner is target)
        vitals = fight.vitals_of(body)
        before = vitals.total
        for _ in range(125):
            run.step({})
            self.assertEqual(vitals.total, before)
            if not phase.active:
                break
            if actor.shots:
                self.assertEqual(fight.personal_barriers[target.body][2], 200 - 14 * actor.shots)
        self.assertFalse(phase.active)
        self.assertEqual(actor.shots, 15)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        AsheBarrierTests.assert_energy_credit(self, zarya, 4, actor.bot.entity, 40)
        for _ in range(15):
            run.step({})
        self.assertLess(vitals.total, before)
        self.assertTrue(owner.client.messages(combat.HIT))

    def test_charge_routes_to_projected_barrier_and_credits_its_caster(self):
        run, owner, zarya, target = AsheBarrierTests.projected_arena(self)
        for player, position in ((owner, (0, 1, 0)), (zarya, (4, 1, 8)), (target, (0, 1, 8))):
            player.mover.position[:] = position
            player.mover.body = None
        run.step({})
        cast, inst = AsheBarrierTests.projected_input(run, zarya, target)
        run.step({zarya: (*cast, 16)})
        self.assertTrue(inst.states[10].active)
        before = AsheBarrierTests.health(self, run, target)
        owner.body_script.set_ult_charge(2240)
        fixture = charge_fixtures.BobChargeTests()
        actor = fixture.begin(run, owner)
        fixture.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertIsNone(actor.uppercut_target)
        self.assertEqual(run.match.combat.personal_barriers[target.body][2], 80)
        AsheBarrierTests.assert_energy_credit(self, zarya, 120, actor.bot.entity, 24)
        for _ in range(5):
            run.step({})
        self.assertEqual(AsheBarrierTests.health(self, run, target), before)
        self.assertEqual(target.mover.velocity[1], 0)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_charge_breaks_personal_barrier_without_body_damage_or_launch(self):
        fixture = charge_fixtures.BobChargeTests()
        run, owner, zarya = fixture.setup(distance=8)
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.mover.position[:] = (0, 1, 8)
        zarya.mover.body = None
        phase = AsheBarrierTests.cast_barrier(self, run, zarya, owner)
        before = AsheBarrierTests.health(self, run, zarya)
        world = zarya.body_script.component.world
        ruleset = world.ruleset
        with patch.object(world, "ruleset", side_effect=lambda key: 100 if key == 0x1D43 else ruleset(key)):
            actor = fixture.begin(run, owner)
            fixture.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertIsNone(actor.uppercut_target)
        self.assertFalse(phase.active)
        self.assertNotIn(zarya.body, run.match.combat.personal_barriers)
        AsheBarrierTests.assert_energy_credit(self, zarya, 100, actor.bot.entity, 20)
        for _ in range(5):
            run.step({})
        self.assertEqual(AsheBarrierTests.health(self, run, zarya), before)
        self.assertEqual(zarya.mover.velocity[1], 0)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_bob_gun_breaks_zarya_personal_barrier_before_body_damage(self):
        run, owner, zarya, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.mover.position[:] = (0, 1, 8)
        zarya.mover.body = None
        while run.frame < actor.ready - 2:
            self.step(run, zarya, actor.bot)
        phase = AsheBarrierTests.cast_barrier(self, run, zarya, actor.bot)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is zarya)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(125):
            self.step(run, zarya, actor.bot)
            self.assertEqual(vitals.total, before)
            if not phase.active:
                break
            if actor.shots:
                self.assertEqual(fight.personal_barriers[zarya.body][2], 200 - 14 * actor.shots)
        self.assertFalse(phase.active)
        self.assertEqual(actor.shots, 15)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        AsheBarrierTests.assert_energy_credit(self, zarya, 4, actor.bot.entity, 40)
        for _ in range(15):
            self.step(run, zarya, actor.bot)
        self.assertLess(vitals.total, before)
        self.assertTrue(owner.client.messages(combat.HIT))

    def test_bob_gun_damage_tracks_fortify_expiry(self):
        run, owner, orisa, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(orisa, heroes()[immunity.ORISA]))
        orisa.mover.position[:] = (0, 1, 8)
        orisa.mover.body = None
        self.step(run, orisa, actor.bot, cast=8)
        self.assertTrue(immunity.fortify_active(orisa, orisa.body))
        verified = set()
        for _ in range(320):
            before = AsheBarrierTests.health(self, run, orisa)
            reports = len(owner.client.messages(combat.HIT))
            self.step(run, orisa, actor.bot)
            if len(owner.client.messages(combat.HIT)) == reports:
                continue
            protected = immunity.fortify_active(orisa, orisa.body)
            self.assertEqual(len(owner.client.messages(combat.HIT)), reports + 1)
            self.assertAlmostEqual(AsheBarrierTests.health(self, run, orisa),
                                   before - (4.2 if protected else 9), places=4)
            verified.add(protected)
            if verified == {False, True}:
                break
        self.assertEqual(verified, {False, True})

    def test_grasp_absorbs_bob_gun_until_state_ends(self):
        run, owner, sigma, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        sigma.mover.position[:] = (0, 1, 8)
        sigma.mover.body = None
        self.step(run, sigma, actor.bot, cast=8)
        script = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        state = script.node_state(script.graph.node(7))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is sigma)
        vitals = fight.vitals_of(target)
        before = vitals.total
        self.assertTrue(state.active)
        for _ in range(40):
            self.step(run, sigma, actor.bot)
        self.assertTrue(state.active)
        self.assertGreater(actor.shots, 0)
        self.assertEqual(vitals.total, before)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        for _ in range(126):
            if not state.active:
                break
            self.step(run, sigma, actor.bot)
        self.assertFalse(state.active)
        granted = vitals.total
        self.assertAlmostEqual(granted, before + 14 * actor.shots * 0.6, places=4)
        for _ in range(20):
            self.step(run, sigma, actor.bot)
        self.assertLess(vitals.total, granted)
        self.assertTrue(owner.client.messages(combat.HIT))

    def test_lethal_reflection_retires_bob_and_credits_genji(self):
        run, owner, genji, actor, _state = self.bob_deflect()
        fight = run.match.combat
        vitals = fight.vitals[actor.bot.entity]
        vitals.damage(1186)
        for _ in range(40):
            self.step(run, genji, actor.bot)
            if actor.bot.dead:
                break
        self.assertTrue(actor.bot.dead)
        self.assertEqual(vitals.killer, genji.body)
        self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        self.step(run, genji, actor.bot)
        self.assertNotIn(owner.body, fight.companions.active)
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(any(entity is actor.bot for _, entity in fight.respawns))

    def test_real_flashbang_pauses_bob_gun_and_resumes_at_same_lifetime(self):
        run, _owner, enemy, actor = gun_fixtures.BobTests().cast()
        self.mccree(run, enemy, (0, 1, 5))
        while run.frame < actor.ready + 20:
            run.step({})
        self.assertGreater(actor.shots, 0)
        deadline = actor.until
        self.cast_flash(run, enemy, actor.bot)
        fight = run.match.combat
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1175)
        health = fight.vitals[enemy.body].total
        until = fight.stuns[actor.bot.entity]
        while run.frame < until - 1:
            self.step(run, enemy, actor.bot)
            self.assertEqual(fight.vitals[enemy.body].total, health)
            self.assertNotIn(20, actor.bot.body_script.component.held)
        for _ in range(30):
            self.step(run, enemy, actor.bot)
        self.assertLess(fight.vitals[enemy.body].total, health)
        self.assertEqual(actor.until, deadline)

    def test_bob_fire_resumes_on_flashbang_expiry_across_skipped_ticks(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, enemy, actor = gun_fixtures.BobTests().cast()
                self.mccree(run, enemy, (0, 1, 5))
                while run.frame < actor.ready + 20:
                    run.step({})
                self.cast_flash(run, enemy, actor.bot)
                fight = run.match.combat
                until = fight.stuns[actor.bot.entity]
                deadline = actor.until
                health = fight.vitals[enemy.body].total
                credit = owner.stats.value(stats.HERO_DAMAGE)
                hit_count = len(owner.client.messages(combat.HIT))
                while run.frame < until - 10:
                    self.step(run, enemy, actor.bot)
                self.assertFalse(actor.bot.firing)
                self.assertEqual(fight.vitals[enemy.body].total, health)

                if skipped:
                    run.frame = until + 18
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    while run.frame < until + 18:
                        self.step(run, enemy, actor.bot)
                hits = owner.client.messages(combat.HIT)[hit_count:]
                results.append([(hit["+0x84"] - until, hit["+0x78"]["+0x4"]) for hit in hits])
                self.assertNotIn(actor.bot.entity, fight.stuns)
                self.assertEqual(actor.until, deadline)
                self.assertEqual(fight.vitals[enemy.body].total, health - 42)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), credit + 42)
        self.assertEqual(results, [[(0, 14), (7, 14), (15, 14)]] * 2)

    def test_real_flashbang_pauses_charge_without_false_wall_then_resumes(self):
        fixture = charge_fixtures.BobChargeTests()
        run, owner, enemy = fixture.setup(distance=8)
        self.mccree(run, enemy, (0, 1, 8))
        actor = fixture.begin(run, owner)
        self.cast_flash(run, enemy, actor.bot)
        self.assertEqual(actor.phase, "charge")
        self.assertIsNone(actor.stopped_reason)
        fight = run.match.combat
        until = fight.stuns[actor.bot.entity]
        position = actor.bot.position
        while run.frame < until - 1:
            self.step(run, enemy, actor.bot)
            self.assertEqual(actor.bot.position, position)
            self.assertIsNone(actor.stopped_reason)
        for _ in range(5):
            self.step(run, enemy, actor.bot)
        self.assertGreater(actor.bot.position[2], position[2])
        self.assertNotEqual(actor.stopped_reason, "wall")


if __name__ == "__main__":
    unittest.main()
