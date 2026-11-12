"""Executed Sound Barrier graph, health absorption and lifetime (reconstructed host)."""

import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_lucio_combat as fixtures
import test_lucio_pve as pve
from test_game_combat import aim

from ow174.game import combat, deflection, absorption as grasp, lucio, wraith, sound_barrier
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import IN_AIR
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(graph.graph(0x073B), "requires actual073B status supplement")
class SoundBarrierTests(unittest.TestCase):
    def scenario(self, charged=True):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        source.body_script.ult_seconds = 0  # isolate explicit charge from placeholder passive rate
        component = source.body_script.component
        self.assertEqual(component.entity_var(ULT_COST).value(), 2940)
        if charged:
            source.body_script.set_ult_charge(2940)
        return helper, run, source, enemy, ally

    def cast(self, run, source):
        run.step({source: (0, 0, 32)})  # authored ultimate logical button37
        for _ in range(41):
            run.step({})

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))

    def test_reaper_fire_recovers_after_wraith_and_hits_sound_barrier(self):
        def fire(run, enemy, direction, frames):
            for i in range(frames):
                run.step({enemy: (*direction, FIRE if i == 0 else 0)})

        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                _, run, source, enemy, ally = self.scenario()
                run.match.switch_hero(enemy, heroes()[wraith.HERO])
                enemy.mover.position[:] = (0, 1, 4)
                ally.mover.position[:] = (20, 1, 4)
                fight = run.match.combat
                direction = aim(fight._eye(enemy), (0, 1.95, 0))
                vitals = self.vitals(run, source)

                run.step({enemy: (*direction, 8)})
                self.assertTrue(wraith.protects(enemy, fight.tick))
                fire(run, enemy, direction, 5)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(enemy.client.messages(combat.HIT))
                for _ in range(200):
                    run.step({enemy: (*direction, 0)})
                self.assertFalse(wraith.protects(enemy, fight.tick))
                instance = wraith.instance_for(enemy)
                recovery = next(state for state in instance.states.values() if state.node.pos == 13)
                self.assertTrue(recovery.active)
                self.assertEqual(recovery.ms, 400)
                fire(run, enemy, direction, 35)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(enemy.client.messages(combat.HIT))
                self.assertFalse(recovery.active)

                if shielded:
                    self.cast(run, source)
                    self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 750)
                fire(run, enemy, direction, 35)
                self.assertEqual(len(enemy.client.messages(combat.HIT)), 1)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200 if shielded else 194)
                if shielded:
                    self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 674)

    def test_sigma_hyperspheres_damage_lucio_and_sound_barrier(self):
        for mode in ("bare", "shielded", "grasp_active", "grasp_expired"):
            with self.subTest(mode=mode):
                _, run, source, enemy, ally = self.scenario()
                run.match.switch_hero(enemy, heroes()[grasp.HERO])
                enemy.mover.position[:] = (0, 1, 4)
                ally.mover.position[:] = (20, 1, 4)
                fight = run.match.combat
                direction = aim(fight._eye(enemy), (0, 1.95, 0))
                vitals = self.vitals(run, source)
                if mode == "shielded":
                    self.cast(run, source)
                    self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 750)
                if mode in ("grasp_active", "grasp_expired"):
                    run.step({enemy: (*direction, 8)})
                    instance = next(i for i in enemy.body_script.component.instances.values()
                                    if i.graph.index == grasp.GRAPH)
                    self.assertIsNotNone(grasp.window(instance, fight.tick))
                    for _ in range(205 if mode == "grasp_expired" else 4):
                        run.step({enemy: (*direction, 0)})
                    self.assertEqual(grasp.window(instance, fight.tick) is not None, mode == "grasp_active")

                with (patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired,
                      patch.object(fight, "_damage", wraps=fight._damage) as damage):
                    for i in range(50):
                        run.step({enemy: (*direction, FIRE if i == 0 else 0)})

                launches = [call.args[3] for call in fired.call_args_list]
                amounts = [call.args[2] for call in damage.call_args_list]
                if mode == "grasp_active":
                    self.assertEqual((launches, amounts, vitals.total), ([], [], 200))
                    self.assertFalse(enemy.client.messages(combat.HIT))
                    continue
                self.assertEqual(launches, [0x0400000000002D35, 0x04000000000032B4])
                self.assertEqual(amounts, [25, 30, 25, 30])
                self.assertEqual(len(enemy.client.messages(combat.HIT)), 4)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200 if mode == "shielded" else 90)
                if mode == "shielded":
                    self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 540)

    def test_sigma_hyperspheres_respect_wall_range_and_team_with_sound_barrier(self):
        for mode in ("wall", "range", "miss", "ally", "ffa"):
            with self.subTest(mode=mode):
                _, run, source, enemy, ally = self.scenario()
                run.match.switch_hero(enemy, heroes()[grasp.HERO])
                enemy.mover.position[:] = (0, 1, 30 if mode == "range" else 4)
                ally.mover.position[:] = (20, 1, 4)
                if mode in ("ally", "ffa"):
                    enemy.team = source.team
                if mode == "ffa":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                self.cast(run, source)
                vitals = self.vitals(run, source)
                shield = vitals.extra[sound_barrier.POOL_KEY]
                self.assertEqual(shield.current, 750)
                if mode == "wall":
                    run.match.collision_world = World([
                        (-5, 0, 2, 5, 0, 2, 5, 6, 2), (-5, 0, 2, 5, 6, 2, -5, 6, 2),
                    ])
                fight = run.match.combat
                direction = (0, 0) if mode == "miss" else aim(fight._eye(enemy), (0, 1.95, 0))
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for i in range(90):
                        run.step({enemy: (*direction, FIRE if i == 0 else 0)})
                self.assertEqual([call.args[3] for call in fired.call_args_list],
                                 [0x0400000000002D35, 0x04000000000032B4])
                self.assertEqual(len(enemy.client.messages(combat.HIT)), 4 if mode == "ffa" else 0)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
                self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now,
                                 750 - 180 - (110 if mode == "ffa" else 0))

    def test_genji_reflection_credits_genji_and_drains_sound_barrier(self):
        for mode in ("front", "rear", "expired", "wall"):
            with self.subTest(mode=mode):
                helper, run, source, enemy, ally = self.scenario()
                run.match.switch_hero(enemy, heroes()[deflection.GENJI])
                enemy.mover.position[:] = (0, 1, 4)
                run.match.switch_hero(ally, heroes()[fixtures.LUCIO])
                ally.mover.position[:] = (3, 1, 0)
                ally.body_script.set_ult_charge(2940)
                self.cast(run, ally)
                self.assertEqual(self.vitals(run, source).extra[sound_barrier.POOL_KEY].now, 750)
                ally.mover.position[:] = (20, 1, 4)
                fight = run.match.combat
                attack_aim = aim(fight._eye(source), (0, 1.95, 4))
                guard_aim = (0, 0) if mode == "rear" else aim(fight._eye(enemy), fight._eye(source))
                run.step({enemy: (*guard_aim, 16)})
                for _ in range(164 if mode == "expired" else 4):
                    run.step({enemy: (*guard_aim, 0)})
                instance = next(i for i in enemy.body_script.component.instances.values()
                                if i.graph.index == deflection.GRAPH)
                self.assertEqual(deflection.window(instance, fight.tick) is not None, mode != "expired")
                if mode == "wall":
                    run.match.collision_world = World([
                        (-5, 0, 2, 5, 0, 2, 5, 6, 2), (-5, 0, 2, 5, 6, 2, -5, 6, 2),
                    ])
                vitals = self.vitals(run, source)
                shield_before = vitals.extra[sound_barrier.POOL_KEY].now
                enemy_before = helper.health(run, enemy)
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for i in range(45):
                        run.step({source: (*attack_aim, FIRE if i == 0 else 0), enemy: (*guard_aim, 0)})
                self.assertEqual([call.args[3] for call in fired.call_args_list], [0x04000000000005FF] * 4)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
                self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now,
                                 shield_before - 90 - (80 if mode == "front" else 0))
                direct_hit = mode in ("rear", "expired")
                self.assertEqual(enemy_before - helper.health(run, enemy), 80 if direct_hit else 0)
                self.assertEqual(len(enemy.client.messages(combat.HIT)), 4 if mode == "front" else 0)
                self.assertEqual(len(source.client.messages(combat.HIT)), 4 if direct_hit else 0)

    def test_self_cast_weapon_recovery_at_authored_wait_boundary(self):
        for button, offset, expected_damage in (
            (FIRE, -1, 0), (FIRE, 0, 80),
            (SECONDARY, -1, 0), (SECONDARY, 0, 0), (SECONDARY, 1, 25),
            (0x800, -9, 0), (0x800, -8, 30),
        ):
            with self.subTest(button=button, offset=offset):
                helper, run, source, enemy, ally = self.scenario()
                ally.mover.position[:] = (20, 1, 4)
                if button == 0x800:
                    enemy.mover.position[:] = (0, 1, 1.7)
                self.cast(run, source)
                component = source.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x073A)
                wait = next(state for state in instance.states.values() if state.node.pos == 11)
                self.assertTrue(wait.active)
                self.assertEqual(wait.ms, 830)
                deadline = wait.start + wait.ms
                frame = deadline // component.frame_ms + offset
                while run.frame + 1 < frame:
                    run.step({})

                before = helper.health(run, enemy)
                shield = self.vitals(run, source).extra[sound_barrier.POOL_KEY].now
                fight = run.match.combat
                hit_frame = None
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    run.step({source: (0, 0, button)})
                    self.assertEqual(wait.active, offset < 0)
                    for _ in range(44):
                        if hit_frame is None and helper.health(run, enemy) < before:
                            hit_frame = run.frame
                        run.step({})

                self.assertEqual(before - helper.health(run, enemy), expected_damage)
                launches = [call.args[3] for call in fired.call_args_list]
                self.assertEqual(launches, [0x04000000000005FF] * 4 if expected_damage == 80 else [])
                if expected_damage == 30:
                    self.assertEqual(hit_frame, deadline // component.frame_ms)
                remaining = self.vitals(run, source).extra[sound_barrier.POOL_KEY].now
                self.assertEqual(remaining, shield - 90)

    def test_charge_gate_cast_delay_and_team_shields(self):
        helper, run, source, enemy, ally = self.scenario(charged=False)
        self.cast(run, source)
        self.assertEqual(helper.health(run, source), 200)
        self.assertEqual(helper.health(run, ally), 200)
        self.assertFalse(run.match.combat.sound_barriers.shielded)
        source.body_script.set_ult_charge(2940)
        run.step({source: (0, 0, 32)})
        self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(40):
            run.step({})
            self.assertEqual(helper.health(run, ally), 200)
        run.step({})
        self.assertEqual(helper.health(run, source), 950)
        self.assertEqual(helper.health(run, ally), 950)
        self.assertEqual(helper.health(run, enemy), 200)

    def test_scheduled_barrier_separates_projectile_hits_before_and_after_slam(self):
        def exercise(schedule, lethal):
            _, run, source, enemy, ally = self.scenario()
            run.step({source: (0, 0, 32)})
            cast = run.frame
            for _ in range(39):
                run.step({})
            fight = run.match.combat
            source_vitals = self.vitals(run, source)
            if lethal:
                source_vitals.damage(199)

            # Use the authored Helix direct damage with positioned in-flight
            # fixtures. They arrive one frame before and after the slam.
            helix = enemy.body_script.component.instances[11].state(16)
            direct = combat.hit_def(helix, helix.node.field("m_6396149F"))
            self.assertEqual(direct.amount, 40)
            for distance in (1.0, 3.0):
                fight.rockets.append(combat.Rocket(
                    enemy, enemy.team, (0, 1.8, distance), (0, 0, -1), 62.5,
                    1.0, direct, None, last_tick=run.frame,
                ))

            def command(frame):
                for player in run.players:
                    player.take_commands([Command(frame)])

            def world(frame):
                run.frame = frame
                run.match.update(frame * combat.TICK, frame)

            def shield(player):
                pool = self.vitals(run, player).extra.get(sound_barrier.POOL_KEY)
                return pool.now if pool is not None else 0

            if schedule == "contiguous":
                for frame in range(cast + 40, cast + 71):
                    command(frame)
                    world(frame)
            else:
                frames = (cast + 70,) if schedule == "sparse" else range(cast + 40, cast + 71)
                for frame in frames:
                    command(frame)
                self.assertEqual(shield(ally), 0)
                if schedule == "split":
                    world(cast + 40)
                    self.assertEqual(shield(ally), 0)
                    world(cast + 41)
                    self.assertEqual(shield(ally), 750)
                world(cast + 70)

            self.assertFalse(fight.sound_barriers.pending)
            self.assertEqual(source_vitals.now[combat.HEALTH_POOL], 0 if lethal else 160)
            self.assertEqual(shield(source), 0 if lethal else 652)
            self.assertEqual(shield(ally), 0 if lethal else 692)
            self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        cases = (("contiguous", False), ("split", False), ("sparse", False), ("sparse", True))
        for schedule, lethal in cases:
            with self.subTest(schedule=schedule, lethal=lethal):
                exercise(schedule, lethal)

    def test_sparse_barrier_cast_observes_slam_even_after_recovery_ends(self):
        for gap, expected in ((130, 572), (300, 232), (450, 0)):
            with self.subTest(gap=gap):
                _, run, source, enemy, ally = self.scenario()
                run.step({source: (0, 0, 32)})
                cast = run.frame
                run.frame += gap
                for player in run.players:
                    player.take_commands([Command(run.frame)])
                barriers = run.match.combat.sound_barriers
                self.assertEqual([round(grant.at / combat.TICK) for grant in barriers.pending], [cast + 41])
                self.assertFalse(barriers.shielded)
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(barriers.pending)
                for player in (source, ally, enemy):
                    vitals = self.vitals(run, player)
                    pool = vitals.extra.get(sound_barrier.POOL_KEY)
                    amount = pool.now if pool is not None else 0
                    self.assertEqual(amount, expected if player is not enemy else 0)
                self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_barrier_decays_before_damage_on_first_retained_world_frame(self):
        _, run, source, enemy, ally = self.scenario()
        run.step({source: (0, 0, 32)})
        cast = run.frame
        run.frame += 450
        for player in run.players:
            player.take_commands([Command(run.frame)])
        fight = run.match.combat
        self.assertEqual([grant.tick for grant in fight.sound_barriers.pending], [cast + 41])

        # Position two flights at the boundary of the retained combat
        # window. Their authored direct damage lands on its first frame.
        helix = enemy.body_script.component.instances[11].state(16)
        direct = combat.hit_def(helix, helix.node.field("m_6396149F"))
        before_retained = run.frame - combat.COMBAT_CATCH_UP
        for _ in range(2):
            fight.rockets.append(combat.Rocket(
                enemy, enemy.team, (0, 1.8, 1), (0, 0, -1), 62.5,
                1.0, direct, None, last_tick=before_retained,
            ))
        run.match.update(run.frame * combat.TICK, run.frame)
        source_vitals = self.vitals(run, source)
        self.assertFalse(source_vitals.extra)
        self.assertFalse(self.vitals(run, ally).extra)
        # 60 shield remains before the two 40-point impacts.
        self.assertEqual(source_vitals.now[combat.HEALTH_POOL], 180)

    def test_existing_barrier_decay_precedes_catchup_projectile_impact(self):
        def exercise(batched, base, distance, expected, *, new=False):
            _, run, source, enemy, ally = self.scenario()
            vitals = self.vitals(run, source)
            vitals.damage(200 - base)
            self.cast(run, source)
            fight = run.match.combat

            def world(frames):
                run.frame += frames
                run.match.update(run.frame * combat.TICK, run.frame)

            world(345)
            self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 60)
            self.assertFalse(fight.sound_barriers.pending)
            start = run.frame
            helix = enemy.body_script.component.instances[11].state(16)
            direct = combat.hit_def(helix, helix.node.field("m_6396149F"))
            if new and not batched:
                for _ in range(39):
                    world(1)
            for _ in range(1 if new else 2):
                fight.rockets.append(combat.Rocket(
                    enemy, enemy.team, (0, 1.8, distance), (0, 0, -1), 62.5,
                    1.0, direct, None, last_tick=None if new else start,
                ))
            if batched:
                world(40)
            else:
                for _ in range(start + 40 - run.frame):
                    world(1)
            self.assertFalse(vitals.extra)
            self.assertFalse(self.vitals(run, ally).extra)
            self.assertFalse(fight.rockets)
            self.assertEqual(vitals.now[combat.HEALTH_POOL], expected)
            self.assertEqual(vitals.death_tick, start + 15 if expected == 0 else None)

        for batched in (False, True):
            with self.subTest(batched=batched):
                exercise(batched, 200, 15, 152)
                exercise(batched, 30, 15, 0)
                exercise(batched, 200, 33, 120)
                exercise(batched, 200, 1, 160, new=True)

    def test_grasp_shield_decay_precedes_catchup_primary_impact(self):
        for schedule, expected in (((1,) * 21, 10.4), ((1, 20), 10.4), ((1, 80), 11.44)):
            with self.subTest(gap=max(schedule)):
                _, run, source, sigma, ally = self.scenario(charged=False)
                run.match.switch_hero(sigma, heroes()[grasp.HERO])
                sigma.mover.position[:] = (0, 1, 4)
                ally.mover.position[:] = (20, 1, 4)
                fight = run.match.combat
                vitals = self.vitals(run, sigma)
                base = vitals.total
                attack = aim(fight._eye(source), (0, 1.95, 4))
                vitals.grant_temporary_shields(grasp.SHIELD_KEY, 10)
                start = run.frame * combat.TICK
                fight.grasp_shields[sigma.body] = ((0, 1), start, start, 5)
                run.step({source: (*attack, FIRE)})

                for frames in schedule:
                    run.frame += frames
                    run.match.update(run.frame * combat.TICK, run.frame)
                charge = source.body_script.component.entity_var(ULT_CHARGE)
                self.assertFalse(vitals.extra)
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(vitals.total, base - expected, places=5)
                self.assertAlmostEqual(charge.value(), expected, places=5)

    def test_temporary_shields_absorb_before_base_pools_and_cannot_be_healed(self):
        _, run, source, enemy, ally = self.scenario()
        fight = run.match.combat
        vitals = combat.Vitals(ally.body, 200, armour=50, shields=100)
        fight.vitals[ally.body] = vitals
        self.cast(run, source)
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 300, False, None, run.frame)
        self.assertEqual(vitals.total, 800)
        self.assertEqual(vitals.now, {combat.HEALTH_POOL: 200, combat.ARMOUR_POOL: 50,
                                      combat.SHIELDS_POOL: 100})
        self.assertEqual(vitals.heal(100), 0)
        fight._damage(enemy, target, 500, False, None, run.frame)
        self.assertEqual(vitals.total, 300)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 50)
        self.assertEqual(vitals.heal(100), 50)
        self.assertFalse(vitals.extra)

    def test_expiry_clears_wire_pool_without_damaging_or_healing_base_health(self):
        helper, run, source, _, ally = self.scenario()
        self.cast(run, source)
        vitals = self.vitals(run, ally)
        active = vitals.component(vitals.ids)[51][combat.SHIELDS_POOL * 16 + 1]
        pool_id, maximum, current, kind, _, ordinary, _, _ = struct.unpack("<IffI4B", active)
        self.assertGreater(pool_id, 0)
        self.assertEqual((maximum, current, kind, ordinary), (750, 750, 2, 0))
        for _ in range(375):
            run.step({})
        self.assertEqual(helper.health(run, ally), 200)
        self.assertFalse(vitals.extra)
        cleared = vitals.component(vitals.ids)[51][combat.SHIELDS_POOL * 16 + 1]
        self.assertEqual(struct.unpack("<I", cleared[:4])[0], 0)
        self.assertFalse(run.match.combat.sound_barriers.shielded)

    def test_input_replay_and_repress_without_charge_do_not_refresh_shields(self):
        _, run, source, _, ally = self.scenario()
        self.cast(run, source)
        for _ in range(5):
            source.take_commands([Command(run.frame, buttons=32)])
        for offset in range(10):
            run.step({source: (0, 0, 32 if offset % 2 == 0 else 0)})
        self.assertEqual(self.vitals(run, ally).extra[sound_barrier.POOL_KEY].now, 730)

    def test_ffa_range_and_real_wall_exclude_other_receivers(self):
        for policy in ("ffa", "range", "wall"):
            with self.subTest(policy=policy):
                helper, run, source, _, ally = self.scenario()
                if policy == "ffa":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                elif policy == "range":
                    ally.mover.position[:] = (0, 1, 32)
                else:
                    run.match.collision_world = World([
                        (-50, 1, -50, -50, 1, 50, 50, 1, 50),
                        (-50, 1, -50, 50, 1, 50, 50, 1, -50),
                        (-5, 1, 2, 5, 1, 2, 5, 6, 2),
                        (-5, 1, 2, 5, 6, 2, -5, 6, 2),
                    ])
                self.cast(run, source)
                self.assertEqual(helper.health(run, ally), 200)
                self.assertEqual(helper.health(run, source), 950)

    def test_airborne_slam_waits_for_physics_landing(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                helper, run, source, _, ally = self.scenario()
                source.mover.position[:] = (0, 100, 0)
                run.step({source: (0, 0, 32)})
                self.assertTrue(source.mover.flags_word & IN_AIR)
                if sparse:
                    run.frame += 130
                    for player in run.players:
                        player.take_commands([Command(run.frame)])
                    self.assertFalse(run.match.combat.sound_barriers.pending)
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(80):
                        run.step({})
                self.assertGreater(source.mover.air_ticks, 0)
                self.assertEqual(helper.health(run, ally), 200)
                for _ in range(400):
                    if source.mover.air_ticks == 0:
                        break
                    run.step({})
                self.assertEqual(source.mover.air_ticks, 0)
                self.assertEqual(helper.health(run, ally), 950)

    def test_death_before_slam_prevents_grant_but_death_after_slam_keeps_allied_shields(self):
        for before in (True, False):
            with self.subTest(before_slam=before):
                helper, run, source, enemy, ally = self.scenario()
                if before:
                    run.step({source: (0, 0, 32)})
                else:
                    self.cast(run, source)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is source)
                fight._damage(enemy, target, 2000, False, None, run.frame)
                for _ in range(60 if before else 1):
                    run.step({})
                self.assertTrue(fight.is_dead(source))
                self.assertEqual(helper.health(run, ally), 200 if before else 948)

    def test_same_caster_can_grant_shields_on_a_second_charged_cast(self):
        _, run, source, _, ally = self.scenario()
        self.cast(run, source)
        fight = run.match.combat
        first = fight.sound_barriers.shielded[ally.body]
        for _ in range(375):
            run.step({})
        vitals = self.vitals(run, ally)
        self.assertNotIn(sound_barrier.POOL_KEY, vitals.extra)

        # The authored ability can outlast the reconstructed shield pool.
        ability, = source.body_script.ultimates
        for _ in range(115):
            if not ability.flags & ability.RUNNING:
                break
            run.step({})
        self.assertFalse(ability.flags & ability.RUNNING)

        source.body_script.set_ult_charge(2940)
        run.step({source: (0, 0, 32)})
        self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(40):
            run.step({})
            self.assertNotIn(sound_barrier.POOL_KEY, vitals.extra)
        run.step({})
        self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 750)
        self.assertIsNot(fight.sound_barriers.shielded[ally.body], first)

    def test_two_casts_refresh_one_pool_instead_of_stacking(self):
        helper, run, source, second, ally = self.scenario()
        run.match.switch_hero(second, heroes()[fixtures.LUCIO])
        second.team = 0
        second.mover.position[:] = (2, 1, 0)
        second.body_script.ult_seconds = 0
        second.body_script.set_ult_charge(2940)
        run.step({source: (0, 0, 32), second: (0, 0, 32)})
        for _ in range(41):
            run.step({})
        self.assertEqual(helper.health(run, ally), 950)
        self.assertEqual(len(self.vitals(run, ally).extra), 1)

    def test_caster_body_retirement_preserves_ally_buff_and_receiver_retirement_clears_it(self):
        helper, run, source, _, ally = self.scenario()
        self.cast(run, source)
        run.match.switch_hero(source, heroes()[SOLDIER])
        run.step({})
        self.assertEqual(helper.health(run, ally), 948)
        old_body = ally.body
        run.match.switch_hero(ally, heroes()[fixtures.LUCIO])
        run.step({})
        self.assertEqual(helper.health(run, ally), 200)
        self.assertNotIn(old_body, run.match.combat.sound_barriers.shielded)

    @unittest.skipUnless(pve.CATALOG and pve.BUNDLE, "requires real five-type NPC resources")
    def test_real_npc_allies_receive_shields_and_enemies_do_not(self):
        fixture = pve.LucioPveTests()
        fixture.setUpClass()
        try:
            for definition in fixture.definitions.values():
                for team in (0, 1):
                    with self.subTest(npc=definition.name, team=team):
                        helper, run, source, bot = fixture.scenario(definition, team=team)
                        source.body_script.ult_seconds = 0
                        source.body_script.set_ult_charge(2940)
                        before = helper.health(run, bot)
                        self.cast(run, source)
                        self.assertEqual(helper.health(run, bot) - before, 750 if team == 0 else 0)
        finally:
            fixture.tearDownClass()
