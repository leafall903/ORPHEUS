"""Actual104319 NPC graphs into Ball; manual controller/contact policies are reconstructed."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_mccree_flashbang as flash_tests
import test_wrecking_ball_grapple as grapple_tests
import test_wrecking_ball_pve as pve_tests
from test_game_combat import aim
from test_wrecking_ball_absorb import use_mech_fixture
from test_wrecking_ball_fireball import prepare_swing, relocate
from test_wrecking_ball_phase import ZENYATTA
from test_wrecking_ball_piledriver import cast, lift, pile_instance

from ow174.game import (
    assassin,
    ball,
    barrier_geometry,
    barriers,
    combat,
    deflection,
    npc,
    npc_charge,
    npc_control,
    npc_melee,
    npc_reposition,
    observers,
    stats,
)
from ow174.game.collision import StaticCollisionProvider, World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class BallIncomingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pve_tests.BallPveTests.setUpClass()
        cls.fixture = pve_tests.BallPveTests()

    @classmethod
    def tearDownClass(cls):
        pve_tests.BallPveTests.tearDownClass()

    def definition(self, name):
        return next(d for d in self.fixture.catalog.values() if d.name == name)

    def arena(self, name='Assassin', *, distance=1.5):
        return self.fixture.scenario(self.definition(name), distance=distance)

    def engage(self, run, player, target, *, shield=True):
        angles = aim(run.match.combat._eye(player),
                     (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        run.step({player: (*angles, 16 if shield else 0)})
        self.assertEqual(npc_control.start(run.match, player), target.entity)

    def wait_pin(self, run, player):
        for _ in range(150):
            run.step({})
            if run.match.combat.is_stunned(player):
                return
        self.fail('actual1157 never controlled the engaged Ball')

    def sniper_reposition_arena(self, *, wall=False):
        run, player, bot, target, own = self.arena('Sniper')
        match = run.match
        x, y, z = player.mover.position
        ground = flat_world(y)
        if wall:
            triangles = [tuple(ground.data[i:i + 9]) for i in range(0, len(ground.data), 13)]
            wall_z = z + 3
            triangles.extend([
                (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 1.1, wall_z),
                (x - 5, y, wall_z, x + 5, y + 1.1, wall_z, x - 5, y + 1.1, wall_z),
            ])
            ground = World(triangles)
        match.collision_world = ground
        match.collision_provider = StaticCollisionProvider(ground)
        relocate(run, player, (x, y, z))
        return run, player, bot, target, own

    def test_actual_sniper_repositions_then_shoots_ball_and_respects_reuse_cooldown(self):
        run, player, bot, target, own = self.sniper_reposition_arena()
        self.engage(run, player, target)
        start = bot.position
        run.step({})
        state = npc_reposition.movement_state(bot)
        destination, speed = state.move_request()
        self.assertEqual(speed, 20)
        self.assertEqual(tuple(destination), (start[0], start[1], start[2] + 4))
        for _ in range(30):
            if not state.active:
                break
            self.assertFalse(bot.attack_input)
            self.assertEqual(own.temporary_shields, 175)
            run.step({})
        self.assertFalse(state.active)
        self.assertLess(math.dist(bot.position, destination), 0.02)
        self.assertEqual(bot.velocity, (0, 0, 0))
        for _ in range(180):
            run.step({})
            if own.temporary_shields < 175:
                break
        self.assertEqual(own.temporary_shields, 75)
        self.assertEqual(own.total - own.temporary_shields, 600)
        self.assertEqual(state.counter, 1)

        x, y, z = bot.position
        relocate(run, player, (x, y, z - 1.5))
        run.step({})
        self.assertEqual(state.counter, 1)
        self.assertTrue(bot.attack_input)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.frame += math.ceil(npc_reposition.COOLDOWN_MS / (1000 * combat.TICK))
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        current = next(t for t in run.match.combat._targets() if t.owner is bot)
        self.engage(run, player, current, shield=False)
        run.step({})
        self.assertEqual(state.counter, 2)
        self.assertTrue(state.active)

    def test_actual_sniper_reposition_cannot_cross_low_wall(self):
        run, player, bot, target, own = self.sniper_reposition_arena(wall=True)
        self.engage(run, player, target, shield=False)
        start = bot.position
        run.step({})
        state = npc_reposition.movement_state(bot)
        self.assertTrue(state.active)
        for _ in range(30):
            run.step({})
            if not state.active:
                break
        self.assertFalse(state.active)
        self.assertGreater(bot.position[2], start[2] + 0.5)
        self.assertLess(bot.position[2], start[2] + 1.5)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(own.total, 600)

    def test_manual_stop_cancels_active_sniper_reposition(self):
        run, player, bot, target, own = self.sniper_reposition_arena()
        self.engage(run, player, target, shield=False)
        for _ in range(5):
            run.step({})
        state = npc_reposition.movement_state(bot)
        self.assertTrue(state.active)
        self.assertGreater(bot.position[2], target.feet[2] + 0.5)
        stopped = bot.position
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(state.active)
        self.assertIsNone(state.move_request())
        for _ in range(45):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(own.total, 600)
        self.assertFalse(bot.attack_input)

    def test_active_sniper_retreat_loses_target_eligibility_and_rifle_recovers(self):
        for reason in ('line of sight', 'range', 'team'):
            with self.subTest(reason=reason):
                self.check_sniper_retreat_target_loss(reason)

    def check_sniper_retreat_target_loss(self, reason):
        run, player, bot, target, own = self.sniper_reposition_arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.engage(run, player, target)
        for _ in range(5):
            run.step({})
        state = npc_reposition.movement_state(bot)
        self.assertTrue(state.active)
        self.assertGreater(bot.position[2], target.feet[2] + 0.5)
        stopped = bot.position
        x, y, z = player.mover.position
        ground, provider, team = match.collision_world, match.collision_provider, bot.team
        if reason == 'line of sight':
            triangles = [tuple(ground.data[i:i + 9]) for i in range(0, len(ground.data), 13)]
            wall_z = z + 1
            triangles.extend([
                (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 5, wall_z),
                (x - 5, y, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
            ])
            match.collision_world = World(triangles)
            match.collision_provider = StaticCollisionProvider(match.collision_world)
        elif reason == 'range':
            relocate(run, player, (x, y, z - npc_control.AIM_RANGE - 1))
        else:
            bot.team = player.team

        run.step({})
        self.assertFalse(state.active)
        self.assertIsNone(state.move_request())
        self.assertEqual(bot.velocity, (0, 0, 0))
        for _ in range(25):
            run.step({})
            self.assertFalse(bot.attack_input)
        self.assertEqual(bot.position, stopped)
        self.assertEqual(own.temporary_shields, 175)

        match.collision_world, match.collision_provider, bot.team = ground, provider, team
        relocate(run, player, (x, y, z))
        for _ in range(180):
            run.step({})
            self.assertEqual(state.counter, 1)
            if own.temporary_shields < 175:
                break
        self.assertTrue(bot.attack_input)
        self.assertEqual(own.temporary_shields, 75)
        self.assertEqual(own.total - own.temporary_shields, 600)

    def test_actual_sleep_interrupts_sniper_retreat_and_retains_reuse_delay(self):
        run, player, ana, bot, target, own, before = self.support_scene(0x02E000000000013B, 'Sniper')
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        relocate(run, ana, (x, y, z - 4))
        angles = aim(fight._eye(ana), (x, y + 1, bot.position[2]))
        for frame in range(40):
            run.step({ana: (*angles, 8 if frame == 0 else 0)})
            if any(rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT for rocket in fight.rockets):
                break
        self.assertTrue(any(rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT
                            for rocket in fight.rockets))
        self.engage(run, player, target)
        state = npc_reposition.movement_state(bot, create=True)
        moved = False
        for _ in range(20):
            run.step({})
            if bot.entity in fight.sleeps:
                break
            moved |= state.active and bot.position[2] > target.feet[2] + 0.25
            self.assertFalse(bot.attack_input)
        self.assertTrue(moved)
        start, deadline = fight.sleeps[bot.entity]
        self.assertEqual(fight.vitals_of(target).total, before - 5)
        self.assertFalse(state.active)
        self.assertEqual(state.counter, 1)
        stopped = bot.position
        self.assertLess(stopped[2], target.feet[2] + 4)
        for _ in range(20):
            run.step({})
            self.assertEqual(bot.position, stopped)
            self.assertFalse(bot.attack_input)
            self.assertEqual(own.total, 775)

        relocate(run, player, (stopped[0], stopped[1], stopped[2] - 1.5))
        run.frame = deadline - 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        run.step({})
        self.assertFalse(fight.is_stunned(bot))
        self.assertTrue(bot.attack_input)
        self.assertEqual(state.counter, 1)
        relocate(run, player, (stopped[0] + 6, stopped[1], stopped[2]))
        for _ in range(180):
            run.step({})
            if own.total < 775:
                break
        self.assertEqual(own.total, 675)
        self.assertEqual(bot.position, stopped)
        self.assertEqual(state.counter, 1)

        ready = start + math.ceil(npc_reposition.COOLDOWN_MS / (1000 * combat.TICK)) + 2
        run.frame = max(run.frame, ready)
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        relocate(run, player, (stopped[0], stopped[1], stopped[2] - 1.5))
        run.step({})
        self.assertEqual(state.counter, 2)
        self.assertTrue(state.active)
        self.assertFalse(bot.attack_input)

    def test_actual_cannon_kill_retires_sniper_reposition(self):
        run, player, bot, target, own = self.sniper_reposition_arena()
        fight = run.match.combat
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        vitals = fight.vitals_of(target)
        vitals.now[combat.HEALTH_POOL] = 5
        fight._sync_body_health(bot, vitals)
        self.engage(run, player, target, shield=False)
        for _ in range(5):
            run.step({})
        state = npc_reposition.movement_state(bot)
        self.assertTrue(state.active)
        x, y, z = bot.position
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        run.step({player: (*angles, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, player.body)
        self.assertFalse(state.active)
        self.assertIsNone(state.move_request())
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        corpse = bot.position
        for _ in range(45):
            run.step({})
        self.assertEqual(bot.position, corpse)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(own.total, 600)

    def test_actual_piledriver_launch_ends_active_sniper_retreat(self):
        run, player, support, bot, target, own, before = self.support_scene(ball.HERO, 'Sniper')
        fight = run.match.combat
        x, y, z = player.mover.position
        relocate(run, support, (x + 1, y, z + 1.5))
        support.body_script.ult_seconds = 0
        support.body_script.set_ult_charge(0)
        lift(run, support)
        cast(run, support, 1)
        pile = pile_instance(support)
        for _ in range(80):
            run.step({})
            if pile.state(3).active and support.mover.position[1] <= y + 4:
                break
        self.assertTrue(pile.state(3).active)
        self.assertEqual(pile.state(7).counter, 0)
        self.engage(run, player, target, shield=False)
        state = npc_reposition.movement_state(bot, create=True)
        moved = False
        for _ in range(16):
            run.step({})
            if pile.state(7).counter:
                break
            moved |= state.active and bot.position[2] > target.feet[2] + 0.25
        self.assertTrue(moved)
        self.assertEqual(pile.state(7).counter, 1)
        self.assertLess(bot.position[2], target.feet[2] + 3.9)
        after = fight.vitals_of(target).total
        damage = before - after
        self.assertGreater(damage, 95)
        self.assertLess(damage, 100)
        self.assertAlmostEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), damage, places=4)
        self.assertEqual(own.total, 600)
        self.assertTrue(bot.airborne)
        self.assertGreater(bot.velocity[1], 10)
        run.step({})
        self.assertFalse(state.active)
        self.assertIsNone(state.move_request())
        self.assertEqual(state.counter, 1)
        self.assertIn(bot.entity, fight.npc_impulses)
        for _ in range(120):
            run.step({})
            if not bot.airborne:
                break
            self.assertFalse(state.active)
        self.assertFalse(bot.airborne)
        self.assertNotIn(bot.entity, fight.npc_impulses)
        self.assertEqual(state.counter, 1)
        self.assertEqual(fight.vitals_of(target).total, after)
        for _ in range(160):
            if own.total < 600:
                break
            run.step({})
        self.assertEqual(own.total, 505)
        self.assertEqual(state.counter, 1)

    def charge_arena(self, *, names=('A',)):
        run, player, bot, target, own = self.fixture.scenario(
            self.definition('Heavy Assault'), distance=10, names=names,
        )
        x, y, z = player.mover.position
        run.match.collision_world = flat_world(y)
        run.match.collision_provider = StaticCollisionProvider(run.match.collision_world)
        relocate(run, player, (x, y, z))
        return run, player, bot, target, own

    def test_actual_heavy_charge_hits_ball_armor_and_adaptive_shield(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                run, player, bot, target, own = self.charge_arena()
                self.engage(run, player, target, shield=False)
                charge = npc_charge._ability(bot)
                start = bot.position
                for _ in range(60):
                    run.step({})
                self.assertGreater(math.dist(start, bot.position), 1.5)
                self.assertEqual(own.total, 600)
                self.assertFalse(bot.attack_input)
                if shield:
                    run.step({player: (0, 0, 16)})
                    self.assertEqual(own.temporary_shields, 175)
                before = own.total
                for _ in range(60):
                    run.step({})
                    if own.total < before:
                        break
                self.assertEqual(own.total, before - (175 if shield else 170))
                self.assertEqual(own.temporary_shields, 0)
                self.assertEqual(charge.instance.vars[59].value(), 2)
                self.assertEqual(charge.instance.vars[6836].value(), (expr.Entity(player.body),))
                self.assertFalse(run.match.combat.is_stunned(player))
                self.assertFalse(bot.attack_input)
                self.assertEqual(npc_control.stop(run.match), 1)
                hit_position = bot.position
                after = own.total
                for _ in range(40):
                    run.step({})
                self.assertEqual(own.total, after)
                self.assertEqual(bot.position, hit_position)
                self.assertGreater(charge.remaining(), 0)

    def test_actual_heavy_charge_shield_spill_heals_during_allied_transcendence(self):
        impact_latency = None
        for aura in ('off', 'active', 'last_tick', 'expired'):
            with self.subTest(transcendence=aura):
                run, player, bot, target, own = self.charge_arena(names=('Ball', 'Zenyatta'))
                zen = self.prepare_allied_zenyatta(run, player)
                deadline = None
                if aura != 'off':
                    self.cast_allied_transcendence(run, zen)
                if aura in ('last_tick', 'expired'):
                    source = zen.body_script
                    ultimate = next(i for i in source.component.instances.values() if i.graph.index == 0x017B)
                    deadline = (ultimate.state(3).start + 6000) // source.frame_ms
                    self.assertIsNotNone(impact_latency)
                    run.frame = deadline - impact_latency - 1 - int(aura == 'last_tick')
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.engage(run, player, target, shield=False)
                engaged = run.frame
                charge = npc_charge._ability(bot)
                for _ in range(60):
                    run.step({})
                self.assertEqual(own.total, 600)
                run.step({player: (0, 0, 16)})
                self.assertEqual(own.temporary_shields, 175)

                # Stage a partially spent shield while leaving the permanent pools full.
                self.assertEqual(own.damage(50), 50)
                self.assertEqual(own.temporary_shields, 125)
                source = zen.body_script
                frozen = source.frame, source.component.now
                for tick in range(run.frame + 1, run.frame + 61):
                    run.match.update(tick * combat.TICK, tick, frames=False)
                    if not own.temporary_shields:
                        break
                self.assertEqual(own.temporary_shields, 0)
                self.assertEqual(charge.instance.vars[59].value(), 2)
                self.assertEqual(charge.instance.vars[6836].value(), (expr.Entity(player.body),))
                if aura == 'off':
                    impact_latency = tick - engaged
                if deadline is not None:
                    self.assertEqual(tick, deadline - int(aura == 'last_tick'))
                first_heal = 4.8 if aura in ('active', 'last_tick') else 0
                self.assertAlmostEqual(own.total, 555 + first_heal)
                self.assertAlmostEqual(own.now[combat.ARMOUR_POOL], 55 + first_heal)
                self.assertAlmostEqual(zen.stats.value(stats.HEALING), first_heal)
                self.assertEqual(npc_control.stop(run.match), 1)
                for later in range(tick + 1, tick + 13):
                    run.match.update(later * combat.TICK, later, frames=False)
                self.assertEqual((source.frame, source.component.now), frozen)
                self.assertEqual(own.temporary_shields, 0)
                # The last active interval settles at expiry, before that tick's hits.
                effective_heal = {'active': 45, 'last_tick': 9.6}.get(aura, 0)
                self.assertAlmostEqual(own.total, 555 + effective_heal)
                self.assertAlmostEqual(zen.stats.value(stats.HEALING), effective_heal)

    def prepare_rear_hook_wall(self, run, player, *, warmup=320):
        match = run.match
        x, y, z = player.mover.position
        wall = z - 12
        triangles = [tuple(match.collision_world.data[i:i + 9])
                     for i in range(0, len(match.collision_world.data), 13)]
        triangles.extend([
            (x - 5, y, wall, x + 5, y, wall, x + 5, y + 8, wall),
            (x - 5, y, wall, x + 5, y + 8, wall, x - 5, y + 8, wall),
        ])
        match.collision_world = World(triangles)
        match.collision_provider = StaticCollisionProvider(match.collision_world)
        for _ in range(warmup):
            run.step({})
        return wall

    def test_actual_heavy_charge_preserves_both_reload_phases(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_heavy_charge_during_reload(automatic=automatic, filled=filled)

    def test_actual_heavy_charge_kills_ball_during_both_reload_phases(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_heavy_charge_during_reload(automatic=automatic, filled=filled, lethal=True)

    def check_heavy_charge_during_reload(self, *, automatic, filled, lethal=False):
        run, player, bot, target, own = self.charge_arena()
        weapon = ball.instance(player, 0x1514)
        self.prepare_reload_for_npc_attack(run, player, weapon, automatic=automatic)
        if lethal:
            own.damage(599, bypass_armour=True)
        initial_health = 1 if lethal else 600

        if not filled:
            self.engage(run, player, target, shield=False)
            for _ in range(42):
                run.step({})
        self.start_reload_for_npc_attack(run, player, weapon, automatic=automatic)
        if filled:
            self.engage(run, player, target, shield=False)

        phase = weapon.state(20 if filled else 21)
        for _ in range(120):
            was_waiting = phase.active
            run.step({})
            if own.total < initial_health:
                break
        self.assertLess(own.total, initial_health)
        self.assertTrue(was_waiting)
        charge = npc_charge._ability(bot)
        self.assertEqual(charge.instance.vars[59].value(), 2)
        self.assertEqual(charge.instance.vars[6836].value(), (expr.Entity(player.body),))
        if lethal:
            self.check_reload_body_after_npc_kill(
                run, player, bot, target, own,
                expected_ammo=80 if filled else (0 if automatic else 67), respawn_damage=10,
            )
            return
        self.check_reload_after_npc_hit(
            run, player, bot, target, own, automatic=automatic, filled=filled, damage=170, return_damage=10,
        )

    def test_actual_npc_primary_and_melee_preserve_both_reload_phases(self):
        for name, distance, damage in (
            ('Trooper', 6, 15), ('Enforcer', 6, 17), ('Sniper', 6, 95), ('Heavy Assault', 6, 2.5),
            ('Trooper', 1.5, 25), ('Heavy Assault', 1.5, 75),
        ):
            delay = self.measure_npc_reload_hit_delay(name, distance)
            for automatic in (False, True):
                for filled in (False, True):
                    with self.subTest(npc=name, distance=distance, automatic=automatic, filled=filled):
                        self.check_npc_primary_or_melee_during_reload(
                            name, distance, damage, delay, automatic=automatic, filled=filled,
                        )

    def measure_npc_reload_hit_delay(self, name, distance):
        run, player, _, target, own = self.arena(name, distance=distance)
        self.engage(run, player, target, shield=False)
        for delay in range(1, 201):
            run.step({})
            if own.total < 600:
                return delay
        self.fail(f'{name} did not land an opening hit')

    def test_all_five_npc_kills_retire_both_reload_phases(self):
        for name, distance, respawn_damage in (
            ('Trooper', 6, 5), ('Enforcer', 6, 5), ('Sniper', 6, 5),
            ('Assassin', 1.5, 5), ('Heavy Assault', 6, 5),
            ('Trooper', 1.5, 5), ('Heavy Assault', 1.5, 10),
        ):
            delay = self.measure_npc_reload_hit_delay(name, distance)
            for automatic in (False, True):
                for filled in (False, True):
                    with self.subTest(npc=name, distance=distance, automatic=automatic, filled=filled):
                        self.check_npc_primary_or_melee_during_reload(
                            name, distance, 1, delay, automatic=automatic, filled=filled,
                            respawn_damage=respawn_damage,
                        )

    def check_npc_primary_or_melee_during_reload(
        self, name, distance, damage, delay, *, automatic, filled, respawn_damage=None,
    ):
        run, player, bot, target, own = self.arena(name, distance=distance)
        weapon = ball.instance(player, 0x1514)
        self.prepare_reload_for_npc_attack(run, player, weapon, automatic=automatic)
        lethal = respawn_damage is not None
        if lethal:
            own.damage(599, bypass_armour=True)
        initial_health = 1 if lethal else 600
        # Leave room inside either wait for variable trigger timing and the full Enforcer volley.
        lead = (87 if filled else 42) - delay - 1
        if lead < 0:
            self.engage(run, player, target, shield=False)
            for _ in range(-lead):
                run.step({})
        self.start_reload_for_npc_attack(run, player, weapon, automatic=automatic)
        self.assertEqual(own.total, initial_health)
        if lead >= 0:
            for _ in range(lead):
                run.step({})
            self.engage(run, player, target, shield=False)

        phase = weapon.state(20 if filled else 21)
        for _ in range(200):
            was_waiting = phase.active
            run.step({})
            if own.total < initial_health:
                break
        self.assertLess(own.total, initial_health)
        self.assertTrue(was_waiting)
        if lethal:
            self.check_reload_body_after_npc_kill(
                run, player, bot, target, own,
                expected_ammo=80 if filled else (0 if automatic else 67), respawn_damage=respawn_damage,
            )
            return
        self.assertTrue(phase.active)
        if name == 'Enforcer':
            for _ in range(6):
                run.step({})
        self.check_reload_after_npc_hit(
            run, player, bot, target, own, automatic=automatic, filled=filled, damage=damage, return_damage=5,
        )

    def check_reload_body_after_npc_kill(
        self, run, player, bot, target, own, *, expected_ammo, respawn_damage,
    ):
        old_body = player.body
        script = player.body_script
        weapon = ball.instance(player, 0x1514)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        x, y, z = bot.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, bot.entity)
        self.assertTrue(script.component.owner_dead)
        self.assertEqual(weapon.vars[53].value(), expected_ammo)
        self.assertNotIn(old_body, fight.pins.values())
        self.assertEqual(npc_control.stop(run.match), 1)

        for _ in range(20):
            run.step({player: (*angles, 1)})
            self.assertFalse(weapon.state(9).active)
            self.assertEqual(weapon.vars[53].value(), expected_ammo)
        for _ in range(700):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(script.retired)
        self.assertEqual(weapon.vars[53].value(), expected_ammo)
        replacement = ball.instance(player, 0x1514)
        for _ in range(15):
            run.step({})
        self.assertIsNot(replacement, weapon)
        self.assertEqual(replacement.vars[53].value(), 80)
        for state in (9, 20, 21):
            self.assertFalse(replacement.state(state).active)
        self.assertEqual(fight.vitals[player.body].total, 600)
        self.assertEqual(vitals.total, before)

        angles = aim(fight._eye(player), (x, y + 1, z))
        for _ in range(80):
            run.step({player: (*angles, 1)})
            if replacement.state(9).active:
                break
        self.assertTrue(replacement.state(9).active)
        self.assertEqual(replacement.vars[53].value(), 79)
        self.assertEqual(vitals.total, before - respawn_damage)

    def prepare_reload_for_npc_attack(self, run, player, weapon, *, automatic):
        ammo = weapon.vars[53]
        for _ in range(240 if automatic else 30):
            run.step({player: (-32768, 0, 1)})
            if automatic and ammo.value() == 1:
                break
        run.step({})
        self.assertEqual(ammo.value(), 1 if automatic else 67)

    def start_reload_for_npc_attack(self, run, player, weapon, *, automatic):
        refill = weapon.state(21)
        for _ in range(30):
            run.step({player: (-32768, 0, 1 if automatic else 1024)})
            if refill.active:
                break
        self.assertTrue(refill.active)
        self.assertEqual(weapon.vars[53].value(), 0 if automatic else 67)

    def check_reload_after_npc_hit(
        self, run, player, bot, target, own, *, automatic, filled, damage, return_damage,
    ):
        fight = run.match.combat
        weapon = ball.instance(player, 0x1514)
        ammo = weapon.vars[53]
        refill, exit_wait = weapon.state(21), weapon.state(20)
        cannon = weapon.state(9)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(own.total, 600 - damage)
        self.assertEqual(refill.active, not filled)
        self.assertEqual(exit_wait.active, filled)
        self.assertEqual(ammo.value(), 80 if filled else (0 if automatic else 67))
        self.assertEqual(npc_control.stop(run.match), 1)

        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(140):
            x, y, z = bot.position
            run.step({player: (*aim(fight._eye(player), (x, y + 1, z)), 1)})
            if cannon.active:
                break
            self.assertEqual(vitals.total, before)
        self.assertTrue(cannon.active)
        self.assertFalse(refill.active)
        self.assertFalse(exit_wait.active)
        self.assertEqual((refill.counter, exit_wait.counter), (1, 1))
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(vitals.total, before - return_damage)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), return_damage)
        self.assertEqual(own.total, 600 - damage)

    def test_actual_heavy_charge_keeps_live_tether_and_retires_it_on_death(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, player, bot, target, own = self.charge_arena()
                match = run.match
                fight = match.combat
                x, y, z = player.mover.position
                wall = self.prepare_rear_hook_wall(run, player)
                if lethal:
                    own.now[combat.ARMOUR_POOL] = 0
                    own.now[combat.HEALTH_POOL] = 170
                    fight._sync_body_health(player, own)
                before = own.total
                enemy_health = fight.vitals_of(target).total
                self.engage(run, player, target, shield=False)
                hook = {player: (-32768, 0, 2)}
                for _ in range(8):
                    run.step(hook)
                body = player.body
                tether = fight.ball_grapples[body]
                self.assertTrue(tether.attached)
                self.assertAlmostEqual(tether.anchor[2], wall, places=4)
                self.assertEqual(own.total, before)
                charge = npc_charge._ability(bot)
                for _ in range(120):
                    previous_position = tuple(player.mover.position)
                    run.step(hook)
                    if own.total < before:
                        break
                    self.assertIs(fight.ball_grapples[body], tether)
                self.assertEqual(charge.instance.vars[59].value(), 2)
                self.assertEqual(charge.instance.vars[6836].value(), (expr.Entity(body),))
                self.assertEqual(fight.vitals_of(target).total, enemy_health)
                self.assertFalse(fight.is_stunned(player))
                self.assertEqual(npc_control.stop(match), 1)
                if lethal:
                    self.assertTrue(own.dead)
                    self.assertEqual(own.killer, bot.entity)
                    self.assertNotIn(body, fight.ball_grapples)
                    for _ in range(30):
                        run.step(hook)
                        self.assertNotIn(body, fight.ball_grapples)
                    run.frame += 660
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertNotEqual(player.body, body)
                    self.assertNotIn(player.body, fight.ball_grapples)
                    self.assertNotIn(body, fight.grapple_seen)
                    relocate(run, player, (x, y, z))
                else:
                    self.assertEqual(own.total, before - 170)
                    self.assertIs(fight.ball_grapples[body], tether)
                    self.assertTrue(tether.attached)
                    self.assertEqual(tuple(player.mover.position), previous_position)
                    self.assertEqual(player.mover.velocity, (0, 0, 0))
                    run.step({})
                    self.assertNotIn(body, fight.ball_grapples)
                    self.assertGreater(ball.instance(player, ball.GRAPPLE).state(25).remaining(), 4.9)
                    run.step(hook)
                    self.assertNotIn(body, fight.ball_grapples)

                run.step({})
                ability = ball.instance(player, ball.GRAPPLE).state(25)
                for _ in range(325):
                    if ability.remaining() == 0:
                        break
                    run.step({})
                self.assertEqual(ability.remaining(), 0)
                for _ in range(8):
                    run.step(hook)
                replacement = fight.ball_grapples[player.body]
                self.assertIsNot(replacement, tether)
                self.assertTrue(replacement.attached)
                self.assertAlmostEqual(replacement.anchor[2], wall, places=4)

    def test_manual_heavy_charge_stop_during_windup_prevents_motion(self):
        run, player, bot, target, own = self.charge_arena()
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        start = bot.position
        for _ in range(8):
            run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 1)
        self.assertEqual(bot.position, start)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(90):
            run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertEqual(bot.position, start)
        self.assertEqual(own.total, 600)
        self.assertGreater(charge.remaining(), 0)
        self.engage(run, player, target, shield=False)
        run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertTrue(bot.attack_input)
        self.assertEqual(bot.position, start)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.frame += math.ceil(charge.remaining() / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(charge.remaining(), 0)
        self.engage(run, player, target, shield=False)
        run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 1)
        self.assertFalse(bot.attack_input)

    def test_manual_heavy_charge_capsule_cannot_cross_low_wall(self):
        run, player, bot, target, own = self.charge_arena()
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        for _ in range(60):
            run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 1)
        x, y, z = bot.position
        wall_z = z - 2
        triangles = [tuple(run.match.collision_world.data[i:i + 9])
                     for i in range(0, len(run.match.collision_world.data), 13)]
        triangles.extend([
            (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 1.1, wall_z),
            (x - 5, y, wall_z, x + 5, y + 1.1, wall_z, x - 5, y + 1.1, wall_z),
        ])
        run.match.collision_world = World(triangles)
        run.match.collision_provider = StaticCollisionProvider(run.match.collision_world)
        self.assertIsNone(combat.ray_world(run.match.collision_world,
                                          (x, y + combat.EYE, z), (0, 0, -1), 8))
        for _ in range(40):
            run.step({})
            if charge.instance.vars[59].value() == 0:
                break
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertLess(bot.position[2], z - 0.5)
        self.assertGreater(bot.position[2], wall_z)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(own.total, 600)
        self.assertGreater(charge.remaining(), 0)

    def test_manual_heavy_charge_misses_ball_who_leaves_its_fixed_lane(self):
        run, player, bot, target, own = self.charge_arena()
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        for _ in range(60):
            run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 1)
        x, y, z = player.mover.position
        relocate(run, player, (x + 4, y, z))
        for _ in range(140):
            run.step({})
            if charge.instance.vars[59].value() == 0:
                break
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertAlmostEqual(bot.position[0], x, delta=0.01)
        self.assertLess(bot.position[2], z - 2)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(own.total, 600)
        self.assertGreater(charge.remaining(), 0)

    def test_manual_heavy_charge_waits_for_teammate_to_clear_lane(self):
        run, player, bot, target, _ = self.charge_arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        x, y, z = bot.position
        teammate = npc.spawn(match, self.definition('Trooper'), (x, y, z - 4), team=bot.team)
        for _ in range(25):
            run.step({})
        self.assertFalse(charge.instance.vars.get(59) and charge.instance.vars[59].value())
        self.assertEqual(bot.position, (x, y, z))
        self.assertTrue(bot.attack_input)
        teammate.position = (x + 4, y, z - 4)
        run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 1)
        self.assertFalse(bot.attack_input)

    def test_moving_heavy_charge_cancels_on_target_loss_or_teammate_block(self):
        for reason in ('line of sight', 'range', 'team', 'teammate'):
            with self.subTest(reason=reason):
                self.check_moving_heavy_charge_cancellation(reason)

    def check_moving_heavy_charge_cancellation(self, reason):
        run, player, bot, target, own = self.charge_arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.engage(run, player, target)
        charge = npc_charge._ability(bot)
        for _ in range(60):
            run.step({})
        phase = charge.instance.vars[npc_charge.PHASE]
        motion = npc_charge._state(charge.instance, npc_charge.MOTION)
        impact = charge.instance.node_state(charge.instance.graph.node(npc_melee.CHARGE_IMPACT))
        self.assertEqual(phase.value(), 1)
        self.assertTrue(motion.active)
        self.assertLess(bot.position[2], target.feet[2] - 1.5)
        self.assertEqual(own.temporary_shields, 100)
        self.assertEqual(impact.counter, 0)
        x, y, z = player.mover.position
        ground, provider, team = match.collision_world, match.collision_provider, bot.team
        teammate = None
        if reason == 'line of sight':
            triangles = [tuple(ground.data[i:i + 9]) for i in range(0, len(ground.data), 13)]
            wall_z = bot.position[2] - 1.5
            triangles.extend([
                (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 5, wall_z),
                (x - 5, y, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
            ])
            match.collision_world = World(triangles)
            match.collision_provider = StaticCollisionProvider(match.collision_world)
        elif reason == 'range':
            relocate(run, player, (x, y, z - npc_control.AIM_RANGE - 1))
        elif reason == 'team':
            bot.team = player.team
        else:
            teammate = npc.spawn(match, self.definition('Trooper'), (x, y, bot.position[2] - 1.5), team=team)

        for _ in range(12):
            run.step({})
            if not phase.value():
                break
        self.assertEqual(phase.value(), 0)
        self.assertFalse(motion.active)
        self.assertEqual(impact.counter, 0)
        self.assertEqual(own.temporary_shields, 100)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertGreater(charge.remaining(), 0)
        if teammate is not None:
            self.assertGreater(bot.position[2], teammate.position[2])
            teammate.position = (x + 5, y, teammate.position[2])
        else:
            self.assertFalse(bot.attack_input)

        match.collision_world, match.collision_provider, bot.team = ground, provider, team
        relocate(run, player, (x, y, bot.position[2] - 8))
        for _ in range(60):
            run.step({})
            self.assertEqual(phase.value(), 0)
            if own.temporary_shields < 100:
                break
        self.assertEqual(own.temporary_shields, 95)
        self.assertEqual(own.total - own.temporary_shields, 600)
        self.assertEqual(impact.counter, 0)

    def test_ball_rifle_death_cancels_moving_heavy_charge_before_respawn(self):
        run, player, soldier, bot, target, own, _ = self.support_scene(SOLDIER, 'Heavy Assault', distance=10)
        match = run.match
        fight = match.combat
        soldier.team = bot.team
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        phase = charge.instance.vars[npc_charge.PHASE]
        impact = charge.instance.node_state(charge.instance.graph.node(npc_melee.CHARGE_IMPACT))
        for _ in range(60):
            run.step({})
        self.assertEqual(phase.value(), 1)
        self.assertTrue(npc_charge._state(charge.instance, npc_charge.MOTION).active)
        self.assertEqual(own.total, 600)
        x, y, z = player.mover.position
        own.damage(595, bypass_armour=True)
        fight._sync_body_health(player, own)
        self.assertEqual(own.total, 5)
        old_body = player.body
        shot = aim(fight._eye(soldier), (x, y + 1, z))
        run.step({soldier: (*shot, 1)})
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, soldier.body)
        run.step({})
        self.assertEqual(phase.value(), 0)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertFalse(bot.attack_input)
        self.assertGreater(charge.remaining(), 0)
        stopped = bot.position
        run.frame += math.ceil(combat.RESPAWN / combat.TICK) + 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(bot.position, stopped)
        self.assertNotEqual(player.body, old_body)
        replacement = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(replacement)
        self.assertEqual(health.total, 600)
        self.assertEqual(impact.counter, 0)
        self.assertGreater(charge.remaining(), 0)
        relocate(run, player, (x, y, stopped[2] - 10))
        for _ in range(60):
            run.step({})
            self.assertEqual(phase.value(), 0)
            if health.total < 600:
                break
        self.assertEqual(health.total, 597.5)
        self.assertEqual(impact.counter, 0)

    def test_actual_allied_piledriver_interrupts_moving_heavy_charge(self):
        run, player, support, bot, target, own, before = self.support_scene(
            ball.HERO, 'Heavy Assault', distance=10,
        )
        match = run.match
        x, y, z = player.mover.position
        match.collision_world = flat_world(y)
        match.collision_provider = StaticCollisionProvider(match.collision_world)
        relocate(run, player, (x, y, z))
        relocate(run, support, (x + 0.75, y, z + 3.5))
        support.body_script.ult_seconds = 0
        support.body_script.set_ult_charge(0)
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        for _ in range(8):
            run.step({})
        lift(run, support)
        cast(run, support, 1)
        landing = pile_instance(support).state(7)
        for _ in range(80):
            run.step({})
            if landing.counter:
                break
        self.assertEqual(landing.counter, 1)
        self.assertLess(bot.position[2], z + 9)
        self.assertEqual(match.combat.vitals_of(target).total, before - 100)
        self.assertTrue(bot.airborne)
        self.assertGreater(bot.velocity[1], 10)
        self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 100)
        run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertGreater(charge.remaining(), 0)
        self.assertEqual(own.total, 600)
        for _ in range(12):
            run.step({})
        self.assertTrue(bot.airborne)
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertEqual(own.total, 600)

    def test_actual_allied_fireball_interrupts_moving_heavy_charge(self):
        run, player, support, bot, target, own, before = self.support_scene(
            ball.HERO, 'Heavy Assault', distance=14,
        )
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        relocate(run, support, (x, y, z + 4.5))
        prepare_swing(run, support)
        support.body_script.ult_seconds = 0
        support.body_script.set_ult_charge(0)
        self.engage(run, player, target, shield=False)
        relocate(run, support, (x, y, z + 4.5))
        charge = npc_charge._ability(bot)
        for _ in range(6):
            run.step({})
        lift(run, support)
        moving = False
        for _ in range(110):
            motion = npc_charge._state(charge.instance, npc_charge.MOTION)
            moving = motion is not None and motion.active and bot.position[2] < target.feet[2] - 0.5
            grapple_tests.hold(run, support, 1)
            if fight.vitals_of(target).total < before or own.total < 600:
                break
        self.assertTrue(moving)
        self.assertIn(bot.entity, fight.ball_contacts[support.body])
        self.assertEqual(fight.vitals_of(target).total, before - 50)
        self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 50)
        self.assertTrue(bot.airborne)
        self.assertGreater(bot.velocity[1], 4)
        self.assertEqual(own.total, 600)
        run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertGreater(charge.remaining(), 0)
        self.assertFalse(motion.active)
        self.assertEqual(own.total, 600)
        for _ in range(120):
            run.step({})
            if not bot.airborne:
                break
            self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertFalse(bot.airborne)
        self.assertEqual(fight.vitals_of(target).total, before - 50)

    def test_actual_ana_sleep_interrupts_moving_heavy_charge(self):
        run, player, support, bot, target, own, before = self.support_scene(
            0x02E000000000013B, 'Heavy Assault', distance=10,
        )
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        match.collision_world = flat_world(y)
        match.collision_provider = StaticCollisionProvider(match.collision_world)
        relocate(run, player, (x, y, z))
        self.engage(run, player, target, shield=False)
        relocate(run, support, (x, y, z + 2))
        charge = npc_charge._ability(bot)
        for _ in range(58):
            run.step({})
        self.assertLess(bot.position[2], z + 9)
        self.assertEqual(charge.instance.vars[59].value(), 1)
        start, deadline = self.sleep_npc(run, support, bot)
        self.assertEqual(deadline - start, 313)
        self.assertEqual(fight.vitals_of(target).total, before - 5)
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertGreater(charge.remaining(), 0)
        stopped = bot.position
        for _ in range(40):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(own.total, 600)
        self.assertTrue(fight.is_stunned(bot))
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(bot))
        self.assertEqual(own.total, 600)
        for _ in range(100):
            run.step({})
            if own.total < 600:
                break
        self.assertLess(own.total, 600)
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertGreater(charge.remaining(), 0)

    def test_actual_cannon_kill_retires_moving_heavy_charge(self):
        run, player, bot, target, own = self.charge_arena()
        fight = run.match.combat
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        vitals = fight.vitals_of(target)
        vitals.now[combat.HEALTH_POOL] = 5
        fight._sync_body_health(bot, vitals)
        self.assertEqual(vitals.total, 5)
        self.engage(run, player, target, shield=False)
        charge = npc_charge._ability(bot)
        for _ in range(58):
            run.step({})
        self.assertEqual(charge.instance.vars[59].value(), 1)
        self.assertLess(bot.position[2], target.feet[2] - 1)
        x, y, z = bot.position
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        for _ in range(20):
            run.step({player: (*angles, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, player.body)
        self.assertEqual(charge.instance.vars[59].value(), 0)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        corpse = bot.position
        for _ in range(100):
            run.step({})
        self.assertEqual(bot.position, corpse)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(own.total, 600)

    def test_actual_npc_melee_graphs_damage_ball_armor_and_adaptive_shield(self):
        for name, graph_id, amount in (('Trooper', 0x1422, 30), ('Heavy Assault', 0x140E, 80)):
            for shield in (False, True):
                with self.subTest(npc=name, shield=shield):
                    run, player, bot, target, own = self.arena(name)
                    self.engage(run, player, target, shield=shield)
                    component = bot.body_script.component
                    instance, = (i for i in component.instances.values() if i.graph.index == graph_id)
                    before = own.total
                    for _ in range(60):
                        run.step({})
                        if own.total < before:
                            break
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(instance.vars[375].value(), (expr.Entity(player.body),))
                    self.assertEqual(own.total, before - amount + (0 if shield else 5))
                    self.assertEqual(own.temporary_shields, 175 - amount if shield else 0)
                    if shield:
                        self.assertEqual(own.total - own.temporary_shields, 600)
                    if name == 'Heavy Assault':
                        self.assertGreater(player.mover.velocity[1], 7)
                        self.assertLess(player.mover.velocity[2], -19)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    after = own.total
                    for _ in range(80):
                        run.step({})
                    self.assertEqual(own.total, after)

    def test_actual_heavy_melee_moves_tethered_ball_even_through_adaptive_shield(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                run, player, bot, target, own = self.arena('Heavy Assault')
                match = run.match
                fight = match.combat
                flat = flat_world(player.mover.position[1])
                match.collision_world = flat
                match.collision_provider = StaticCollisionProvider(flat)
                wall = self.prepare_rear_hook_wall(run, player)
                self.engage(run, player, target, shield=shield)
                before = own.total
                hook = {player: (-32768, 0, 2)}
                for _ in range(8):
                    run.step(hook)
                tether = fight.ball_grapples[player.body]
                self.assertTrue(tether.attached)
                self.assertAlmostEqual(tether.anchor[2], wall, places=4)
                self.assertEqual(own.total, before)
                for _ in range(60):
                    previous = tuple(player.mover.position)
                    run.step(hook)
                    if own.total < before:
                        break
                melee = npc_melee.ability(bot.body_script.component)
                self.assertEqual(melee.instance.graph.index, 0x140E)
                self.assertEqual(melee.instance.vars[375].value(), (expr.Entity(player.body),))
                self.assertEqual(own.total, before - (80 if shield else 75))
                self.assertEqual(own.temporary_shields, 95 if shield else 0)
                self.assertFalse(fight.is_stunned(player))
                self.assertIs(fight.ball_grapples[player.body], tether)
                self.assertTrue(player.mover.airborne)
                self.assertGreater(player.mover.position[1], previous[1])
                self.assertLess(player.mover.position[2], previous[2])
                self.assertGreater(player.mover.velocity[1], 6)
                self.assertLess(player.mover.velocity[2], -15)
                self.assertEqual(npc_control.stop(match), 1)
                after = own.total
                for _ in range(8):
                    run.step(hook)
                    self.assertIs(fight.ball_grapples[player.body], tether)
                    self.assertEqual(own.total, after)
                run.step({})
                self.assertNotIn(player.body, fight.ball_grapples)
                self.assertGreater(ball.instance(player, ball.GRAPPLE).state(25).remaining(), 4.9)

    def test_manual_npc_melee_stop_preserves_cooldown_and_allows_reuse(self):
        for name, amount in (('Trooper', 30), ('Heavy Assault', 80)):
            with self.subTest(npc=name):
                run, player, bot, target, own = self.arena(name)
                self.engage(run, player, target)
                for _ in range(10):
                    run.step({})
                    state = npc_melee.ability(bot.body_script.component)
                    if state is not None and state.flags & state.RUNNING:
                        break
                self.assertIsNotNone(state)
                self.assertTrue(state.flags & state.RUNNING)
                self.assertGreater(state.remaining(), 0)
                self.assertEqual(own.temporary_shields, 175)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(22):
                    run.step({})
                self.assertEqual(own.temporary_shields, 175)
                self.assertFalse(state.flags & state.RUNNING)
                self.assertGreater(state.remaining(), 0)

                self.engage(run, player, target, shield=False)
                run.step({})
                self.assertFalse(state.flags & state.RUNNING)
                self.assertTrue(bot.attack_input)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(65):
                    run.step({})
                self.assertEqual(state.remaining(), 0)
                before = own.temporary_shields
                self.engage(run, player, target, shield=False)
                for _ in range(60):
                    run.step({})
                    if own.temporary_shields < before:
                        break
                self.assertEqual(own.temporary_shields, before - amount)

    def test_actual_piledriver_interrupts_both_npc_melee_windups(self):
        for name, impact_node, damage in (('Trooper', 4, 25), ('Heavy Assault', 5, 75)):
            with self.subTest(npc=name):
                run, player, support, bot, target, own, before = self.support_scene(ball.HERO, name)
                fight = run.match.combat
                x, y, z = player.mover.position
                relocate(run, support, (x + 0.75, y, z + 1.5))
                support.body_script.ult_seconds = 0
                support.body_script.set_ult_charge(0)
                lift(run, support)
                cast(run, support, 1)
                pile = pile_instance(support)
                for _ in range(80):
                    run.step({})
                    if pile.state(3).active and support.mover.position[1] <= y + 4:
                        break
                self.assertTrue(pile.state(3).active)
                self.assertEqual(pile.state(7).counter, 0)
                self.engage(run, player, target, shield=False)
                melee = npc_melee.ability(bot.body_script.component)
                impact = melee.instance.node_state(melee.instance.graph.node(impact_node))
                for _ in range(16):
                    run.step({})
                    if pile.state(7).counter:
                        break
                self.assertTrue(melee.flags & melee.RUNNING)
                self.assertEqual(pile.state(7).counter, 1)
                self.assertEqual(impact.counter, 0)
                self.assertEqual(own.total, 600)
                self.assertEqual(fight.vitals_of(target).total, before - 100)
                self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 100)
                self.assertTrue(bot.airborne)

                run.step({})
                self.assertFalse(melee.flags & melee.RUNNING)
                self.assertGreater(melee.remaining(), 0)
                for _ in range(125):
                    self.assertEqual(impact.counter, 0)
                    if not bot.airborne:
                        break
                    run.step({})
                self.assertFalse(bot.airborne)
                self.assertEqual(impact.counter, 0)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(125):
                    if melee.remaining() == 0:
                        break
                    run.step({})
                self.assertEqual(melee.remaining(), 0)
                self.assertEqual(impact.counter, 0)

                bx, by, bz = bot.position
                relocate(run, player, (bx, by, bz - 1.5))
                current = next(t for t in fight._targets() if t.owner is bot)
                self.engage(run, player, current, shield=False)
                for _ in range(60):
                    previous = own.total
                    run.step({})
                    if impact.counter:
                        break
                self.assertEqual(impact.counter, 1)
                self.assertEqual(own.total, previous - damage)

    def test_manual_npcs_switch_from_primary_to_melee_on_entering_reach(self):
        for name, primary, melee in (('Trooper', 20, 30), ('Heavy Assault', 5, 80)):
            with self.subTest(npc=name):
                run, player, bot, target, own = self.arena(name, distance=6)
                self.engage(run, player, target)
                for _ in range(100):
                    run.step({})
                    if own.temporary_shields < 175:
                        break
                self.assertEqual(own.temporary_shields, 175 - primary)
                self.assertTrue(bot.attack_input)
                state = npc_melee.ability(bot.body_script.component)
                self.assertEqual(state.remaining(), 0)

                x, y, z = player.mover.position
                bot.position = (x, y, z + 1.5)
                for _ in range(60):
                    run.step({})
                    if own.temporary_shields < 175 - primary:
                        break
                self.assertTrue(state.flags & state.RUNNING)
                self.assertFalse(bot.attack_input)
                self.assertEqual(own.temporary_shields, 175 - primary - melee)

    def test_manual_npc_melee_loses_wall_and_team_eligibility_during_windup(self):
        for name in ('Trooper', 'Heavy Assault'):
            for wall in (False, True):
                with self.subTest(npc=name, wall=wall):
                    run, player, bot, target, own = self.arena(name)
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.engage(run, player, target)
                    for _ in range(10):
                        run.step({})
                        state = npc_melee.ability(bot.body_script.component)
                        if state is not None and state.flags & state.RUNNING:
                            break
                    self.assertIsNotNone(state)
                    self.assertTrue(state.flags & state.RUNNING)

                    if wall:
                        old = match.collision_world
                        triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
                        x, y, z = player.mover.position
                        z += 0.75
                        triangles.extend([
                            (x - 5, y, z, x + 5, y, z, x + 5, y + 5, z),
                            (x - 5, y, z, x + 5, y + 5, z, x - 5, y + 5, z),
                        ])
                        match.collision_world = World(triangles)
                        match.collision_provider = StaticCollisionProvider(match.collision_world)
                    else:
                        bot.team = player.team
                    run.step({})
                    self.assertFalse(state.flags & state.RUNNING)
                    for _ in range(80):
                        run.step({})
                    self.assertEqual(own.temporary_shields, 175)
                    self.assertFalse(bot.attack_input)

    def test_all_five_actual_attacks_damage_adaptive_shield_then_stop(self):
        amounts = {'Trooper': 20, 'Enforcer': 34, 'Sniper': 100, 'Assassin': 25, 'Heavy Assault': 5}
        for name, amount in amounts.items():
            with self.subTest(npc=name):
                run, player, bot, target, own = self.arena(name, distance=1.5 if name == 'Assassin' else 6)
                self.engage(run, player, target)
                self.assertEqual(own.temporary_shields, 175)
                for _ in range(200):
                    run.step({})
                    if own.temporary_shields < 175:
                        break
                if name == 'Enforcer':
                    for _ in range(6):
                        run.step({})
                self.assertEqual(own.temporary_shields, 175 - amount)
                self.assertEqual(own.total - own.temporary_shields, 600)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(npc_control.stop(run.match), 1)
                before = own.total
                for _ in range(80):
                    run.step({})
                self.assertEqual(own.total, before)
                self.assertFalse(run.match.combat.is_stunned(player))

    def test_lethal_quick_melee_stops_engaged_npcs_and_releases_pin(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault', 'Assassin'):
            with self.subTest(npc=name):
                if name == 'Assassin':
                    run, victim, attacker, bot, target = self.allied_pin_rescue_scene()
                    fight = run.match.combat
                    own = fight.vitals[victim.body]
                    self.assertEqual(fight.pins.get(bot.entity), victim.body)
                    self.assertTrue(fight.is_stunned(victim))
                else:
                    run, attacker, bot, target, own = self.arena(name)
                    victim = attacker
                    fight = run.match.combat
                    self.engage(run, attacker, target, shield=False)
                    run.step({})
                    if name in ('Trooper', 'Heavy Assault'):
                        for _ in range(10):
                            melee = npc_melee.ability(bot.body_script.component)
                            if melee is not None and melee.flags & melee.RUNNING:
                                break
                            run.step({})
                        self.assertIsNotNone(melee)
                        self.assertTrue(melee.flags & melee.RUNNING)
                    elif name == 'Sniper':
                        self.assertTrue(npc_reposition.movement_state(bot).active)
                    else:
                        self.assertTrue(bot.attack_input)
                    self.assertEqual(own.total, 600)

                vitals = fight.vitals_of(target)
                vitals.damage(vitals.total - 20)
                self.assertEqual(vitals.total, 20)
                script = attacker.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                health = own.total
                x, y, z = bot.position
                melee_aim = aim(fight._eye(attacker), (x, y + 0.7, z))
                run.step({attacker: (*melee_aim, 0x800)})

                self.assertTrue(bot.dead)
                self.assertEqual(vitals.killer, attacker.body)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 20)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)
                self.assertEqual(attacker.client.deaths[0][0], bot.entity)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.is_stunned(victim))
                self.assertFalse(bot.attack_input)
                position = bot.position
                for _ in range(140):
                    run.step({})
                self.assertEqual(bot.position, position)
                self.assertEqual(own.total, health)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(bot.entity, fight.pins)

    def prepare_allied_zenyatta(self, run, player):
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        zen = run.players[1]
        zen.team = player.team
        self.assertTrue(match.switch_hero(zen, heroes()[ZENYATTA]))
        x, y, z = player.mover.position
        relocate(run, zen, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        return zen

    def cast_allied_transcendence(self, run, zen):
        script = zen.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertGreater(cost, 0)
        script.set_ult_charge(cost)
        run.step({zen: (-32768, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_all_five_npc_opening_hits_during_allied_transcendence(self):
        for name, distance, damage in (
            ('Trooper', 6, 20), ('Enforcer', 6, 34), ('Sniper', 6, 100),
            ('Assassin', 1.5, 25), ('Heavy Assault', 6, 5),
            ('Trooper', 1.5, 30), ('Heavy Assault', 1.5, 80),
        ):
            with self.subTest(npc=name, distance=distance):
                run, player, bot, target, own = self.fixture.scenario(
                    self.definition(name), distance=distance, names=('Ball', 'Zenyatta'),
                )
                match = run.match
                zen = self.prepare_allied_zenyatta(run, player)

                # Isolate permanent healing from the temporary pool the NPC will hit.
                own.now[combat.ARMOUR_POOL] -= 15
                self.assertEqual(own.total, 585)
                self.cast_allied_transcendence(run, zen)
                self.engage(run, player, target)
                self.assertEqual(own.temporary_shields, 175)
                self.assertEqual(bot.attack_target, player.entity)
                for _ in range(200):
                    run.step({})
                    if own.temporary_shields < 175:
                        break
                if name == 'Enforcer':
                    for _ in range(6):
                        run.step({})
                self.assertEqual(own.temporary_shields, 175 - damage)
                self.assertEqual(match.combat.is_stunned(player), name == 'Assassin')
                self.assertEqual(npc_control.stop(match), 1)
                for _ in range(5):
                    run.step({})
                self.assertEqual(own.temporary_shields, 175 - damage)
                self.assertEqual(own.total - own.temporary_shields, 600)
                self.assertAlmostEqual(zen.stats.value(stats.HEALING), 15)
                zen_target = next(t for t in match.combat._targets() if t.owner is zen)
                self.assertTrue(match.combat._has_protection(zen_target, combat.DAMAGE_IMMUNITY_STATES))

    def test_all_five_npc_opening_hits_straddle_adaptive_shield_expiry(self):
        for name, distance, shield_damage, armor_damage in (
            ('Trooper', 6, 20, 15), ('Enforcer', 6, 34, 17), ('Sniper', 6, 100, 95),
            ('Assassin', 1.5, 25, 20), ('Heavy Assault', 6, 5, 2.5),
            ('Trooper', 1.5, 30, 25), ('Heavy Assault', 1.5, 80, 75), ('Heavy Assault', 10, 175, 170),
        ):
            with self.subTest(npc=name, distance=distance):
                latency = self.npc_opening_hit_latency(name, distance, shield_damage)
                for early in (False, True):
                    with self.subTest(early=early):
                        self.check_npc_hit_at_shield_deadline(
                            name, distance, latency, early, shield_damage, armor_damage,
                        )
                with self.subTest(replay=True):
                    self.check_replayed_npc_hit_before_shield_deadline(name, distance, latency, shield_damage)

    def npc_shield_deadline_scene(self, name, distance):
        if distance == 10:
            run, player, bot, target, own = self.charge_arena()
        else:
            run, player, bot, target, own = self.arena(name, distance=distance)
        fight = run.match.combat
        x, y, z = target.feet
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        if distance == 10:
            # Charge needs more separation than the shield's enemy search.
            bot.position = (x, y, z - 4)
        run.step({player: (*angles, 16)})
        self.assertEqual(own.temporary_shields, 175)
        self.assertEqual(own.adaptive_until, run.frame + 562)
        if distance == 10:
            bot.position = (x, y, z)
        return run, player, bot, own

    def npc_opening_hit_latency(self, name, distance, shield_damage):
        # Measure real attack timing before scheduling an impact at expiry.
        run, player, bot, own = self.npc_shield_deadline_scene(name, distance)
        start = run.frame + 150
        run.match.update(start * combat.TICK, start, frames=False)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for tick in range(start + 1, start + 201):
            run.match.update(tick * combat.TICK, tick, frames=False)
            if own.total < 775:
                self.assertEqual(own.temporary_shields, 175 - shield_damage)
                self.assertEqual(own.total - own.temporary_shields, 600)
                return tick - start
        self.fail(f'{name} did not land an opening attack')

    def check_npc_hit_at_shield_deadline(self, name, distance, latency, early, shield_damage, armor_damage):
        run, player, bot, own = self.npc_shield_deadline_scene(name, distance)
        match = run.match
        script = player.body_script
        frozen = script.frame, script.component.now
        expiry = own.adaptive_until
        hit_tick = expiry - int(early)
        start = hit_tick - latency
        match.update(start * combat.TICK, start, frames=False)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for tick in range(start + 1, hit_tick):
            match.update(tick * combat.TICK, tick, frames=False)
            self.assertEqual(own.total, 775)
        match.update(hit_tick * combat.TICK, hit_tick, frames=False)
        if early:
            self.assertEqual(own.temporary_shields, 175 - shield_damage)
            self.assertEqual(own.total, 775 - shield_damage)
        else:
            self.assertEqual(own.temporary_shields, 0)
            self.assertEqual(own.total, 600 - armor_damage)
            self.assertEqual(own.maximum, 600)
        self.assertEqual((script.frame, script.component.now), frozen)

    def check_replayed_npc_hit_before_shield_deadline(self, name, distance, latency, shield_damage):
        run, player, bot, own = self.npc_shield_deadline_scene(name, distance)
        match = run.match
        fight = match.combat
        script = player.body_script
        frozen = script.frame, script.component.now
        expiry = own.adaptive_until
        start = expiry - 1 - latency
        match.update(start * combat.TICK, start, frames=False)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for tick in range(start + 1, expiry - 8):
            match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(own.total, 775)
        hits = []
        damage = own.damage

        def record_damage(amount, **kwargs):
            shield_before = own.temporary_shields
            dealt = damage(amount, **kwargs)
            hits.append((fight.tick, shield_before - own.temporary_shields, dealt))
            return dealt

        with patch.object(own, 'damage', side_effect=record_damage):
            match.update(expiry * combat.TICK, expiry, frames=False)
        self.assertEqual({tick for tick, _, _ in hits}, {expiry - 1})
        self.assertEqual(sum(shield for _, shield, _ in hits), shield_damage)
        self.assertEqual(sum(dealt for _, _, dealt in hits), shield_damage)
        self.assertEqual((own.total, own.temporary_shields, own.maximum), (600, 0, 600))
        self.assertEqual((script.frame, script.component.now), frozen)

    def nano_npc_scene(self, name, distance):
        return self.fixture.nano_scenario(self.definition(name), distance=distance)

    def test_actual_nano_reduces_each_npc_primary_and_assassin_pin_damage(self):
        for name, shield_loss, armor_loss in (
            ('Trooper', 10, 5), ('Enforcer', 17, 8.5), ('Sniper', 50, 45),
            ('Assassin', 12.5, 7.5), ('Heavy Assault', 2.5, 1.25),
        ):
            for shield in (False, True):
                with self.subTest(npc=name, shield=shield):
                    distance = 1.5 if name == 'Assassin' else 6
                    run, player, bot, target, own = self.nano_npc_scene(name, distance)
                    fight = run.match.combat
                    self.engage(run, player, target, shield=shield)
                    before, adaptive = own.total, own.temporary_shields
                    self.assertEqual(adaptive, 175 if shield else 0)
                    for _ in range(200):
                        run.step({})
                        if own.total < before:
                            break
                    if name == 'Enforcer':
                        for _ in range(6):
                            run.step({})
                    self.assertLess(run.frame, own.nano_until)
                    loss = shield_loss if shield else armor_loss
                    self.assertAlmostEqual(own.total, before - loss)
                    self.assertAlmostEqual(own.temporary_shields, adaptive - loss if shield else 0)
                    self.assertIsNotNone(bot.body_script)
                    self.assertEqual(fight.is_stunned(player), name == 'Assassin')
                    if name == 'Assassin':
                        self.assertEqual(fight.pins[bot.entity], player.body)
                        instance = next(i for i in bot.body_script.component.instances.values()
                                        if i.graph.index == 0x1157)
                        continuous = instance.state(instance.graph.node(15).state)
                        for _ in range(150):
                            if continuous.active:
                                break
                            run.step({})
                        self.assertTrue(continuous.active)
                        before = own.total
                        for _ in range(10):
                            run.step({})
                        self.assertAlmostEqual(before - own.total, 8 if shield else 4, places=3)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    before = own.total
                    for _ in range(24):
                        run.step({})
                    self.assertAlmostEqual(own.total, before)
                    self.assertFalse(fight.is_stunned(player))

    def test_actual_nano_reduces_npc_melee_and_heavy_charge_with_normal_control(self):
        for name, distance, graph_id, damage in (
            ('Trooper', 1.5, 0x1422, 15), ('Heavy Assault', 1.5, 0x140E, 40),
            ('Heavy Assault', 10, 0x1408, 87.5),
        ):
            for shield in (False, True):
                with self.subTest(npc=name, graph=hex(graph_id), shield=shield):
                    run, player, bot, target, own = self.nano_npc_scene(name, distance)
                    self.engage(run, player, target, shield=shield)
                    instance = next(i for i in bot.body_script.component.instances.values()
                                    if i.graph.index == graph_id)
                    before, adaptive = own.total, own.temporary_shields
                    for _ in range(140):
                        run.step({})
                        if own.total < before:
                            break
                    self.assertLess(run.frame, own.nano_until)
                    self.assertEqual(own.total, before - damage + (0 if shield else 5))
                    self.assertEqual(own.temporary_shields, adaptive - damage if shield else 0)
                    self.assertFalse(bot.attack_input)
                    self.assertFalse(run.match.combat.is_stunned(player))
                    if graph_id == 0x1408:
                        self.assertEqual(instance.vars[59].value(), 2)
                        targets = instance.vars[6836].value()
                    else:
                        targets = instance.vars[375].value()
                    self.assertEqual(targets, (expr.Entity(player.body),))
                    if graph_id == 0x140E:
                        self.assertGreater(player.mover.velocity[1], 7)
                        self.assertLess(player.mover.velocity[2], -19)
                    self.assertEqual(npc_control.stop(run.match), 1)

    def test_current_manual_ranged_npc_attack_timing_after_piledriver(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault'):
            with self.subTest(npc=name):
                run, player, bot, target, own = self.arena(name)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                run.step({player: (0, 0, 16)})
                self.assertEqual(own.temporary_shields, 175)
                lift(run, player)
                cast(run, player, 10)
                for _ in range(100):
                    if bot.airborne:
                        break
                    run.step({})
                self.assertEqual(vitals.total, before - 100)
                self.assertTrue(bot.airborne)
                launched = next(target for target in fight._targets() if target.owner is bot)
                self.engage(run, player, launched, shield=False)
                self.assertEqual(own.temporary_shields, 175)
                for _ in range(100):
                    run.step({})
                    if not bot.airborne:
                        break
                    self.assertTrue(bot.attack_input)
                    if own.temporary_shields < 175:
                        break
                if name == 'Sniper':
                    self.assertFalse(bot.airborne)
                    self.assertEqual(own.temporary_shields, 175)
                    retreat = npc_reposition.movement_state(bot)
                    self.assertTrue(retreat.active)
                    for _ in range(240):
                        run.step({})
                        if own.temporary_shields < 175:
                            break
                    self.assertFalse(retreat.active)
                else:
                    self.assertTrue(bot.airborne)
                    self.assertIn(bot.entity, fight.npc_impulses)
                self.assertLess(own.temporary_shields, 175)
                self.assertEqual(own.total - own.temporary_shields, 600)
                self.assertEqual(npc_control.stop(run.match), 1)
                remaining = own.temporary_shields
                for _ in range(100):
                    run.step({})
                self.assertFalse(bot.airborne)
                self.assertFalse(bot.attack_input)
                self.assertEqual(own.temporary_shields, remaining)

    def test_all_five_actual_attacks_can_kill_ball_and_release_dead_body(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault'):
            with self.subTest(npc=name):
                run, player, bot, target, own = self.arena(name)
                self.engage(run, player, target)
                self.assertEqual(own.temporary_shields, 175)
                old_body = player.body
                old_script = player.body_script
                for _ in range(4500):
                    run.step({})
                    if own.dead:
                        break
                self.assertTrue(own.dead)
                self.assertEqual(own.killer, bot.entity)
                self.assertEqual(own.total, 0)
                fight = run.match.combat
                self.assertNotIn(old_body, fight.pins.values())
                for _ in range(10):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(own.total, 0)
                # Manual engagement follows the player across spawns. Stop it
                # explicitly to check the new body's independent health pools.
                self.assertEqual(npc_control.stop(run.match), 1)

                for _ in range(700):
                    run.step({})
                    if player.body != old_body:
                        break
                self.assertNotEqual(player.body, old_body)
                self.assertTrue(old_script.retired)
                fresh = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(fresh)
                for _ in range(25):
                    run.step({})
                self.assertEqual(vitals.total, 600)
                self.assertFalse(fight.is_stunned(player))

    def test_damaged_adaptive_shield_and_cooldown_retire_with_ball_body(self):
        for name, shield_damage, armor_damage in (
            ('Trooper', 20, 15), ('Enforcer', 34, 17), ('Sniper', 100, 95),
            ('Assassin', 25, 20), ('Heavy Assault', 5, 2.5),
        ):
            with self.subTest(npc=name):
                self.check_adaptive_shield_body_replacement(name, shield_damage, armor_damage)

    def check_adaptive_shield_body_replacement(self, name, shield_damage, armor_damage):
        distance = 1.5 if name == 'Assassin' else 6
        run, player, bot, target, old_vitals = self.arena(name, distance=distance)
        match = run.match
        fight = match.combat
        self.engage(run, player, target)
        self.assertEqual(old_vitals.temporary_shields, 175)
        old_shield = ball.instance(player, 0x1566)
        for _ in range(200):
            run.step({})
            if old_vitals.temporary_shields < 175:
                break
        self.assertEqual(old_vitals.temporary_shields, 175 - shield_damage)
        self.assertEqual(old_vitals.total - old_vitals.temporary_shields, 600)
        self.assertGreater(old_shield.state(0).remaining(), 0)
        self.assertTrue(old_shield.state(6).active)
        old_body, old_script = player.body, player.body_script
        old_pool, old_expiry = old_vitals.temporary_shields, old_vitals.adaptive_until
        position = tuple(player.mover.position)
        self.assertTrue(match.switch_hero(player, heroes()[ball.HERO]))
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertNotIn(old_body, fight.vitals)
        self.assertNotIn(old_body, fight.pins.values())
        self.assertEqual(bot.attack_target, player.entity)
        relocate(run, player, position)
        replacement = next(t for t in fight._targets() if t.owner is player)
        fresh = fight.vitals_of(replacement)
        self.assertEqual((fresh.total, fresh.maximum, fresh.temporary_shields, fresh.adaptive_until),
                         (600, 600, 0, 0))

        for _ in range(200):
            run.step({})
            if fresh.total < 600:
                break
        self.assertEqual(fresh.total, 600 - armor_damage)
        self.assertEqual(fresh.temporary_shields, 0)
        self.assertEqual(old_vitals.temporary_shields, old_pool)
        if name == 'Assassin':
            self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertEqual(npc_control.stop(match), 1)
        for _ in range(65):
            run.step({})
        self.assertLess(run.frame, old_expiry)
        self.assertFalse(fight.is_stunned(player))
        shield = ball.instance(player, 0x1566)
        self.assertIsNot(shield, old_shield)
        self.assertEqual(shield.state(0).remaining(), 0)
        permanent = fresh.total
        target = next(t for t in fight._targets() if t.owner is bot)
        x, y, z = target.feet
        angles = aim(fight._eye(player), (x, y + target.height / 2, z))
        run.step({player: (*angles, 16)})
        self.assertEqual(shield.state(6).counter, 1)
        self.assertGreater(shield.state(0).remaining(), 0)
        self.assertEqual(fresh.temporary_shields, 175)
        self.assertEqual(fresh.total, permanent + 175)
        self.assertGreater(fresh.adaptive_until, old_expiry)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for _ in range(200):
            run.step({})
            if fresh.temporary_shields < 175:
                break
        self.assertEqual(fresh.temporary_shields, 175 - shield_damage)
        self.assertEqual(fresh.total - fresh.temporary_shields, permanent)
        self.assertEqual(old_vitals.temporary_shields, old_pool)

    def test_enforcer_authored_projectile_count_damage_and_interception(self):
        for distance, interruption in ((1.5, None), (15, None), (6, 'stop'), (6, 'wall')):
            with self.subTest(distance=distance, interruption=interruption):
                run, player, bot, target, own = self.arena('Enforcer', distance=distance)
                self.engage(run, player, target)
                fight = run.match.combat
                for _ in range(120):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), 25)
                self.assertEqual(len({rocket.direction for rocket in fight.rockets}), 25)
                self.assertTrue(all(rocket.shooter is bot and rocket.speed == 80 for rocket in fight.rockets))
                before = own.temporary_shields
                if interruption == 'stop':
                    npc_control.stop(run.match)
                    self.assertFalse(fight.rockets)
                if interruption == 'wall':
                    x, y, z = player.mover.position
                    wall = z + 3
                    run.match.collision_world = World([
                        (x - 5, y, wall, x + 5, y, wall, x + 5, y + 5, wall),
                        (x - 5, y, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                    ])
                for _ in range(18):
                    run.step({})
                loss = before - own.temporary_shields
                if interruption or distance == 1.5:
                    self.assertEqual(loss, 0 if interruption else 50)
                    if interruption:
                        self.assertFalse(fight.rockets)
                else:
                    self.assertGreater(loss, 0)
                    self.assertLess(loss, 14)  # seven hits attenuated from two points each

    def test_enforcer_in_flight_volley_uses_replacement_ball_pose_and_pools(self):
        for leaves_lane in (False, True):
            with self.subTest(leaves_lane=leaves_lane):
                self.check_enforcer_volley_across_ball_replacement(leaves_lane)

    def check_enforcer_volley_across_ball_replacement(self, leaves_lane):
        run, player, bot, target, old_vitals = self.arena('Enforcer', distance=6)
        match = run.match
        fight = match.combat
        self.engage(run, player, target)
        for _ in range(120):
            run.step({})
            if old_vitals.temporary_shields < 175:
                break
        self.assertEqual(old_vitals.temporary_shields, 141)
        self.assertEqual(old_vitals.total, 741)
        volley = ()
        for _ in range(120):
            previous = tuple(fight.rockets)
            previous_ids = {id(rocket) for rocket in previous}
            run.step({})
            volley = tuple(rocket for rocket in fight.rockets if id(rocket) not in previous_ids)
            if volley:
                break
        self.assertEqual(len(volley), 25)
        self.assertTrue(all(rocket.shooter is bot and rocket.speed == 80 for rocket in volley))
        self.assertEqual(old_vitals.total, 741)

        old_body, old_script = player.body, player.body_script
        x, y, z = player.mover.position
        self.assertTrue(match.switch_hero(player, heroes()[ball.HERO]))
        self.assertTrue(old_script.retired)
        self.assertNotEqual(player.body, old_body)
        self.assertNotIn(old_body, fight.vitals)
        relocate(run, player, (x + 8 if leaves_lane else x, y, z))
        replacement = next(t for t in fight._targets() if t.owner is player)
        fresh = fight.vitals_of(replacement)
        self.assertEqual((fresh.total, fresh.temporary_shields), (600, 0))
        retained = tuple(fight.rockets)
        retained_ids = {id(rocket) for rocket in retained}
        self.assertLessEqual({id(rocket) for rocket in volley}, retained_ids)
        buttons = {} if leaves_lane else {player: (0, 0, 16)}
        for _ in range(10):
            run.step(buttons)
            self.assertLessEqual({id(rocket) for rocket in fight.rockets}, retained_ids)
        self.assertEqual(fresh.temporary_shields, 0)
        self.assertEqual(fresh.total, 600 if leaves_lane else 583)
        if not leaves_lane:
            ability = ball.instance(player, 0x1566).state(0)
            self.assertEqual(ability.remaining(), 0)
            for _ in range(14):
                run.step(buttons)
                self.assertEqual(fresh.temporary_shields, 0)
                self.assertEqual(fresh.total, 583)
            run.step(buttons)
            self.assertEqual(fresh.temporary_shields, 175)
            self.assertGreater(ability.remaining(), 0)
            for _ in range(200):
                run.step({})
                if fresh.temporary_shields < 175:
                    break
            self.assertEqual(fresh.temporary_shields, 141)
            self.assertEqual(fresh.total - fresh.temporary_shields, 583)
        self.assertEqual((old_vitals.total, old_vitals.temporary_shields), (741, 141))
        self.assertNotIn(old_body, fight.vitals)

    def test_enforcer_in_flight_volley_survives_death_until_explicit_removal(self):
        for lethal, remove_corpse in ((False, False), (True, False), (True, True)):
            with self.subTest(lethal=lethal, remove_corpse=remove_corpse):
                self.check_enforcer_volley_after_source_death(lethal, remove_corpse)

    def check_enforcer_volley_after_source_death(self, lethal, remove_corpse):
        run, player, soldier, bot, target, own, _ = self.support_scene(SOLDIER, 'Enforcer', distance=6)
        fight = run.match.combat
        npc_health = fight.vitals_of(target)
        npc_health.damage(npc_health.total - 5, bypass_armour=True)
        fight._sync_body_health(bot, npc_health)
        if lethal:
            own.damage(own.total - 10, bypass_armour=True)
            fight._sync_body_health(player, own)
        self.engage(run, player, target, shield=not lethal)
        for _ in range(120):
            run.step({})
            if fight.rockets:
                break
        volley = tuple(fight.rockets)
        self.assertEqual(len(volley), 25)
        self.assertTrue(all(rocket.shooter is bot and rocket.speed == 80 for rocket in volley))
        self.assertEqual(own.total, 10 if lethal else 775)
        x, y, z = bot.position
        shot = aim(fight._eye(soldier), (x, y + target.height / 2, z))
        run.step({soldier: (*shot, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(npc_health.killer, soldier.body)
        self.assertEqual(own.total, 10 if lethal else 775)
        projectile_ids = {id(rocket) for rocket in volley}
        self.assertEqual({id(rocket) for rocket in fight.rockets}, projectile_ids)
        corpse = bot.position
        if remove_corpse:
            self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
            self.assertFalse(fight.rockets)

        for _ in range(16):
            run.step({})
            self.assertLessEqual({id(rocket) for rocket in fight.rockets}, projectile_ids)
            self.assertEqual(bot.position, corpse)
        if remove_corpse:
            self.assertEqual(own.total, 10)
            self.assertFalse(player.client.messages(combat.DEATH_NOTICE))
            self.assertNotIn(bot.entity, fight.vitals)
            self.assertFalse(fight.rockets)
        elif lethal:
            self.assertTrue(own.dead)
            self.assertEqual(own.killer, bot.entity)
            self.assertGreater(own.death_tick, npc_health.death_tick)
            notice, = player.client.messages(combat.DEATH_NOTICE)
            self.assertEqual(notice["+0x7C"]["+0x0"], bot.entity)
        else:
            self.assertEqual(own.temporary_shields, 141)
            self.assertEqual(own.total - own.temporary_shields, 600)

    def test_dead_enforcer_volley_hits_before_shield_expiry_during_world_gap(self):
        latency = self.npc_opening_hit_latency('Enforcer', 6, 34)
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                self.check_dead_enforcer_volley_crosses_shield_expiry(latency, skipped)

    def check_dead_enforcer_volley_crosses_shield_expiry(self, latency, skipped):
        run, player, bot, own = self.npc_shield_deadline_scene('Enforcer', 6)
        match = run.match
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        source_health = fight.vitals_of(target)
        source_health.damage(source_health.total - 5, bypass_armour=True)
        fight._sync_body_health(bot, source_health)
        expiry = own.adaptive_until
        start = expiry - 1 - latency
        match.update(start * combat.TICK, start, frames=False)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for tick in range(start + 1, expiry - 1):
            match.update(tick * combat.TICK, tick, frames=False)
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 25)
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(player), (x, y + target.height / 2, z))
        player.take_commands([Command(tick, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertTrue(bot.dead)
        self.assertEqual(source_health.killer, player.body)
        self.assertEqual(own.total, 775)
        self.assertEqual(len(fight.rockets), 25)
        script = player.body_script
        frozen = script.frame, script.component.now
        hits = []
        damage = own.damage

        def record_damage(amount, **kwargs):
            shield_before = own.temporary_shields
            dealt = damage(amount, **kwargs)
            hits.append((fight.tick, shield_before - own.temporary_shields, dealt))
            return dealt

        with patch.object(own, 'damage', side_effect=record_damage):
            updates = (expiry,) if skipped else range(tick + 1, expiry + 1)
            for frame in updates:
                match.update(frame * combat.TICK, frame, frames=False)
        self.assertEqual({frame for frame, _, _ in hits}, {expiry - 1})
        self.assertEqual(sum(shield for _, shield, _ in hits), 34)
        self.assertEqual(sum(dealt for _, _, dealt in hits), 34)
        self.assertEqual((own.total, own.temporary_shields), (600, 0))
        self.assertEqual((script.frame, script.component.now), frozen)

    def projected_npc_scene(self, name, distance, *, aim_ticks=0, pin_before_cast=False):
        run, player, support, _, _ = grapple_tests.scene(distance=None)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        support.team = player.team
        run.match.switch_hero(support, heroes()[barriers.ZARYA])
        for actor in run.players:
            actor.client = pve_tests.PveClient(actor)
        x, y, z = player.mover.position
        relocate(run, support, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        bot = npc.spawn(run.match, self.definition(name), (x, y, z + distance),
                        team=1 - player.team)
        target = next(t for t in fight._targets() if t.owner is bot)
        own = fight.vitals[player.body]
        if pin_before_cast:
            self.engage(run, player, target, shield=False)
            self.wait_pin(run, player)
        elif aim_ticks:
            self.engage(run, player, target, shield=False)
            for _ in range(aim_ticks):
                run.step({})
            self.assertEqual(own.total, 600)
        angles = aim(fight._eye(support), (x, y + 1, z))
        run.step({support: (*angles, 16)})
        for _ in range(12):
            run.step({support: (*angles, 0)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, player)
        if not aim_ticks and not pin_before_cast:
            self.engage(run, player, target, shield=False)
        return run, player, support, bot, target, own, bubble

    def test_actual_enforcer_projectiles_damage_projected_barrier_before_ball(self):
        run, _, support, bot, _, own, bubble = self.projected_npc_scene('Enforcer', 1.5)
        fight = run.match.combat
        for _ in range(80):
            run.step({})
            if any(rocket.shooter is bot for rocket in fight.rockets):
                break
        self.assertEqual(sum(rocket.shooter is bot for rocket in fight.rockets), 25)
        for _ in range(18):
            run.step({})
        self.assertEqual(bubble.vitals.total, 150)
        self.assertEqual(own.total, 600)
        self.assertAlmostEqual(support.body_script.component.entity_var(87).value(), 10, places=5)

    def test_actual_ranged_npc_hits_damage_projected_barrier_then_ball_after_expiry(self):
        for name, damage in (('Trooper', 20), ('Sniper', 100), ('Heavy Assault', 5)):
            with self.subTest(npc=name):
                run, player, support, _, target, own, bubble = self.projected_npc_scene(
                    name, 6, aim_ticks=65 if name == 'Sniper' else 0,
                )
                for _ in range(125):
                    run.step({})
                    if bubble.vitals.total < 200 or bubble.retired:
                        break
                self.assertFalse(bubble.retired)
                self.assertEqual(bubble.vitals.total, 200 - damage)
                self.assertEqual(own.total, 600)
                self.assertAlmostEqual(support.body_script.component.entity_var(87).value(), damage / 5)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(125):
                    run.step({})
                    if bubble.retired:
                        break
                self.assertTrue(bubble.retired)
                self.assertEqual(own.total, 600)
                self.engage(run, player, target, shield=False)
                for _ in range(200):
                    run.step({})
                    if own.total < 600:
                        break
                self.assertEqual(own.total, 600 - damage + min(5, damage / 2))

    def test_actual_npc_melee_and_charge_hit_projected_barrier_before_ball(self):
        for name, distance, damage in (('Trooper', 1.5, 30), ('Heavy Assault', 1.5, 80),
                                       ('Heavy Assault', 10, 175)):
            with self.subTest(npc=name, distance=distance):
                run, player, support, bot, _, own, bubble = self.projected_npc_scene(name, distance)
                match = run.match
                x, y, z = player.mover.position
                match.collision_world = flat_world(y)
                match.collision_provider = StaticCollisionProvider(match.collision_world)
                player.mover.use(world=match.collision_world)
                support.mover.use(world=match.collision_world)
                for _ in range(110):
                    run.step({})
                    if bubble.vitals.total < 200 or own.total < 600:
                        break
                self.assertFalse(bubble.retired)
                self.assertEqual(bubble.vitals.total, 200 - damage)
                self.assertEqual(own.total, 600)
                self.assertEqual(tuple(player.mover.position), (x, y, z))
                self.assertEqual(player.mover.velocity, (0, 0, 0))
                self.assertAlmostEqual(support.body_script.component.entity_var(87).value(), damage / 5)
                if distance == 10:
                    charge = npc_charge._ability(bot)
                    self.assertEqual(charge.instance.vars[59].value(), 2)
                    self.assertFalse(match.combat.is_stunned(player))
                self.assertEqual(npc_control.stop(match), 1)
                for _ in range(125):
                    run.step({})
                    if bubble.retired:
                        break
                self.assertTrue(bubble.retired)
                self.assertEqual(own.total, 600)
                self.assertEqual(player.mover.velocity, (0, 0, 0))

    def test_projected_barrier_prevents_heavy_melee_impulse_on_tethered_ball(self):
        run, player, support, bot, _, own, bubble = self.projected_npc_scene('Heavy Assault', 1.5)
        fight = run.match.combat
        wall = self.prepare_rear_hook_wall(run, player, warmup=0)
        hook = {player: (-32768, 0, 2)}
        for _ in range(8):
            run.step(hook)
        tether = fight.ball_grapples[player.body]
        self.assertTrue(tether.attached)
        self.assertAlmostEqual(tether.anchor[2], wall, places=4)
        self.assertEqual(bubble.vitals.total, 200)
        position = tuple(player.mover.position)
        for _ in range(60):
            run.step(hook)
            if bubble.vitals.total < 200 or own.total < 600:
                break
        melee = npc_melee.ability(bot.body_script.component)
        self.assertEqual(melee.instance.graph.index, 0x140E)
        self.assertEqual(melee.instance.vars[375].value(), (expr.Entity(player.body),))
        self.assertEqual(bubble.vitals.total, 120)
        self.assertEqual(support.body_script.component.entity_var(87).value(), 16)
        self.assertEqual(own.total, 600)
        self.assertIs(fight.ball_grapples[player.body], tether)
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual(player.mover.velocity, (0, 0, 0))
        self.assertFalse(player.mover.airborne)

    def test_assassin_0055_relay_forwards_only_incoming_damage_amount(self):
        _, _, _, bot, _, _, bubble = self.projected_npc_scene(
            'Assassin', 1.5, pin_before_cast=False,
        )
        component = bot.body_script.component
        pin = next(i for i in component.instances.values() if i.graph.index == 0x1157)
        world = component.world
        native = world.native

        def resolve_owner(cfg, instance):
            if instance is pin and cfg.get('$') == 'STU_F7262B36':
                return expr.Entity(bubble.entity)
            return native(cfg, instance)

        incoming = {0x13: 10.0, 0x12: expr.Entity(bubble.entity),
                    0x3D: expr.Asset(0x0D8000000000302F)}
        # Supply the unresolved target and observe the authored sender's parameters.
        with (
            patch.object(world, 'native', side_effect=resolve_owner),
            patch.object(world, 'send_message') as sent,
        ):
            runtime.send_instance_message(pin, 0x0240000000000011, incoming, bubble.entity)
            component.run_now()
        sent.assert_called_once_with(bubble.entity, 0x0240000000000055, {0x2A: 10.0}, bot.entity)

    def test_projected_barrier_child_admits_injected_assassin_tag_to_modify_health(self):
        for tag, expected_entries in ((0, 1), (0x0D8000000000191F, 0), (0x0D8000000000302F, 1)):
            with self.subTest(tag=hex(tag)):
                _, _, _, bot, _, _, bubble = self.projected_npc_scene(
                    'Assassin', 1.5, pin_before_cast=False,
                )
                child = next(i for i in bubble.component.instances.values() if i.graph.index == 0x256)
                damage = child.state(child.graph.node(21).state)
                before = damage.counter
                # Inject the child message directly. The native producer is still unknown.
                params = {0x2A: 10.0, 0x12: expr.Entity(bot.entity), 0x3D: expr.Asset(tag)}
                runtime.send_instance_message(child, 0x0240000000000055, params, bot.entity)
                bubble.component.run_now()
                self.assertEqual(damage.counter - before, expected_entries)
                self.assertEqual(bubble.vitals.total, 200)

    def test_current_manual_pin_damage_phases_bypass_projected_barrier(self):
        # Record the authored discriminator separately from the current server policy.
        for already_pinned in (False, True):
            with self.subTest(already_pinned=already_pinned):
                run, player, support, bot, _, own, bubble = self.projected_npc_scene(
                    'Assassin', 1.5, pin_before_cast=already_pinned,
                )
                fight = run.match.combat
                pin = next(instance for instance in bot.body_script.component.instances.values()
                           if instance.graph.index == 0x1157)
                for pos, amount_var in ((15, 11984), (25, 13230)):
                    modify = pin.graph.node(pos).field('m_modifyHealth')
                    tags = [tag['m_EE3055BD']['m_identifier'] for tag in modify['m_148F3152']]
                    self.assertEqual(tags, ['0x0D8000000000302F'])
                    self.assertEqual(modify['m_amount']['m_identifier'], amount_var)
                self.assertEqual(fight.pins.get(bot.entity), player.body if already_pinned else None)
                energy = support.body_script.component.entity_var(87)
                before_energy = energy.value()
                self.wait_pin(run, player)
                if not already_pinned:
                    self.assertEqual(own.total, 580)
                continuous = pin.state(pin.graph.node(15).state)
                for _ in range(65):
                    if continuous.active:
                        break
                    run.step({})
                self.assertTrue(continuous.active)
                before = own.total
                for _ in range(10):
                    run.step({})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertAlmostEqual(before - own.total, 8, places=3)
                self.assertFalse(bubble.retired)
                self.assertEqual(bubble.vitals.total, 200)
                self.assertEqual(energy.value(), before_energy)
                for _ in range(125):
                    run.step({})
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    if bubble.retired:
                        break
                self.assertTrue(bubble.retired)
                self.assertEqual(bubble.vitals.total, 200)
                self.assertEqual(energy.value(), before_energy)
                health_at_expiry = own.total
                for _ in range(10):
                    run.step({})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertAlmostEqual(health_at_expiry - own.total, 8, places=3)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.is_stunned(player))

    @unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA_DIR'), 'prepared Reinhardt mesh required')
    def test_reinhardt_shield_intercepts_actual_enforcer_projectiles(self):
        data = Path(os.environ['ORPHEUS_BARRIER_DATA_DIR'])
        self.enterContext(patch.object(barrier_geometry, 'DATA_DIR', data))
        for shield_up in (False, True):
            with self.subTest(shield_up=shield_up):
                run, player, rein, _, _ = grapple_tests.scene(distance=None)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                rein.team = player.team
                run.match.switch_hero(rein, heroes()[barriers.REINHARDT])
                for actor in run.players:
                    actor.client = pve_tests.PveClient(actor)
                x, y, z = player.mover.position
                relocate(run, rein, (x + 1.25, y, z + 2))
                for _ in range(40):
                    run.step({rein: (0, 0, 2)})
                fight = run.match.combat
                shield, = fight.barriers.values()
                self.assertTrue(shield.mesh)
                self.assertEqual(shield.vitals.total, 1600)
                bot = npc.spawn(run.match, self.definition('Enforcer'), (x, y, z + 6),
                                team=1 - player.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                hold = 2 if shield_up else 0
                angles = aim(fight._eye(player),
                             (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
                run.step({player: (*angles, 0), rein: (0, 0, hold)})
                self.assertEqual(npc_control.start(run.match, player), target.entity)
                for _ in range(80):
                    run.step({rein: (0, 0, hold)})
                    if any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                self.assertEqual(sum(rocket.shooter is bot for rocket in fight.rockets), 25)
                for _ in range(18):
                    run.step({rein: (0, 0, hold)})
                if shield_up:
                    self.assertFalse(shield.retired)
                    self.assertEqual(shield.vitals.total, 1550)
                    self.assertEqual(fight.vitals[player.body].total, 600)
                else:
                    self.assertTrue(shield.retired)
                    self.assertEqual(shield.vitals.total, 1600)
                    self.assertLess(fight.vitals[player.body].total, 600)

    def test_winston_shell_intercepts_enforcer_pellets_on_boundary_crossing(self):
        for dome_offset, blocked in ((0, True), (6, True), (3, False)):
            with self.subTest(dome_offset=dome_offset):
                run, player, winston, _, own = grapple_tests.scene(distance=None)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                winston.team = player.team
                run.match.switch_hero(winston, heroes()[barriers.WINSTON])
                for actor in run.players:
                    actor.client = pve_tests.PveClient(actor)
                x, y, z = player.mover.position
                relocate(run, winston, (x, y, z + dome_offset))
                for _ in range(40):
                    run.step({})
                run.step({winston: (0, 0, 16)})
                fight = run.match.combat
                for _ in range(45):
                    if fight.barriers:
                        break
                    run.step({})
                dome, = fight.barriers.values()
                self.assertEqual(dome.vitals.total, 700)
                relocate(run, winston, (x + 30, y, z))
                bot = npc.spawn(run.match, self.definition('Enforcer'), (x, y, z + 6),
                                team=1 - player.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                self.engage(run, player, target, shield=False)
                for _ in range(80):
                    run.step({})
                    if fight.rockets:
                        break
                pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                self.assertEqual(len(pellets), 25)
                self.assertEqual(math.dist(pellets[0].position, dome.centre) < barriers.DOME_RADIUS,
                                 dome_offset != 0)
                self.assertEqual(math.dist(player.mover.position, dome.centre) < barriers.DOME_RADIUS,
                                 dome_offset != 6)
                for _ in range(18):
                    run.step({})
                self.assertEqual(dome.vitals.total, 650 if blocked else 684)
                self.assertEqual(own.total, 600 if blocked else 583)

    @unittest.skipUnless(os.environ.get('ORPHEUS_DVA_INPUT_DIR'), 'prepared 0407 inputs required')
    def test_allied_matrix_intercepts_enforcer_pellets_before_ball(self):
        use_mech_fixture(self)
        for front in (True, False):
            with self.subTest(front=front):
                self.check_enforcer_matrix(front)

    def check_enforcer_matrix(self, front):
        run, player, dva, bot, target, own, _ = self.support_scene(graph.DVA, 'Enforcer', distance=6)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(dva, replace(heroes()[graph.DVA], body=graph.DVA_MECH)))
        x, y, z = player.mover.position
        relocate(run, dva, (x, y, z - 1.5))
        for _ in range(100):
            run.step({})
        mech = fight.vitals_of(next(t for t in fight._targets() if t.owner is dva))
        matrix = next(i for i in dva.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.engage(run, player, target, shield=False)
        held = {dva: (0 if front else -32768, 0, 2)}
        for _ in range(80):
            run.step(held)
            if fight.rockets:
                break
        pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
        self.assertEqual(len(pellets), 25)
        self.assertTrue(all(rocket.deflectable for rocket in pellets))
        self.assertTrue(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 100)
        for _ in range(18):
            run.step(held)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(own.total, 600 if front else 583)
        if not front:
            return
        self.assertEqual(mech.total, 600)
        self.assertFalse(fight.rockets)

        for _ in range(30):
            run.step({})
            if not matrix.state(3).active:
                break
        self.assertFalse(matrix.state(3).active)
        for _ in range(160):
            run.step({})
            if own.total < 600:
                break
        self.assertLess(own.total, 600)

    def test_allied_grasp_absorbs_enforcer_pellets_and_grants_shields(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                self.check_enforcer_grasp(blocked)

    def check_enforcer_grasp(self, blocked):
        run, player, sigma, bot, target, own, _ = self.support_scene(deflection.SIGMA, 'Enforcer', distance=6)
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        relocate(run, sigma, (x + 0.75, y, z + 2))
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        before = health.total
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.engage(run, player, target, shield=False)
        for frame in range(80):
            run.step({sigma: (0, 0, 8 if frame == 0 else 0)})
            if fight.rockets:
                break
        self.assertEqual(sum(rocket.shooter is bot for rocket in fight.rockets), 25)
        self.assertTrue(grasp.state(1).active)
        if blocked:
            old = match.collision_world
            triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
            wall = z + 5.75
            triangles.extend([
                (x - 5, y, wall, x + 5, y, wall, x + 5, y + 6, wall),
                (x - 5, y, wall, x + 5, y + 6, wall, x - 5, y + 6, wall),
            ])
            match.collision_world = World(triangles)
            match.collision_provider = StaticCollisionProvider(match.collision_world)
        for _ in range(18):
            run.step({sigma: (0, 0, 0)})
        self.assertEqual(own.total, 600)
        self.assertEqual(health.total, before)
        absorbed = grasp.vars[21312].value()
        self.assertEqual(absorbed, 0 if blocked else 50)
        self.assertEqual(health.temporary_shields, 0)
        self.assertEqual(npc_control.stop(match), 1)
        for _ in range(160):
            run.step({})
            if not grasp.state(1).active:
                break
        self.assertFalse(grasp.state(1).active)
        self.assertAlmostEqual(health.temporary_shields, absorbed * 0.6, places=4)
        self.assertAlmostEqual(health.total, before + health.temporary_shields, places=4)
        self.assertEqual(own.total, 600)

    def test_allied_deflect_returns_enforcer_pellets_with_genji_credit(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.check_enforcer_deflect(lethal)

    def check_enforcer_deflect(self, lethal):
        run, player, genji, bot, target, own, before = self.support_scene(
            deflection.GENJI, 'Enforcer', distance=6,
        )
        fight = run.match.combat
        genji_vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        genji_health = genji_vitals.total
        for actor in (player, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        pellets = self.launch_enforcer_deflect(run, player, genji, bot, target)
        enemy = fight.vitals_of(target)
        if lethal:
            enemy.now[combat.ARMOUR_POOL] = 0
            enemy.now[combat.SHIELDS_POOL] = 0
            enemy.now[combat.HEALTH_POOL] = 6
            before = enemy.total
            fight._sync_body_health(bot, enemy)
        for _ in range(24):
            run.step({genji: (0, 0, 0)})
        returned = [rocket for rocket in pellets if rocket.shooter is genji]
        self.assertTrue(returned)
        self.assertTrue(all(rocket.team == genji.team and rocket.reflections == 1 for rocket in returned))
        self.assertTrue(all(rocket.direction[2] > 0 for rocket in returned))
        self.assertEqual(own.total, 600)
        self.assertEqual(genji_vitals.total, genji_health)
        damage = before - enemy.total
        self.assertGreater(damage, 0)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), damage)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if lethal:
            self.assertEqual(damage, 6)
            self.assertTrue(bot.dead)
            self.assertEqual(enemy.killer, genji.body)
            self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)

    def launch_enforcer_deflect(self, run, player, genji, bot, target):
        fight = run.match.combat
        instances = genji.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == 0x03A3)
        self.engage(run, player, target, shield=False)
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 2))
        for frame in range(80):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
            if fight.rockets:
                break
        pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
        self.assertEqual(len(pellets), 25)
        self.assertTrue(instance.state(10).active)
        return pellets

    def test_enforcer_return_obeys_cover_teams_and_reflector_body_retirement(self):
        for case in ('wall', 'ally', 'enemy', 'replacement'):
            with self.subTest(case=case):
                run, player, genji, bot, target, own, before = self.support_scene(
                    deflection.GENJI, 'Enforcer', distance=6,
                )
                match = run.match
                fight = match.combat
                genji.body_script.ult_seconds = 0
                genji.body_script.set_ult_charge(0)
                self.launch_enforcer_deflect(run, player, genji, bot, target)
                for _ in range(10):
                    run.step({genji: (0, 0, 0)})
                    returned = [rocket for rocket in fight.rockets if rocket.shooter is genji]
                    # Wait for a reflected ray which would still hit the original target.
                    aimed = next((rocket for rocket in returned if combat.ray_capsule(
                        rocket.position, rocket.direction, *target.segment(), target.radius,
                    ) is not None), None)
                    if aimed is not None:
                        break
                self.assertIsNotNone(aimed)
                self.assertEqual(fight.vitals_of(target).total, before)
                self.assertEqual(npc_control.stop(match), 1)
                self.assertTrue(all(rocket in fight.rockets for rocket in returned))
                x, y, z = bot.position
                near = max(rocket.position[2] for rocket in returned)
                self.assertLess(near, z - target.radius)
                middle = (near + z - target.radius) / 2

                if case == 'wall':
                    old = match.collision_world
                    triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
                    triangles.extend([
                        (x - 4, y, middle, x + 4, y, middle, x + 4, y + 5, middle),
                        (x - 4, y, middle, x + 4, y + 5, middle, x - 4, y + 5, middle),
                    ])
                    match.collision_world = World(triangles)
                    match.collision_provider = StaticCollisionProvider(match.collision_world)
                elif case == 'replacement':
                    previous_body = genji.body
                    self.assertTrue(match.switch_hero(genji, heroes()[deflection.GENJI]))
                    self.assertNotEqual(genji.body, previous_body)
                    genji.body_script.ult_seconds = 0
                    genji.body_script.set_ult_charge(0)
                    self.assertFalse(any(rocket.shooter is genji for rocket in fight.rockets))
                else:
                    blocker = npc.spawn(match, self.definition('Trooper'), (aimed.position[0], y, middle),
                                        team=genji.team if case == 'ally' else bot.team)
                    body = next(t for t in fight._targets() if t.owner is blocker)
                    self.assertIsNotNone(combat.ray_capsule(
                        aimed.position, aimed.direction, *body.segment(), body.radius,
                    ))
                    blocker_health = fight.vitals_of(body)
                    blocker_before = blocker_health.total
                for _ in range(24):
                    run.step({})
                if fight.rockets:
                    run.frame += 100
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(own.total, 600)
                damage = before - fight.vitals_of(target).total
                if case in ('wall', 'replacement'):
                    self.assertEqual(damage, 0)
                elif case == 'ally':
                    self.assertGreater(damage, 0)
                    self.assertEqual(blocker_health.total, blocker_before)
                else:
                    self.assertLess(blocker_health.total, blocker_before)
                    damage += blocker_before - blocker_health.total
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), damage)

    def test_opposing_grasp_consumes_enforcer_pellets_after_genji_deflect(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.check_enforcer_return_absorption(deflection.SIGMA, active=active)

    @unittest.skipUnless(os.environ.get('ORPHEUS_DVA_INPUT_DIR'), 'prepared 0407 inputs required')
    def test_opposing_matrix_consumes_enforcer_pellets_after_genji_deflect(self):
        use_mech_fixture(self)
        self.check_enforcer_return_absorption(graph.DVA, active=True)

    def enforcer_return_defense_scene(self, defender_hero, *, distance=6, support_hero=None, ana=False):
        definition = self.definition('Enforcer')
        names = ('Ball', 'Genji', 'Defender')
        game_map = pve_tests.KANEZAKA_DM
        if support_hero is not None:
            names += ('Support',)
            game_map = replace(pve_tests.PRACTICE_RANGE, team_sizes=(6, 6))
        if ana:
            names += ('Ana',)
        run, player, bot, target, own = self.fixture.scenario(
            definition, distance=distance, names=names, game_map=game_map,
        )
        _, genji, defender = run.players[:3]
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        genji.team, defender.team = player.team, bot.team
        self.assertTrue(match.switch_hero(genji, heroes()[deflection.GENJI]))
        mech = defender_hero == graph.DVA
        hero = heroes()[defender_hero]
        self.assertTrue(match.switch_hero(defender, replace(hero, body=graph.DVA_MECH) if mech else hero))
        x, y, z = player.mover.position
        relocate(run, genji, (x + 2, y, z))
        relocate(run, defender, (x + 20, y, z))
        if support_hero is not None:
            support = run.players[3]
            support.team = player.team
            self.assertTrue(match.switch_hero(support, heroes()[support_hero]))
            relocate(run, support, (x + 6, y, z))
            relocate(run, genji, (x + 2, y, z - 6))
        if ana:
            ally = run.players[-1]
            ally.team = player.team
            self.assertTrue(match.switch_hero(ally, heroes()[pve_tests.ANA]))
            relocate(run, ally, (x + 6, y, z + 2))
        for _ in range(100):
            run.step({})
        for actor in (player, genji, defender):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        return run, player, genji, defender, bot, target, own

    def check_enforcer_return_absorption(self, defender_hero, *, active):
        run, player, genji, defender, bot, target, own = self.enforcer_return_defense_scene(defender_hero)
        match = run.match
        fight = match.combat
        mech = defender_hero == graph.DVA
        x, y, z = player.mover.position
        guard = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
        enemy = fight.vitals_of(target)
        guard_before, enemy_before = guard.total, enemy.total
        instances = defender.body_script.component.instances.values()
        graph_index, state_index, button = (0x02CC, 3, 2) if mech else (0x1D02, 1, 8)
        defense = next(i for i in instances if i.graph.index == graph_index)
        self.engage(run, player, target, shield=False)
        relocate(run, genji, (x, y, z + 2))
        relocate(run, defender, (x, y, z + 4))
        for frame in range(80):
            run.step({genji: (0, 0, 16 if frame == 0 else 0),
                      defender: (-32768, 0, button if active and (mech or frame == 0) else 0)})
            if fight.rockets:
                break
        pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
        self.assertEqual(len(pellets), 25)
        self.assertEqual(defense.state(state_index).active, active)
        for _ in range(20):
            run.step({defender: (-32768, 0, button if active and mech else 0)})
        self.assertTrue(any(rocket.shooter is genji for rocket in pellets))
        self.assertEqual(own.total, 600)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        damage = guard_before - guard.total + enemy_before - enemy.total
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), damage)
        if not active:
            self.assertLess(guard.total, guard_before)
            return
        self.assertEqual(damage, 0)
        self.assertEqual(npc_control.stop(match), 1)
        if mech:
            self.assertLess(defense.vars[87].value(), 100)
        else:
            absorbed = defense.vars[21312].value()
            self.assertGreater(absorbed, 0)
            self.assertLessEqual(absorbed, 50)
            for _ in range(160):
                run.step({})
                if not defense.state(state_index).active:
                    break
            self.assertFalse(defense.state(state_index).active)
            self.assertAlmostEqual(guard.temporary_shields, absorbed * 0.6, places=4)
        self.assertEqual(defender.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_second_deflect_returns_enforcer_pellets_to_ball_with_last_owner_credit(self):
        run, player, genji, opponent, bot, target, own, pellets = self.launch_opposing_deflects(
            distance=10, opposing_offset=7,
        )
        fight = run.match.combat
        lifetime = pellets[0].left
        self.assertEqual(opponent.team, bot.team)
        enemy = fight.vitals_of(target)
        before = enemy.total
        pose = {opponent: (-32768, 0, 0)}
        for _ in range(20):
            run.step(pose)
            returned = [r for r in fight.rockets if r.shooter is opponent and r.reflections == 2]
            if returned:
                break
        self.assertTrue(returned)
        self.assertTrue(all(rocket.team == opponent.team and rocket.direction[2] < 0 for rocket in returned))
        for rocket in returned:
            self.assertGreater(rocket.flown, 0)
            self.assertAlmostEqual(rocket.left + rocket.flown, lifetime)
        self.assertEqual(own.total, 600)
        self.assertEqual(enemy.total, before)
        self.assertEqual(npc_control.stop(run.match), 1)
        x, y, z = genji.mover.position
        relocate(run, genji, (x + 10, y, z))
        for _ in range(24):
            run.step(pose)
        damage = 600 - own.total
        self.assertGreater(damage, 0)
        self.assertTrue(all(rocket in pellets for rocket in returned))
        self.assertEqual(enemy.total, before)
        self.assertAlmostEqual(opponent.stats.value(stats.HERO_DAMAGE), damage, places=4)
        opponent_charge = opponent.body_script.component.entity_var(ULT_CHARGE)
        self.assertAlmostEqual(opponent_charge.value(), damage, places=4)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_double_deflected_enforcer_volley_keeps_timing_after_source_removal(self):
        stepped = self.enforcer_return_after_source_removal(skipped=False)
        skipped = self.enforcer_return_after_source_removal(skipped=True)
        self.assertEqual(skipped, stepped)

    def enforcer_return_after_source_removal(self, *, skipped, scene=None, shield=False):
        if scene is None:
            scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=10)
        launch_start = scene[0].frame
        run, player, genji, opponent, bot, _, own, _ = self.fire_into_opposing_deflects(
            scene, opposing_offset=7, shield=shield,
        )
        shield_before = own.temporary_shields
        if shield:
            self.assertEqual(shield_before, 100)
        match = run.match
        fight = match.combat
        for _ in range(20):
            run.step({opponent: (-32768, 0, 0)})
            returned = [
                rocket for rocket in fight.rockets if rocket.shooter is opponent and rocket.reflections == 2
            ]
            if returned:
                break
        self.assertTrue(returned)
        self.assertEqual(own.temporary_shields, shield_before)
        self.assertEqual(own.total, 600 + shield_before)
        self.assertEqual(npc.remove_spawned(match), (bot.entity,))
        self.assertTrue(all(rocket in fight.rockets for rocket in returned))
        self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
        x, y, z = genji.mover.position
        relocate(run, genji, (x + 10, y, z))
        for actor in (player, genji, opponent):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        opponent_damage = opponent.stats.value(stats.HERO_DAMAGE)
        start = run.frame
        end = start + 16
        hits = []
        damage = own.damage

        def record_damage(amount, **kwargs):
            dealt = damage(amount, **kwargs)
            hits.append((fight.tick - launch_start, dealt))
            return dealt

        with patch.object(own, 'damage', side_effect=record_damage):
            for tick in (end,) if skipped else range(start + 1, end + 1):
                match.update(tick * combat.TICK, tick, frames=False)
        dealt = sum(amount for _, amount in hits)
        permanent_damage = 600 - (own.total - own.temporary_shields)
        self.assertGreater(dealt, 0)
        self.assertAlmostEqual(opponent.stats.value(stats.HERO_DAMAGE) - opponent_damage, dealt, places=4)
        self.assertAlmostEqual(opponent.body_script.component.entity_var(ULT_CHARGE).value(),
                               permanent_damage, places=4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if shield:
            self.assertEqual(permanent_damage, 0)
            self.assertAlmostEqual(own.temporary_shields, shield_before - dealt, places=4)
        return hits

    def test_reflected_enforcer_volley_obeys_adaptive_shield_expiry(self):
        baseline = self.enforcer_return_after_source_removal(skipped=False)
        for early in (False, True):
            with self.subTest(early=early):
                stepped = self.enforcer_at_adaptive_shield_expiry(baseline, early, skipped=False)
                skipped = self.enforcer_at_adaptive_shield_expiry(baseline, early, skipped=True)
                self.assertEqual(skipped, stepped)

    def test_reflected_enforcer_volley_obeys_simultaneous_shield_expiry(self):
        scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=10, support_hero=barriers.ZARYA)
        baseline = self.enforcer_return_after_source_removal(skipped=False, scene=scene)
        for nano in (False, True):
            with self.subTest(nano=nano):
                stepped = self.enforcer_at_adaptive_shield_expiry(
                    baseline, True, skipped=False, projected=True, nano=nano,
                )
                skipped = self.enforcer_at_adaptive_shield_expiry(
                    baseline, True, skipped=True, projected=True, nano=nano,
                )
                self.assertEqual(skipped, stepped)

    def enforcer_at_adaptive_shield_expiry(self, baseline, early, *, skipped, projected=False, nano=False):
        support = barriers.ZARYA if projected else None
        scene = self.enforcer_return_defense_scene(
            deflection.GENJI, distance=10, support_hero=support, ana=nano,
        )
        run, player, _, _, _, _, own = scene
        match = run.match
        run.step({player: (0, 0, 16)})
        self.assertEqual(own.temporary_shields, 100)
        deadline = own.adaptive_until
        if projected:
            run.frame = deadline - 126
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            zarya = run.players[3]
            run.step({zarya: (-16384, 0, 16)})
            for _ in range(12):
                run.step({zarya: (-16384, 0, 0)})
            bubble, = match.combat.barriers.values()
            self.assertIs(bubble.recipient, player)
            self.assertEqual(bubble.expires, deadline)
            self.assertEqual(bubble.vitals.total, 200)
        if nano:
            self.cast_enforcer_fixture_nano(run, player, run.players[-1])
            self.assertGreater(own.nano_until, deadline + 16)
        run.frame = deadline - baseline[0][0] - int(early)
        self.assertGreater(run.frame, match.tick)
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        start = run.frame
        hits = self.enforcer_return_after_source_removal(skipped=skipped, scene=scene)
        if projected:
            absorbed = sum(2 * dealt for frame, dealt in baseline if start + frame < deadline)
            self.assertGreater(absorbed, 0)
            self.assertTrue(bubble.retired)
            self.assertAlmostEqual(bubble.vitals.total, 200 - absorbed, places=4)
            self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), absorbed / 5, places=4)
            baseline = [(frame, dealt) for frame, dealt in baseline if start + frame >= deadline]
        self.assertEqual(len(hits), len(baseline))
        expected_armor = 0.0
        for (frame, dealt), (expected_frame, base) in zip(hits, baseline, strict=True):
            self.assertEqual(frame, expected_frame)
            if nano:
                base *= 0.5
            if start + frame < deadline:
                self.assertAlmostEqual(dealt, 2 * base, places=5)
            else:
                expected_armor += base
                self.assertAlmostEqual(dealt, base, places=5)
        self.assertAlmostEqual(own.total, 600 - expected_armor, places=5)
        self.assertEqual((own.temporary_shields, own.maximum, own.adaptive_until), (0, 600, 0))
        return hits

    def test_reflected_enforcer_volley_obeys_nano_expiry_with_or_without_shield(self):
        scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=10, support_hero=pve_tests.ANA)
        baseline = self.enforcer_return_after_source_removal(skipped=False, scene=scene)
        for early in (False, True):
            for shield in (False, True):
                with self.subTest(early=early, shield=shield):
                    stepped = self.enforcer_return_at_nano_boundary(baseline, early, shield, skipped=False)
                    skipped = self.enforcer_return_at_nano_boundary(baseline, early, shield, skipped=True)
                    self.assertEqual(skipped, stepped)

    def cast_enforcer_fixture_nano(self, run, player, ana):
        script = ana.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        x, y, z = player.mover.position
        angles = aim(run.match.combat._eye(ana), (x, y + 0.8, z))
        run.step({ana: (*angles, 32)})
        for _ in range(13):
            run.step({})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def enforcer_return_at_nano_boundary(self, baseline, early, shield, *, skipped):
        scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=10, support_hero=pve_tests.ANA)
        run, player, _, _, _, _, own = scene
        self.cast_enforcer_fixture_nano(run, player, run.players[3])
        deadline = own.nano_until
        self.assertGreater(deadline, run.frame)
        run.frame = deadline - baseline[0][0] - int(early)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        start = run.frame
        hits = self.enforcer_return_after_source_removal(skipped=skipped, scene=scene, shield=shield)
        self.assertEqual(len(hits), len(baseline))
        for (frame, dealt), (expected_frame, base) in zip(hits, baseline, strict=True):
            self.assertEqual(frame, expected_frame)
            if start + frame < deadline:
                base *= 0.5
            self.assertAlmostEqual(dealt, base * (2 if shield else 1), places=5)
        return hits

    def test_reflected_enforcer_volley_obeys_projected_barrier_expiry(self):
        scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=10, support_hero=barriers.ZARYA)
        baseline = self.enforcer_return_after_source_removal(skipped=False, scene=scene)
        for early in (False, True):
            for shield in (False, True):
                with self.subTest(early=early, shield=shield):
                    stepped = self.enforcer_at_projection_expiry(baseline, early, shield, skipped=False)
                    skipped = self.enforcer_at_projection_expiry(baseline, early, shield, skipped=True)
                    self.assertEqual(skipped, stepped)

    def enforcer_at_projection_expiry(self, baseline, early, shield, *, skipped):
        scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=10, support_hero=barriers.ZARYA)
        run, player, _, _, _, _, _ = scene
        match = run.match
        fight = match.combat
        zarya = run.players[3]
        run.step({zarya: (-16384, 0, 16)})
        for _ in range(12):
            run.step({zarya: (-16384, 0, 0)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, player)
        self.assertEqual(bubble.vitals.total, 200)
        energy = zarya.body_script.component.entity_var(87)
        self.assertEqual(expr.to_float(energy.value()), 0)
        deadline = bubble.expires
        run.frame = deadline - baseline[0][0] - int(early)
        self.assertGreater(run.frame, match.tick)
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        start = run.frame
        hits = self.enforcer_return_after_source_removal(skipped=skipped, scene=scene, shield=shield)
        self.assertTrue(bubble.retired)
        self.assertNotIn(bubble.entity, fight.barriers)
        body_hits = [(frame, dealt) for frame, dealt in baseline if start + frame >= deadline]
        self.assertEqual(len(hits), len(body_hits))
        for (frame, dealt), (expected_frame, base) in zip(hits, body_hits, strict=True):
            self.assertEqual(frame, expected_frame)
            self.assertAlmostEqual(dealt, base * (2 if shield else 1), places=5)
        absorbed = sum(2 * dealt for frame, dealt in baseline if start + frame < deadline)
        self.assertAlmostEqual(bubble.vitals.total, 200 - absorbed, places=4)
        charge = expr.to_float(energy.value())
        self.assertAlmostEqual(charge, absorbed / 5, places=4)
        return hits, bubble.vitals.total, charge

    def test_overlapping_opposing_deflects_retire_enforcer_pellets_at_the_reflection_limit(self):
        run, _, genji, opponent, bot, _, own, pellets = self.launch_opposing_deflects(
            distance=6, opposing_offset=3.5,
        )
        fight = run.match.combat
        for _ in range(25):
            run.step({opponent: (-32768, 0, 0)})
            capped = [r for r in pellets if r.reflections == 8 and r not in fight.rockets]
            if capped:
                break
        self.assertTrue(capped)
        self.assertTrue(all(rocket.left > 0 for rocket in capped))
        self.assertTrue(all(rocket.shooter is opponent and rocket.team == bot.team for rocket in capped))
        self.assertEqual(max(rocket.reflections for rocket in pellets), 8)
        self.assertEqual(own.total, 600)
        self.assertTrue(all(actor.body_script.component.entity_var(ULT_CHARGE).value() == 0
                            for actor in (genji, opponent)))

    def launch_opposing_deflects(self, *, distance, opposing_offset):
        scene = self.enforcer_return_defense_scene(deflection.GENJI, distance=distance)
        return self.fire_into_opposing_deflects(scene, opposing_offset=opposing_offset)

    def fire_into_opposing_deflects(self, scene, *, opposing_offset, shield=False):
        run, player, genji, opponent, bot, target, own = scene
        self.engage(run, player, target, shield=shield)
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 2))
        relocate(run, opponent, (x, y, z + opposing_offset))
        for frame in range(80):
            button = 16 if frame == 0 else 0
            run.step({genji: (0, 0, button), opponent: (-32768, 0, button)})
            if run.match.combat.rockets:
                break
        pellets = [rocket for rocket in run.match.combat.rockets if rocket.shooter is bot]
        self.assertEqual(len(pellets), 25)
        for actor in (genji, opponent):
            instances = actor.body_script.component.instances.values()
            self.assertTrue(next(i for i in instances if i.graph.index == 0x03A3).state(10).active)
        return run, player, genji, opponent, bot, target, own, pellets

    def test_actual_pin_continuous_phase_uses_elapsed_clock_and_reengages_after_stop(self):
        run, player, bot, target, own = self.arena()
        self.engage(run, player, target)
        graph = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        continuous = None
        for _ in range(150):
            run.step({})
            continuous = graph.states.get(graph.graph.node(15).state)
            if continuous is not None and continuous.active:
                break
        self.assertIsNotNone(continuous)
        self.assertTrue(continuous.active)
        before = own.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before - own.total, 16, places=3)
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.pins)
        before = own.total
        for _ in range(80):
            run.step({})
        self.assertEqual(own.total, before)
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        self.assertLess(own.total, before)

    def test_actual_pin_mounts_observable_victim_graph_then_retires_and_reuses_it(self):
        run, player, bot, target, own = self.arena()
        self.engage(run, player, target)
        self.wait_pin(run, player)
        script = player.body_script
        component = script.component
        victim, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
        self.assertEqual(victim.source_entity(), bot.entity)
        self.assertTrue(victim.states[victim.graph.node(2).state].active)
        self.assertEqual(observers.snapshot(script).instances[victim.id][0], assassin.VICTIM_GRAPH)
        for _ in range(40):
            run.step({})
        self.assertTrue(victim.states[victim.graph.node(3).state].active)
        self.assertIn(victim.id, observers.snapshot(script).instances)
        self.assertTrue(run.match.combat.is_stunned(player))

        self.assertEqual(npc_control.stop(run.match), 1)
        release = victim.states[victim.graph.node(4).state]
        self.assertTrue(release.active)
        self.assertFalse(victim.states[victim.graph.node(3).state].active)
        self.assertTrue(observers.snapshot(script).states[(victim.id, release.index)][1])
        self.assertFalse(run.match.combat.is_stunned(player))
        health = own.total
        for _ in range(62):
            run.step({})
            self.assertIn(victim.id, component.instances)
            self.assertTrue(release.active)
        self.assertEqual(own.total, health)
        run.step({})
        self.assertNotIn(victim.id, component.instances)
        self.assertNotIn(victim.id, observers.snapshot(script).instances)
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        fresh, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
        self.assertNotEqual(fresh.id, victim.id)
        self.assertEqual(fresh.source_entity(), bot.entity)
        self.assertTrue(fresh.states[fresh.graph.node(2).state].active)

    def test_actual_second_assassin_pin_replaces_victim_release_before_timeout(self):
        run, player, bot, target, _ = self.arena()
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        component = player.body_script.component
        old, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
        npc_control.stop(run.match)
        self.assertTrue(old.states[old.graph.node(4).state].active)
        x, y, z = player.mover.position
        bot.position = (x + 20, y, z)
        second = npc.spawn(run.match, self.definition('Assassin'), (x, y, z + 1.5), team=bot.team)
        target = next(t for t in run.match.combat._targets() if t.owner is second)
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        fresh, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
        self.assertNotEqual(fresh.id, old.id)
        self.assertEqual(fresh.source_entity(), second.entity)
        self.assertLess(component.now - old.states[old.graph.node(4).state].start, 1000)
        self.assertTrue(fresh.states[fresh.graph.node(2).state].active)

    def test_actual_overlapping_assassin_pins_keep_victim_hold_until_last_release(self):
        for release_first in (False, True):
            with self.subTest(release_first=release_first):
                run, player, first, target, own = self.arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                second = npc.spawn(match, self.definition('Assassin'), (x + 1.5, y, z), team=first.team)
                self.engage(run, player, target)
                self.wait_pin(run, player)
                script = player.body_script
                component = script.component
                pin_start = run.frame
                self.assertEqual(script.timed_control_commands[assassin.PIN_MESSAGE], ((pin_start, None),))
                victim, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
                self.assertEqual(victim.source_entity(), first.entity)
                target = next(t for t in match.combat._targets() if t.owner is second)
                self.engage(run, player, target, shield=False)
                for _ in range(85):
                    run.step({})
                self.assertEqual(match.combat.pins, {first.entity: player.body, second.entity: player.body})
                hold = victim.states[victim.graph.node(3).state]
                self.assertTrue(hold.active)
                hold_start = hold.start
                self.assertEqual(victim.source_entity(), first.entity)
                self.assertEqual([i.id for i in component.instances.values()
                                  if i.graph.index == assassin.VICTIM_GRAPH], [victim.id])
                self.assertGreater(own.temporary_shields, 48)
                before = own.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(before - own.total, 32, places=3)

                removed, remaining = (first, second) if release_first else (second, first)
                removed.team = player.team
                run.step({})
                self.assertEqual(match.combat.pins, {remaining.entity: player.body})
                self.assertTrue(match.combat.is_stunned(player))
                self.assertEqual(script.timed_control_commands[assassin.PIN_MESSAGE], ((pin_start, None),))
                self.assertTrue(hold.active)
                self.assertEqual(hold.counter, 1)
                self.assertEqual(hold.start, hold_start)
                self.assertEqual(victim.source_entity(), remaining.entity)
                self.assertEqual(victim.vars[0x1B].value(), expr.Entity(remaining.entity))
                self.assertIn(victim.id, observers.snapshot(player.body_script).instances)
                before = own.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(before - own.total, 16, places=3)

                npc_control.stop(match)
                self.assertFalse(match.combat.is_stunned(player))
                self.assertEqual(script.timed_control_commands[assassin.PIN_MESSAGE],
                                 ((pin_start, run.frame),))
                self.assertFalse(hold.active)
                self.assertTrue(victim.states[victim.graph.node(4).state].active)
                before = own.total
                for _ in range(63):
                    run.step({})
                self.assertNotIn(victim.id, component.instances)
                self.assertEqual(own.total, before)

    def test_actual_rifle_kill_of_first_assassin_preserves_second_pin(self):
        run, player, support, first, first_target, _, _ = self.support_scene(0x02E000000000006E)
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        second = npc.spawn(match, self.definition('Assassin'), (x - 1.5, y, z), team=first.team)
        self.engage(run, player, first_target)
        self.wait_pin(run, player)
        component = player.body_script.component
        victim, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
        second_target = next(t for t in fight._targets() if t.owner is second)
        self.engage(run, player, second_target, shield=False)
        for _ in range(30):
            run.step({})
            if second.entity in fight.pins:
                break
        self.assertEqual(fight.pins, {first.entity: player.body, second.entity: player.body})

        vitals = fight.vitals_of(first_target)
        vitals.now[combat.HEALTH_POOL] = 1
        fight._sync_body_health(first, vitals)
        relocate(run, support, (x + 2, y, z + 1.5))
        angles = aim(fight._eye(support), (x, y + 1, z + 1.5))
        for _ in range(100):
            run.step({support: (*angles, 1)})
            if first.dead:
                break
        self.assertTrue(first.dead)
        self.assertEqual(vitals.killer, support.body)
        self.assertEqual(fight.pins, {second.entity: player.body})
        self.assertTrue(fight.is_stunned(player))
        self.assertIn(victim.id, component.instances)
        self.assertEqual(victim.source_entity(), second.entity)
        self.assertFalse(victim.states[victim.graph.node(4).state].active)
        npc_control.stop(match)
        self.assertFalse(fight.is_stunned(player))
        self.assertTrue(victim.states[victim.graph.node(4).state].active)

    def test_pinned_commands_do_not_leak_after_release_and_fresh_inputs_work(self):
        run, player, bot, target, own = self.arena()
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        fight = run.match.combat
        before = fight.vitals_of(target).total
        position = tuple(player.mover.position)
        ammo = ball.instance(player, 0x1514).vars[53].value()
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            player.take_commands([Command(run.frame, buttons=1 | 2 | 8 | 16 | 32 | 64 | 128 | 1024 | 0x800,
                                          right=127, forward=127)])
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), ammo)
        self.assertFalse(player.body_script.component.held)
        self.assertFalse(fight.ball_grapples)
        self.assertEqual(fight.pins, {bot.entity: player.body})

        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertNotIn(bot.entity, fight.pins)
        for _ in range(40):
            run.step({})
        self.assertFalse(ball.rolling(player))
        self.assertFalse(fight.ball_grapples)
        self.assertFalse(player.body_script.component.held)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), ammo)
        self.assertEqual(fight.vitals_of(target).total, before)

        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 0.5, target.feet[2]))
        run.step({player: (*angles, 16 | 1)})
        self.assertEqual(own.temporary_shields, 175)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), ammo - 1)
        self.assertEqual(fight.vitals_of(target).total, before - 5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)

    def test_assassin_pin_release_does_not_reset_adaptive_shield_cooldown(self):
        run, player, _, target, own = self.arena()
        self.engage(run, player, target)
        shield = ball.instance(player, 0x1566)
        cooldown = shield.state(0)
        active = shield.state(6)
        self.assertEqual(active.counter, 1)
        self.wait_pin(run, player)
        self.assertGreater(own.temporary_shields, 0)
        self.assertLess(own.temporary_shields, 175)
        damaged = own.temporary_shields
        before_release = cooldown.remaining()
        self.assertGreater(before_release, 0)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({player: (0, 0, 16)})
        self.assertEqual(active.counter, 1)
        self.assertEqual(own.temporary_shields, damaged)
        self.assertGreater(cooldown.remaining(), 0)
        self.assertLess(cooldown.remaining(), before_release)
        for _ in range(int(before_release / combat.TICK) + 5):
            run.step({})
        self.assertEqual(cooldown.remaining(), 0)
        self.assertEqual(active.counter, 1)
        self.assertEqual(own.temporary_shields, 0)
        run.step({player: (0, 0, 16)})
        self.assertEqual(active.counter, 2)
        self.assertEqual(own.temporary_shields, 175)

    def test_assassin_pin_release_does_not_bypass_cannon_reload(self):
        for automatic in (False, True):
            for after_fill in (False, True):
                with self.subTest(automatic=automatic, after_fill=after_fill):
                    self.check_pin_during_reload(automatic, after_fill)

    def check_pin_during_reload(self, automatic, after_fill):
        run, player, bot, target, _ = self.arena()
        weapon = ball.instance(player, 0x1514)
        ammo = weapon.vars[53]
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(240 if automatic else 30):
            run.step({player: (-32768, 0, 1)})
            if automatic and ammo.value() == 0:
                break
        if not automatic:
            run.step({})
            run.step({player: (0, 0, 1024)})
        if after_fill:
            for _ in range(100):
                if ammo.value() == 80:
                    break
                run.step({})
        phase = weapon.state(20 if after_fill else 21)
        for _ in range(30):
            if phase.active:
                break
            run.step({})
        self.assertTrue(phase.active)
        reload_ammo = ammo.value()
        if after_fill:
            self.assertEqual(reload_ammo, 80)
        elif automatic:
            self.assertEqual(reload_ammo, 0)
        else:
            self.assertGreater(reload_ammo, 0)
            self.assertLess(reload_ammo, 80)
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(phase.active, after_fill)
        self.assertEqual(ammo.value(), reload_ammo)
        activation = phase.counter
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 0.5, target.feet[2]))
        run.step({player: (*angles, 1)})
        self.assertEqual(ammo.value(), reload_ammo)
        self.assertFalse(weapon.state(9).active)
        self.assertEqual(vitals.total, before)

        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(fight.is_stunned(player))
        for _ in range(150):
            run.step({player: (*angles, 1)})
            if vitals.total < before:
                break
            self.assertFalse(weapon.state(9).active)
        self.assertFalse(weapon.state(20).active)
        self.assertFalse(weapon.state(21).active)
        self.assertEqual(phase.counter, activation + int(automatic and not after_fill))
        self.assertEqual(ammo.value(), 79 if automatic or after_fill else reload_ammo - 1)
        self.assertEqual(vitals.total, before - 5)

    def test_range_wall_and_team_loss_end_real_pin_without_late_damage(self):
        for reason in ('range', 'wall', 'team'):
            with self.subTest(reason=reason):
                run, player, bot, target, own = self.arena()
                self.engage(run, player, target)
                self.wait_pin(run, player)
                if reason == 'range':
                    x, y, z = bot.position
                    bot.position = (x, y, z + 10)
                elif reason == 'team':
                    run.match.game_map = run.match.game_map.__class__(
                        **{**run.match.game_map.__dict__, 'free_for_all': False})
                    bot.team = player.team
                else:
                    x, y, z = player.mover.position
                    wall = z + 0.75
                    run.match.collision_world = World([
                        (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
                        (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
                        (x - 5, y, wall, x + 5, y, wall, x + 5, y + 5, wall),
                        (x - 5, y, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                    ])
                before = own.total
                for _ in range(80):
                    run.step({})
                self.assertEqual(own.total, before)
                self.assertFalse(run.match.combat.pins)

    def test_removal_and_victim_body_replacement_retire_actual_pin(self):
        for action in ('replace', 'remove', 'replace_during_release'):
            with self.subTest(action=action):
                run, player, bot, target, own = self.arena()
                self.engage(run, player, target)
                self.wait_pin(run, player)
                old_body = player.body
                component = player.body_script.component
                victim, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
                if action == 'remove':
                    npc.remove_spawned(run.match)
                else:
                    if action == 'replace_during_release':
                        npc_control.stop(run.match)
                        self.assertTrue(victim.states[victim.graph.node(4).state].active)
                    run.match.switch_hero(player, heroes()[0x02E00000000001CA])
                self.assertNotIn(old_body, run.match.combat.pins.values())
                self.assertNotIn(victim.id, component.instances)
                before = own.total
                for _ in range(30):
                    run.step({})
                self.assertEqual(own.total, before)
                if action == 'remove':
                    self.assertNotIn(bot, run.match.bots)

    def test_actual_support_rifle_kills_pin_source_and_releases_ball(self):
        run, player, support, _, _ = grapple_tests.scene(distance=None)
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for actor in run.players:
            actor.client = pve_tests.PveClient(actor)
        x, y, z = player.mover.position
        support.mover.position[:] = (x + 2, y, z)
        bot = npc.spawn(run.match, self.definition('Assassin'), (x, y, z + 1.5),
                        team=1 - player.team)
        target = next(t for t in fight._targets() if t.owner is bot)
        self.engage(run, player, target)
        self.wait_pin(run, player)
        component = player.body_script.component
        victim, = (i for i in component.instances.values() if i.graph.index == assassin.VICTIM_GRAPH)
        angles = aim(fight._eye(support), (x, y + 0.5, z + 1.5))
        for _ in range(200):
            run.step({support: (*angles, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(victim.id, component.instances)
        self.assertIn(bot, run.match.bots)
        before = fight.vitals.get(player.body).total
        for _ in range(50):
            run.step({})
        self.assertEqual(fight.vitals[player.body].total, before)

    def allied_pin_rescue_scene(self, *, fireball=False):
        run, player, support, _, _ = grapple_tests.scene(distance=None)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        support.team = player.team
        self.assertTrue(run.match.switch_hero(support, heroes()[ball.HERO]))
        for actor in run.players:
            actor.client = pve_tests.PveClient(actor)
        x, y, z = player.mover.position
        if fireball:
            relocate(run, support, (x, y, z - 4.5))
            prepare_swing(run, support)
        else:
            relocate(run, support, (x + 1.5, y, z + 1.5))
            for _ in range(40):
                run.step({})
        support.body_script.ult_seconds = 0
        support.body_script.set_ult_charge(0)
        fight = run.match.combat
        bot = npc.spawn(run.match, self.definition('Assassin'), (x, y, z + 1.5),
                        team=1 - player.team)
        target = next(t for t in fight._targets() if t.owner is bot)
        self.engage(run, player, target)
        self.wait_pin(run, player)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        return run, player, support, bot, target

    def test_actual_allied_piledriver_lifts_assassin_and_releases_pin(self):
        run, player, support, bot, target = self.allied_pin_rescue_scene()
        fight = run.match.combat
        before = fight.vitals_of(target).total
        lift(run, support)
        cast(run, support, 1)
        landing = pile_instance(support).state(7)
        for _ in range(100):
            run.step({})
            if landing.counter:
                break
        self.assertEqual(landing.counter, 1)
        self.assertEqual(fight.vitals_of(target).total, before - 100)
        self.assertGreater(bot.velocity[1], 10)
        self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 100)
        for _ in range(40):
            run.step({})
            if not fight.is_stunned(player):
                break
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.pins)
        health = fight.vitals[player.body].total
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[player.body].total, health)
        self.assertEqual(bot.attack_target, player.entity)
        self.assertTrue(bot.firing)
        for _ in range(150):
            run.step({})
            if not bot.airborne:
                break
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(fight.vitals[player.body].total, health)
        self.assertFalse(bot.airborne)
        self.wait_pin(run, player)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertLess(fight.vitals[player.body].total, health)

    def test_static_wall_blocks_melee_rescue_without_breaking_pin(self):
        run, player, support, bot, target = self.allied_pin_rescue_scene()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        x, y, z = target.feet
        relocate(run, support, (x, y, z + 1.5))
        wall = z + 0.75
        ground = run.match.collision_world
        run.match.collision_world = World([
            (x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
            (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30),
            (x - 3, y - 1, wall, x + 3, y - 1, wall, x + 3, y + 4, wall),
            (x - 3, y - 1, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
        ])
        angles = aim(fight._eye(support), (x, y + 0.7, z))
        run.step({support: (*angles, 0x800)})
        for _ in range(75):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        run.match.collision_world = ground
        run.step({support: (*angles, 0x800)})
        for _ in range(40):
            if vitals.total < before:
                break
            run.step({})
        self.assertEqual(vitals.total, before - 30)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))

    def test_actual_quick_melee_releases_pin_after_cannon_leaves_it_active(self):
        run, player, support, bot, target = self.allied_pin_rescue_scene()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        angles = aim(fight._eye(support), (target.feet[0], target.feet[1] + 0.7, target.feet[2]))
        run.step({support: (*angles, 1)})
        self.assertEqual(vitals.total, before - 5)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        run.step({})
        run.step({support: (*angles, 0x800)})
        self.assertEqual(vitals.total, before - 35)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 35)
        health = fight.vitals[player.body].total
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[player.body].total, health)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        self.engage(run, player, target, shield=False)
        self.wait_pin(run, player)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertLess(fight.vitals[player.body].total, health)

    def test_melee_rescue_rearms_only_when_target_is_eligible_after_release(self):
        for obstacle in (None, 'range', 'wall', 'team'):
            with self.subTest(obstacle=obstacle):
                run, player, support, bot, target = self.allied_pin_rescue_scene()
                fight = run.match.combat
                angles = aim(fight._eye(support), (target.feet[0], target.feet[1] + 0.7, target.feet[2]))
                run.step({support: (*angles, 0x800)})
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == 0x1157)
                release = instance.state(23)  # Anim at node 31
                self.assertTrue(release.active)
                self.assertFalse(fight.is_stunned(player))
                health = fight.vitals[player.body].total

                x, y, z = player.mover.position
                team = player.team
                ground = run.match.collision_world
                if obstacle == 'range':
                    relocate(run, player, (x + 10, y, z))
                elif obstacle == 'wall':
                    wall = (z + bot.position[2]) / 2
                    run.match.collision_world = World([
                        (x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
                        (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30),
                        (x - 3, y - 1, wall, x + 3, y - 1, wall, x + 3, y + 4, wall),
                        (x - 3, y - 1, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
                    ])
                elif obstacle == 'team':
                    player.team = bot.team

                for _ in range(100):
                    run.step({})
                    if not release.active:
                        break
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(fight.vitals[player.body].total, health)
                self.assertFalse(release.active)
                self.assertEqual(release.ended_at - release.start, 1467)

                if obstacle is not None:
                    for _ in range(20):
                        run.step({})
                        self.assertFalse(bot.attack_input)
                        self.assertNotIn(bot.entity, fight.pins)
                        self.assertEqual(fight.vitals[player.body].total, health)
                    run.match.collision_world = ground
                    player.team = team
                    relocate(run, player, (x, y, z))

                self.wait_pin(run, player)
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertLess(fight.vitals[player.body].total, health)

    def test_actual_allied_fireball_pushes_assassin_and_releases_pin(self):
        run, player, support, bot, target = self.allied_pin_rescue_scene(fireball=True)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(100):
            grapple_tests.hold(run, support, 1)
            if vitals.total < before:
                break
        self.assertTrue(ball.instance(support, 0x1203).state(35).active)
        self.assertEqual(vitals.total, before - 50)
        self.assertGreater(bot.velocity[2], 20)
        self.assertEqual(support.body_script.component.entity_var(ULT_CHARGE).value(), 50)

        for _ in range(40):
            run.step({})
            if not fight.is_stunned(player):
                break
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.pins)
        health = fight.vitals[player.body].total
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[player.body].total, health)

    def test_actual_pin_interrupts_attached_grapple_and_clears_contact_channel(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                self.check_attached_grapple_interrupted_by_pin(frozen)

    def check_attached_grapple_interrupted_by_pin(self, frozen):
        run, player, _, _, _ = grapple_tests.scene()
        fight = run.match.combat
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, self.definition('Assassin'), (x, y, z + 1.5),
                        team=1 - player.team)
        for actor in run.players:
            actor.client = pve_tests.PveClient(actor)
        grapple_tests.hold(run, player, 5)
        self.assertTrue(fight.ball_grapples[player.body].attached)
        target = next(t for t in fight._targets() if t.owner is bot)
        angles = aim(fight._eye(player),
                     (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        run.step({player: (*angles, 2)})
        npc_control.start(run.match, player)
        script = player.body_script
        if frozen:
            before = script.frame
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertEqual(script.frame, before)
            self.assertEqual(script.grapple_interrupt_frame, run.frame)
        else:
            run.step({player: (*angles, 2)})
        self.assertTrue(fight.is_stunned(player))
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertNotIn(player.body, fight.ball_contacts)
        if frozen:
            player.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=2)])
            self.assertFalse(hasattr(script, 'grapple_interrupt_frame'))
        self.assertFalse(script.component.held)
        cooldown = ball.instance(player, ball.GRAPPLE).state(25)
        self.assertEqual(cooldown.last // script.frame_ms, run.frame)
        self.assertAlmostEqual(cooldown.remaining(), 5, delta=0.02)

    def test_delayed_preimpact_grapple_respects_assassin_pin(self):
        cases = ((2, True, True), (11, True, False), (2, False, False), (11, False, True))
        for lead, live, replay_impact in cases:
            with self.subTest(lead=lead, live=live, replay_impact=replay_impact):
                self.check_delayed_grapple_through_assassin_pin(lead, live, replay_impact)

    def check_delayed_grapple_through_assassin_pin(self, lead, live, replay_impact):
        run, player, _, _, _ = grapple_tests.scene(distance=22)
        match = run.match
        fight = match.combat
        for actor in run.players:
            actor.client = pve_tests.PveClient(actor)
        x, y, z = player.mover.position
        bot = npc.spawn(match, self.definition('Assassin'), (x + 1.5, y, z), team=1 - player.team)
        target = next(t for t in fight._targets() if t.owner is bot)
        angles = aim(fight._eye(player), (x + 1.5, y + target.height / 2, z))
        run.step({player: (*angles, 0)})
        script = player.body_script
        frozen = script.frame
        run.frame += 15
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for _ in range(25):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            if fight.is_stunned(player):
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        impact = run.frame
        self.assertGreater(impact - lead, frozen)
        run.frame += 15
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        if not live:
            self.assertEqual(npc_control.stop(match), 1)
        self.assertEqual(fight.is_stunned(player), live)
        self.assertEqual(script.frame, frozen)

        player.take_commands([Command(impact - lead, buttons=2), Command(impact - 1, buttons=2)])
        grapple = ball.instance(player, ball.GRAPPLE)
        cooldown = grapple.state(25)
        self.assertEqual(grapple.state(0).counter, 1)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertFalse(grapple.vars[435].value())
        if replay_impact:
            player.take_commands([Command(impact, buttons=2)])
            self.assertAlmostEqual(cooldown.remaining(), 5, delta=0.02)
        player.take_commands([Command(run.frame, buttons=2)])
        self.assertEqual(cooldown.last // script.frame_ms, impact)
        self.assertNotIn(player.body, fight.ball_grapples)
        if live:
            self.assertEqual(npc_control.stop(match), 1)
        self.assertFalse(fight.is_stunned(player))
        for _ in range(320):
            if cooldown.remaining() == 0:
                break
            grapple_tests.hold(run, player, 1)
        self.assertEqual(cooldown.remaining(), 0)
        grapple_tests.hold(run, player, 1)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertEqual(grapple.state(0).counter, 1)
        run.step({})
        grapple_tests.hold(run, player, 1)
        retry = fight.ball_grapples[player.body]
        grapple_tests.hold(run, player, retry.arrives - run.frame)
        self.assertTrue(retry.attached)

    def test_delayed_piledriver_cancels_at_assassin_pin(self):
        cases = ((4, 18, False, False), (4, 18, True, True), (52, 3, False, True), (52, 3, True, False))
        for lead, phase, live, replay_impact in cases:
            with self.subTest(phase=phase, live=live, replay_impact=replay_impact):
                self.check_delayed_piledriver_through_assassin_pin(lead, phase, live, replay_impact)

    def check_delayed_piledriver_through_assassin_pin(self, lead, phase, live, replay_impact):
        run, player, bot, target, _ = self.arena()
        match = run.match
        fight = match.combat
        health = fight.vitals_of(target)
        before = health.total
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(ULT_CHARGE)
        x, floor, z = player.mover.position
        lift(run, player, 20)
        y = player.mover.position[1]
        bot.position = (x + 1.5, y, z)
        angles = aim(fight._eye(player), (x + 1.5, y + target.height / 2, z))
        run.step({player: (*angles, 0)})
        frozen = script.frame
        run.frame += 60
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for _ in range(25):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            if fight.is_stunned(player):
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        impact = run.frame
        self.assertGreater(impact - lead, frozen)
        run.frame += 8
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        if not live:
            self.assertEqual(npc_control.stop(match), 1)
        self.assertEqual(fight.is_stunned(player), live)
        self.assertEqual(script.frame, frozen)
        player.take_commands([Command(impact - lead, buttons=128), Command(impact - 1)])
        pile = pile_instance(player)
        self.assertTrue(pile.state(phase).active)
        self.assertEqual(pile.state(7).counter, 0)
        if replay_impact:
            player.take_commands([Command(impact)])
            self.assertFalse(pile.state(phase).active)
        player.take_commands([Command(run.frame)])
        self.assertFalse(pile.state(phase).active)
        self.assertGreater(pile.state(24).remaining(), 0)
        if live:
            self.assertEqual(npc_control.stop(match), 1)
        bot.position = (x + 1, floor, z)
        for _ in range(150):
            run.step({})
            if not player.mover.airborne:
                break
        self.assertFalse(player.mover.airborne)
        self.assertEqual(pile.state(7).counter, 0)
        self.assertEqual(health.total, before)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))
        lift(run, player)
        cast(run, player, 1)
        self.assertFalse(pile.state(18).active)
        self.assertGreater(pile.state(24).remaining(), 0)
        run.frame = impact + 650
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(run.frame)])
        self.assertEqual(pile.state(24).remaining(), 0)
        lift(run, player)
        cast(run, player)
        self.assertEqual(pile.state(7).counter, 1)
        self.assertEqual(health.total, before - 100)
        self.assertEqual(charge.value(), 100)

    def test_all_five_manual_attacks_obey_a_real_wall_then_recover(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault'):
            with self.subTest(npc=name):
                run, player, _, target, own = self.arena(name, distance=1.5 if name == 'Assassin' else 6)
                self.engage(run, player, target)
                original = run.match.collision_world
                x, y, z = player.mover.position
                wall = z + 0.75
                run.match.collision_world = World([
                    (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
                    (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
                    (x - 5, y, wall, x + 5, y, wall, x + 5, y + 5, wall),
                    (x - 5, y, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                ])
                before = own.total
                for _ in range(120):
                    run.step({})
                self.assertEqual(own.total, before)
                run.match.collision_world = original
                for _ in range(200):
                    run.step({})
                    if own.total < before:
                        break
                self.assertLess(own.total, before)

    def support_scene(self, hero, name='Assassin', distance=1.5):
        run, player, support, _, _ = grapple_tests.scene(distance=None)
        match = run.match
        fight = match.combat
        match.game_map = match.game_map.__class__(**{**match.game_map.__dict__, 'free_for_all': False})
        match.bots.clear()
        match._pending_bot_scripts.clear()
        x, y, z = player.mover.position
        match.switch_hero(support, heroes()[hero])
        support.team = player.team
        support.mover.position[:] = (x + 2, y, z)
        for actor in run.players:
            actor.client = pve_tests.PveClient(actor)
        for _ in range(40):
            run.step({})
        definition = self.definition(name)
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1 - player.team)
        target = next(t for t in fight._targets() if t.owner is bot)
        own = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(own)
        source_total = definition.health + definition.armour + definition.shields
        self.assertEqual(fight.vitals_of(target).total, source_total)
        return run, player, support, bot, target, health, source_total

    def support_pin_scene(self, hero):
        scene = self.support_scene(hero)
        run, player, _, _, target, _, _ = scene
        self.engage(run, player, target)
        self.wait_pin(run, player)
        return scene

    def test_actual_support_flashbang_interrupts_source_and_releases_ball(self):
        run, player, support, bot, target, health, source_total = self.support_pin_scene(0x02E0000000000042)
        fight = run.match.combat
        flash_tests.FlashbangTests().cast(run, support, bot)
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(fight.pins)
        self.assertEqual(fight.vitals_of(target).total, source_total - 25)
        before = health.total
        deadline = fight.stuns[bot.entity]
        while run.frame < deadline - 1:
            run.step({})
        self.assertEqual(health.total, before)

    def test_actual_support_sleep_releases_ball_and_pauses_assassin_damage(self):
        run, player, support, bot, target, health, source_total = self.support_pin_scene(0x02E000000000013B)
        fight = run.match.combat
        start, deadline = self.sleep_npc(run, support, bot)
        self.assertEqual(deadline - start, 313)
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(fight.pins)
        self.assertEqual(fight.vitals_of(target).total, source_total - 5)
        before = health.total
        while run.frame < deadline - 1:
            run.step({})
        self.assertEqual(health.total, before)
        self.assertTrue(fight.is_stunned(bot))
        run.frame = deadline
        run.match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertFalse(fight.is_stunned(bot))
        self.assertNotIn(bot.entity, fight.sleeps)
        self.wait_pin(run, player)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertLess(health.total, before)

    def sleep_npc(self, run, support, bot):
        fight = run.match.combat
        x, y, z = bot.position
        angles = aim(fight._eye(support), (x, y + 1, z))
        for frame in range(65):
            run.step({support: (*angles, 8 if frame == 0 else 0)})
            if bot.entity in fight.sleeps:
                return fight.sleeps[bot.entity]
        self.fail('actual Ana dart did not sleep the NPC')

    def test_actual_support_sleep_pauses_each_ranged_npc_and_weapon_recovers(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault'):
            with self.subTest(npc=name):
                run, player, support, bot, target, health, source_total = self.support_scene(
                    0x02E000000000013B, name, distance=6,
                )
                fight = run.match.combat
                self.engage(run, player, target)
                before = health.total
                for _ in range(200):
                    run.step({})
                    if health.total < before:
                        break
                self.assertLess(health.total, before)
                start, deadline = self.sleep_npc(run, support, bot)
                self.assertEqual(deadline - start, 313)
                definition = self.definition(name)
                damage = 2.5 if definition.armour and not definition.shields else 5
                self.assertEqual(fight.vitals_of(target).total, source_total - damage)
                # In-flight rounds may finish independently of the sleeping source.
                launched = tuple(rocket for rocket in fight.rockets if rocket.shooter is bot)
                launched_ids = {id(rocket) for rocket in launched}
                for _ in range(24):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    remaining = {id(rocket) for rocket in fight.rockets if rocket.shooter is bot}
                    self.assertLessEqual(remaining, launched_ids)
                before = health.total
                while run.frame < deadline - 1:
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertTrue(fight.is_stunned(bot))
                    remaining = {id(rocket) for rocket in fight.rockets if rocket.shooter is bot}
                    self.assertLessEqual(remaining, launched_ids)
                self.assertEqual(health.total, before)
                for _ in range(200):
                    run.step({})
                    if health.total < before:
                        break
                self.assertNotIn(bot.entity, fight.sleeps)
                self.assertFalse(fight.is_stunned(bot))
                self.assertLess(health.total, before)

    def test_actual_sleep_pauses_npc_route_and_route_resumes_after_expiry(self):
        run, _, support, bot, _, _, _ = self.support_scene(0x02E000000000013B, 'Trooper', distance=6)
        fight = run.match.combat
        _, deadline = self.sleep_npc(run, support, bot)
        start = bot.position
        x, y, z = start
        bot.route = (start, (x + 4, y, z))
        bot.index = 0
        bot.wait = 0
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(bot.position, start)
        self.assertTrue(fight.is_stunned(bot))
        for _ in range(45):
            run.step({})
        self.assertFalse(fight.is_stunned(bot))
        self.assertGreater(bot.position[0], x)
