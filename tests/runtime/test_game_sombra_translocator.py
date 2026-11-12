"""Actual Translocator graph -> server entity and reconstructed placement/recall."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import test_game_sombra_combat as fixture
import test_game_sombra_control as control
import test_game_sombra_npc as actual_npcs
import test_server_snapshot as stored

from ow174.game import (
    combat,
    npc,
    npc_control,
    pharah,
    reinhardt_barrier,
    shot_guards,
    stats,
    translocators,
    world,
)
from ow174.game.commands import FIRE, Command
from ow174.game.script import expr
from ow174.game.script.driver import OWNER_HEALTH
from ow174.game.script.runtime import send_instance_message


class TranslocatorTests(unittest.TestCase):
    def setUp(self):
        path = os.environ.get("ORPHEUS_TRANSLOCATOR_DATA")
        if not path:
            self.skipTest("set ORPHEUS_TRANSLOCATOR_DATA to the external additive0C1A record")
        patch = mock.patch.object(translocators, "DATA_PATH", Path(path))
        patch.start()
        self.addCleanup(patch.stop)

    def make(self):
        f = fixture.SombraCombatTests()
        f.setUp()
        return f

    def cast(self, f):
        f.press(16, 20)
        self.assertIsNotNone(f.a.body_script)
        actors = list(f.run.match.combat.translocators.entries.values())
        self.assertEqual(len(actors), 1)
        return actors[0]

    def test_actual_volley_binds_server_identity_and_original_health_pool_without_lease(self):
        f = self.make()
        actor = self.cast(f)
        manager = f.run.match.combat
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0C19)
        self.assertEqual(root.vars[5875].value(), expr.Entity(actor.entity))
        self.assertIsNone(manager.projectiles.lease_id(f.a, translocators.DEFINITION, f.run.frame))
        self.assertFalse(manager.projectiles.shots)
        self.assertFalse(manager.rockets)
        self.assertEqual(manager.vitals[actor.entity].total, 5)
        self.assertEqual(actor.body_script.component.entity_var(OWNER_HEALTH).value(), 5)
        self.assertTrue(actor.body_script.component.instances[1].node_state(
            actor.body_script.component.instances[1].graph._by_pos[23]).active)
        self.assertEqual(actor.body_script.component.instances[1].source_entity(), f.a.body)
        self.assertEqual(actor.gravity, 20)
        self.assertGreater(actor.until_tick - f.run.frame, math.ceil(998 / combat.TICK))
        self.assertLessEqual(actor.until_tick - f.run.frame, math.ceil(999 / combat.TICK))
        self.assertTrue(any(u.entity == actor.entity and u.op == world.OP_CREATE
                            for u in f.a.client.entities))
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(f.b), 200)

    def test_reconstructed_arc_lands_without_damage_or_endless_energy_gain(self):
        f = self.make()
        actor = self.cast(f)
        self.assertGreater(actor.position[2], 3)
        self.assertLess(actor.position[1], 1.5)
        f.press(0, 300)
        self.assertTrue(actor.settled)
        self.assertAlmostEqual(actor.position[1], translocators.RADIUS, places=3)
        self.assertEqual(actor.velocity, (0, 0, 0))
        where = actor.position
        f.press(0, 30)
        self.assertEqual(actor.position, where)
        self.assertEqual(f.health(f.b), 200)

    def test_gap_and_continuous_entity_motion_agree(self):
        values = []
        for gap in (False, True):
            f = self.make()
            actor = self.cast(f)
            if gap:
                f.run.match.combat.translocators.update(f.run.frame + 30)
            else:
                for tick in range(f.run.frame + 1, f.run.frame + 31):
                    f.run.match.combat.translocators.update(tick)
            values.append((actor.position, actor.velocity, actor.distance))
        self.assertEqual(values[0], values[1])

    def test_second_press_consumes_beacon_and_relocates_then_lands_at_destination(self):
        f = self.make()
        actor = self.cast(f)
        f.press(0, 150)
        destination = actor.position
        # Teleport crosses a newly introduced wall; placement checks the target capsule.
        f.wall()
        f.press(16)
        f.press(0, 60)
        self.assertFalse(f.run.match.combat.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], destination[2], places=2)
        self.assertAlmostEqual(f.a.mover.position[0], destination[0], places=2)
        self.assertLess(abs(f.a.mover.position[1]), 0.01)
        self.assertEqual(f.a.mover.velocity, (0, 0, 0))
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(f.b), 200)

    def test_camo_replay_preserves_an_already_accepted_throw(self):
        samples = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                f = self.make()
                fight = f.run.match.combat
                f.press(8)
                f.press(0, 60)
                camo = f.camo().state(13)
                f.press(FIRE, shooter=f.b, target=f.a)
                f.press(0, 65)
                self.assertEqual(f.health(f.a), 180)
                self.assertGreater(camo.remaining(), 0)
                f.run.step({f.a: (16384, 0, 16)})
                self.assertFalse(fight.translocators.entries)
                if gap:
                    f.run.frame += 30
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                else:
                    for _ in range(30):
                        f.run.step({f.a: (16384, 0, 0)})
                actor, = fight.translocators.entries.values()
                self.assertGreater(actor.position[0], 5)
                self.assertEqual(actor.last_tick, f.run.frame)
                samples.append((actor.position, actor.velocity, actor.distance, actor.until_tick))
        self.assertEqual(samples[0], samples[1])

    @staticmethod
    def advance_to(f, tick, gap, *, buttons=0):
        if gap:
            f.run.frame = tick
            f.run.match.update(tick * combat.TICK, tick, frames=False)
            if buttons:
                f.a.take_commands([Command(tick, buttons=buttons)])
        else:
            while f.run.frame < tick:
                f.run.step({f.a: (0, 0, buttons if f.run.frame + 1 == tick else 0)})

    def test_world_only_throw_releases_on_time_and_unlocks_recall(self):
        samples = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                f = self.make()
                fight = f.run.match.combat
                f.run.step({f.a: (0, 0, 16)})
                start = f.run.frame
                self.advance_to(f, start + 5, gap)
                self.assertFalse(fight.translocators.entries)
                self.advance_to(f, start + 8, gap)
                actor, = fight.translocators.entries.values()
                self.assertEqual(actor.until_tick, start + 6 + math.ceil(999 / combat.TICK))
                flight = (actor.position, actor.velocity, actor.distance, actor.last_tick)
                self.advance_to(f, start + 12, gap, buttons=16)
                self.assertEqual(f.a.mover.position, [0, 0, 0])
                self.assertIs(fight.translocators.entries[actor.entity], actor)
                self.advance_to(f, start + 160, gap, buttons=16)
                self.assertTrue(actor.settled)
                self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)
                self.advance_to(f, start + 225, gap)
                self.assertFalse(fight.translocators.entries)
                self.assertNotIn(f.a.body, fight.translocators.recall_poses)
                self.assertEqual(f.health(f.a), 200)
                samples.append((flight, tuple(f.a.mover.position), actor.position))
        self.assertEqual(samples[0], samples[1])

    def test_world_only_airborne_recall_matches_descent_and_landing(self):
        runs = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                f = self.make()
                fight = f.run.match.combat
                f.b.mover.position[:] = (0, 20, 5)
                f.b.mover.body = None
                actor = self.cast(f)
                f.press(0, 60)
                self.assertFalse(actor.settled)
                f.press(16)
                start = f.run.frame
                self.assertGreater(f.a.mover.position[1], 10)
                samples = []
                for offset in (10, 45, 100, 180):
                    # The endpoint remains open, so its owner pose is from the previous frame.
                    self.advance_to(f, start + offset + int(gap), gap)
                    self.assertFalse(fight.translocators.entries)
                    samples.append((tuple(f.a.mover.position), f.a.mover.velocity))
                self.assertGreater(samples[1][0][1], 0)
                self.assertLess(samples[1][1][1], 0)
                self.assertEqual(samples[-1][0][1], 0)
                self.assertNotIn(f.a.body, fight.translocators.recall_poses)
                runs.append(samples)
        self.assertEqual(runs[0], runs[1])

    def test_airborne_recall_uses_original_selection_and_movement_output(self):
        f = self.make()
        f.b.mover.position[:] = (0, 20, 5)
        f.b.mover.body = None
        actor = self.cast(f)
        f.press(0, 60)  # original throw admission must finish before a fresh recall edge
        self.assertFalse(actor.settled)
        f.press(16)
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0C19)
        move = root.node_state(root.graph._by_pos[131])
        self.assertTrue(move.accepted)
        self.assertGreater(move.destination.y, 0.2)
        self.assertAlmostEqual(f.a.mover.position[2], move.destination.z, places=2)
        f.press(0, 40)  # original movement-duration state completes before beacon destruction
        self.assertFalse(f.run.match.combat.translocators.entries)

    def test_recall_edge_during_original_throw_admission_is_not_buffered(self):
        f = self.make()
        actor = self.cast(f)
        f.press(0)
        f.press(16)
        f.press(0, 60)
        self.assertIn(actor.entity, f.run.match.combat.translocators.entries)
        self.assertLess(abs(f.a.mover.position[2]), 0.01)
        f.press(16)
        f.press(0, 40)
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)

    def test_obstructed_destination_refuses_displacement_without_fallback_to_origin(self):
        f = self.make()
        actor = self.cast(f)
        f.press(0, 150)
        z = actor.position[2]
        # A plane through the destination intersects the actual standing capsule.
        f.geometry((-3, 0, z, 3, 0, z, 3, 4, z), (-3, 0, z, 3, 4, z, -3, 4, z))
        f.a.mover.position[:] = (3, 0, 0)
        f.a.mover.body = None
        f.press(0, 2)
        f.press(16)
        f.press(0, 35)
        self.assertAlmostEqual(f.a.mover.position[0], 3, places=2)
        self.assertLess(abs(f.a.mover.position[2]), 0.01)

    def test_manual_recycle_routes_original_message_and_enforces_original_cooldown(self):
        f = self.make()
        actor = self.cast(f)
        f.press(0, 80)
        f.press(4, 20)
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0C19)
        self.assertGreater(root.state(2).remaining(), 5)
        f.press(0)
        f.press(16, 20)
        self.assertFalse(f.run.match.combat.translocators.entries)
        f.press(0, math.ceil(root.state(2).remaining() / combat.TICK) + 3)
        self.assertNotEqual(self.cast(f).entity, actor.entity)

    def test_authored_manual_recycle_starts_cooldown_on_the_device_clock(self):
        for camo_cooldown in (False, True):
            timings = []
            for mode in ("contiguous", "single gap", "split gap"):
                gap = mode != "contiguous"
                with self.subTest(mode=mode, camo_cooldown=camo_cooldown):
                    f = self.make()
                    fight = f.run.match.combat
                    actor = self.cast(f)
                    f.press(0, 410)
                    ability = actor.cast.state(2)
                    self.assertEqual(ability.remaining(), 0)
                    if camo_cooldown:
                        f.press(8)
                        f.press(0, 60)
                        f.press(FIRE, shooter=f.b, target=f.a)
                        f.press(0, 65)
                        self.assertEqual(f.health(f.a), 180)
                        self.assertGreater(f.camo().state(13).remaining(), 0)
                    f.press(4)
                    pressed = f.run.frame
                    self.assertIn(actor.entity, fight.translocators.entries)
                    with mock.patch.object(fight.translocators, "destroy_at",
                                           wraps=fight.translocators.destroy_at) as destroy:
                        if mode == "split gap":
                            self.advance_to(f, pressed + 1, gap)
                        self.advance_to(f, pressed + 59 + int(gap), gap)
                    destroy.assert_called_once_with(actor.entity, pressed + 2)
                    self.assertNotIn(actor.entity, fight.translocators.entries)
                    self.assertEqual(ability.last // f.a.body_script.frame_ms, pressed + 1)
                    self.assertAlmostEqual(ability.remaining(), 5.072, places=3)
                    timings.append(ability.remaining())
            self.assertEqual(timings, [timings[0]] * 3)

    def test_existing_beacon_cannot_recall_during_actual_stun_then_recovers(self):
        case = control.SombraControlTests()
        f = case.make()
        actor = self.cast(f)
        f.press(0, 80)
        end = case.stun(f)
        before = tuple(f.a.mover.position)
        f.press(16, 10)
        self.assertEqual(tuple(f.a.mover.position), before)
        self.assertIn(actor.entity, f.run.match.combat.translocators.entries)
        f.press(0, end - f.run.frame + 2)
        f.press(16)
        f.press(0, 60)
        self.assertFalse(f.run.match.combat.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)

    def test_actual_hack_and_emp_lock_existing_recall_until_status_expires(self):
        from ow174.game.content import heroes

        for button in (2, 0x20):
            with self.subTest(button=button):
                f = self.make()
                match = f.run.match
                fight = match.combat
                actor = self.cast(f)
                f.press(0, 300)
                self.assertTrue(actor.settled)
                self.assertTrue(match.switch_hero(f.b, heroes()[fixture.SOMBRA]))
                x, _, z = actor.position
                f.a.mover.position[:] = (x, 0, z - 2)
                f.b.mover.position[:] = (x + 3, 0, z)
                for player in (f.a, f.b):
                    player.mover.body = None
                f.press(0, 50)
                if button == 0x20:
                    f.b.body_script.set_ult_charge(1400)
                for _ in range(100):
                    f.press(button, shooter=f.b, target=f.a)
                    if f.a.body in fight.hacks:
                        break
                effect, = fight.hacks[f.a.body]
                before = tuple(f.a.mover.position)
                f.press(16, 12)
                self.assertEqual(tuple(f.a.mover.position), before)
                self.assertEqual(fight.vitals[actor.entity].total, 5)
                self.assertNotIn(actor.entity, fight.hacks)
                f.press(0, effect.until_tick - f.run.frame + 2)
                self.assertNotIn(f.a.body, fight.hacks)
                f.press(16)
                f.press(0, 60)
                self.assertNotIn(actor.entity, fight.translocators.entries)
                self.assertAlmostEqual(f.a.mover.position[2], z, places=2)

    def test_enemy_actual_rifle_destroys_five_hp_entity_without_hero_kill_or_respawn(self):
        f = self.make()
        actor = self.cast(f)
        f.press(0, 300)
        yaw, pitch = fixture.aim(f.run.match.combat._eye(f.b), actor.position)
        f.run.step({f.b: (yaw, pitch, FIRE)})
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)
        self.assertNotIn(actor.entity, f.run.match.combat.vitals)
        self.assertFalse(actor.body_script.component.instances)
        self.assertFalse(any(who is actor for _, who in f.run.match.combat.respawns))
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0C19)
        self.assertFalse(root.find_var(expr.INSTANCE, 5875).value())
        self.assertEqual(f.health(f.a), 200)
        self.assertTrue(any(u.entity == actor.entity and u.op == world.OP_DESTROY
                            for u in f.a.client.entities))

    def test_hostile_rifle_and_rocket_start_cooldown_at_the_world_impact(self):
        for rocket in (False, True):
            timings = []
            for gap in (False, True):
                with self.subTest(rocket=rocket, gap=gap):
                    f = self.make()
                    fight = f.run.match.combat
                    actor = self.cast(f)
                    f.press(0, 430)
                    self.assertTrue(actor.settled)
                    ability = actor.cast.state(2)
                    self.assertEqual(ability.remaining(), 0)
                    if rocket:
                        self.assertTrue(f.run.match.switch_hero(f.b, fixture.heroes()[pharah.HERO]))
                    x, _, z = actor.position
                    f.b.mover.position[:] = (x, 0, z + 6)
                    f.b.mover.body = None
                    f.press(0, 50)
                    yaw, pitch = fixture.aim(fight._eye(f.b), actor.position)
                    self.advance_to(f, f.run.frame + 180, gap)
                    f.run.frame += 1
                    fired = f.run.frame
                    f.run.match.update(fired * combat.TICK, fired, frames=False)
                    with mock.patch.object(fight.translocators, "destroy_at",
                                           wraps=fight.translocators.destroy_at) as destroy:
                        f.b.take_commands([Command(fired, yaw=yaw, pitch=pitch, buttons=FIRE)])
                        if not gap:
                            f.a.take_commands([Command(fired)])
                        self.advance_to(f, fired + 45 + int(gap), gap)
                    (entity, impact), = (call.args for call in destroy.call_args_list)
                    self.assertEqual(entity, actor.entity)
                    self.assertEqual(impact > fired, rocket)
                    self.assertEqual(ability.last // f.a.body_script.frame_ms, impact - 1)
                    self.assertNotIn(actor.entity, fight.translocators.entries)
                    self.assertNotIn(actor.entity, fight.vitals)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(f.b.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 0)
                    timings.append((impact - fired, ability.remaining()))
            self.assertEqual(timings[0], timings[1])

    def test_pilot_light_gun_destroys_enemy_beacon_without_character_credit(self):
        f = self.make()
        match = f.run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(f.b, fixture.heroes()[shot_guards.DVA]))
        f.b.mover.position[:] = (0, 0, 4)
        f.b.mover.body = None
        f.press(0, 50)
        actor = self.cast(f)
        f.press(0, 180)
        self.assertTrue(actor.settled)
        weapon = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x02B8)
        yaw, pitch = fixture.aim(fight._eye(f.b), actor.position)
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0

        f.run.step({f.b: (yaw, pitch, FIRE)})
        f.press(0, 40)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(fight.vitals[actor.entity].total, 5)

        f.b.team = 1
        f.run.step({f.b: (yaw, pitch, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 18)
        self.assertIn(actor.entity, fight.translocators.entries)
        f.press(0, 40)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertNotIn(actor.entity, fight.vitals)
        self.assertFalse(actor.body_script.component.instances)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.b.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 0)
        self.assertFalse(f.b.client.messages(combat.HIT))
        self.assertFalse(f.b.client.messages(combat.KILL_NOTICE))

    def test_owner_and_ally_actual_fire_do_not_destroy_the_entity(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                f = self.make()
                actor = self.cast(f)
                f.press(0, 300)
                shooter = f.a
                if ally:
                    f.run.match.game_map = fixture.KANEZAKA_DM.__class__(**{
                        **fixture.KANEZAKA_DM.__dict__, "free_for_all": False,
                    })
                    f.b.team = f.a.team
                    shooter = f.b
                yaw, pitch = fixture.aim(f.run.match.combat._eye(shooter), actor.position)
                f.run.step({shooter: (yaw, pitch, FIRE)})
                self.assertIn(actor.entity, f.run.match.combat.translocators.entries)
                self.assertEqual(f.run.match.combat.vitals[actor.entity].total, 5)
                f.press(16)
                f.press(0, 60)
                self.assertFalse(f.run.match.combat.translocators.entries)
                self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)

    def test_hero_replacement_retires_old_entity_and_does_not_transfer_it(self):
        f = self.make()
        actor = self.cast(f)
        self.assertTrue(f.run.match.switch_hero(f.a, f.a.hero))
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)
        self.assertFalse(actor.body_script.component.instances)
        self.assertNotIn(actor.entity, f.run.match.combat.vitals)

    def test_lifetime_deadline_and_fresh_identity_after_retirement(self):
        f = self.make()
        actor = self.cast(f)
        actor.until_tick = f.run.frame + 3  # isolate retirement clock without simulating999 seconds
        f.press(0, 2)
        self.assertIn(actor.entity, f.run.match.combat.translocators.entries)
        f.press(0)
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)
        f.press(0, 200)
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0C19)
        self.assertGreater(root.state(2).remaining(), 0)
        f.press(16, 20)
        self.assertFalse(f.run.match.combat.translocators.entries)
        f.press(0, math.ceil(root.state(2).remaining() / combat.TICK) + 3)
        second = self.cast(f)
        self.assertNotEqual(second.entity, actor.entity)

    def test_world_only_authored_lifetime_starts_cooldown_at_expiry(self):
        f = self.make()
        fight = f.run.match.combat
        actor = self.cast(f)
        f.press(0, 100)
        deadline = actor.until_tick
        ability = actor.cast.state(2)
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        f.press(FIRE, shooter=f.b, target=f.a)
        damage = f.run.frame
        self.assertEqual(f.health(f.a), 180)
        self.advance_to(f, deadline + 5, True)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertEqual(camo.last // f.a.body_script.frame_ms, damage + 47)
        self.assertEqual(ability.last // f.a.body_script.frame_ms, deadline - 1)
        self.assertAlmostEqual(ability.remaining(), 5.92, places=3)
        f.a.take_commands([Command(f.run.frame, buttons=16)])
        self.advance_to(f, deadline + 30, True)
        self.assertFalse(fight.translocators.entries)
        self.advance_to(f, deadline + 400, True, buttons=16)
        self.advance_to(f, deadline + 430, True)
        fresh, = fight.translocators.entries.values()
        self.assertNotEqual(fresh.entity, actor.entity)

    def test_actual_control_and_hack_reject_throw_until_their_deadlines(self):
        from ow174.game.content import heroes

        for mode in ("stun", "hack"):
            with self.subTest(mode=mode):
                case = control.SombraControlTests()
                f = case.make()
                if mode == "stun":
                    end = case.stun(f)
                else:
                    f.run.match.switch_hero(f.b, heroes()[fixture.SOMBRA])
                    f.b.mover.position[:] = (0, 0, 3)
                    f.b.mover.body = None
                    f.press(0, 50)
                    f.press(2, 100, shooter=f.b, target=f.a)
                    end = max(e.until_tick for e in f.run.match.combat.hacks[f.a.body])
                f.press(16, 20)
                self.assertFalse(f.run.match.combat.translocators.entries)
                f.press(0, end - f.run.frame + 2)
                self.cast(f)

    def test_actual_owner_death_retires_the_owned_entity(self):
        f = self.make()
        actor = self.cast(f)
        f.press(0, 80)
        f.press(FIRE, 100, shooter=f.b, target=f.a)
        self.assertEqual(f.health(f.a), 0)
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)

    def test_lethal_deflect_retires_beacon_before_next_frame(self):
        f = self.make()
        match = f.run.match
        self.assertTrue(match.switch_hero(f.b, fixture.heroes()[shot_guards.GENJI]))
        f.b.mover.position[:] = (0, 0, 4)
        f.b.mover.body = None
        f.press(0, 50)
        actor = self.cast(f)
        f.press(0, 180)
        fight = match.combat
        f.health(f.a)
        fight.vitals[f.a.body].now[world.HEALTH_POOL] = 8
        f.press(16, shooter=f.b, target=f.a)
        f.press(0, 4, shooter=f.b, target=f.a)
        self.assertIsNotNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
        self.assertIn(actor.entity, fight.translocators.entries)

        f.press(FIRE)
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(fight.vitals[f.a.body].killer, f.b.body)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertNotIn(actor.entity, fight.vitals)
        self.assertFalse(actor.body_script.component.instances)
        for viewer in (f.a, f.b):
            self.assertEqual(sum(u.entity == actor.entity and u.op == world.OP_DESTROY
                                 for u in viewer.client.entities), 1)

    def test_active_entity_is_captured_with_cross_component_watchers(self):
        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        match = player.match
        session.apply("switch_hero", {"account": player.account_lo, "hero": fixture.SOMBRA})
        for n in range(60):
            tick = match.tick + 1
            match.update(tick * combat.TICK, tick)
            player.take_commands([Command(player.body_script.frame + 1, buttons=16 if n == 1 else 0)])
            if match.combat.translocators.entries and n > 20:
                break
        self.assertEqual(len(match.combat.translocators.entries), 1)
        actor = next(iter(match.combat.translocators.entries.values()))
        first = case.capture(session)
        self.assertEqual(stored.snapshot_digest(first), stored.snapshot_digest(case.capture(session)))
        actor.position = (actor.position[0] + 1, actor.position[1], actor.position[2])
        self.assertNotEqual(stored.snapshot_digest(first), stored.snapshot_digest(case.capture(session)))
        before = stored.snapshot_digest(case.capture(session))
        match.combat.vitals[actor.entity].now[world.HEALTH_POOL] = 4
        self.assertNotEqual(before, stored.snapshot_digest(case.capture(session)))


class TranslocatorNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("ORPHEUS_TRANSLOCATOR_DATA")
        if not path:
            raise unittest.SkipTest("set ORPHEUS_TRANSLOCATOR_DATA to the additive entity record")
        patch = mock.patch.object(translocators, "DATA_PATH", Path(path))
        patch.start()
        cls.addClassCleanup(patch.stop)
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_actual_npc_volley_can_destroy_beacon_between_it_and_its_target(self):
        for key in ("1", "2", "3", "5"):
            for allied, gap in ((False, False), (False, True), (True, False)):
                with self.subTest(npc=self.catalog[key].name, allied=allied, gap=gap):
                    f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team, f.b.team = (bot.team if allied else 0), 0
                    actor = TranslocatorTests.cast(self, f)
                    f.press(0, 410)
                    self.assertTrue(actor.settled)
                    x, y, z = actor.position
                    # Lower the firing lane so eye-height fire crosses the settled beacon.
                    floor = y - combat.EYE
                    f.floor = [tuple(value + floor if index % 3 == 1 else value
                                     for index, value in enumerate(triangle)) for triangle in f.floor]
                    f.geometry()
                    bot.position = (x - 3, floor, z)
                    f.b.spawned = True
                    eye = fight._eye(f.b)[1] - f.b.mover.position[1]
                    f.b.mover.position[:] = (x + 4, y - eye, z + 2)
                    f.b.mover.body = None
                    yaw, pitch = fixture.aim(fight._eye(f.b), (x - 3, y, z))
                    f.run.step({f.b: (yaw, pitch, 0)})
                    if key == "3":
                        # Keep the firing lane fixed using the authored reposition inhibit.
                        special = next(item for item in bot.body_script.component.instances.values()
                                       if item.graph.index == 0x142C)
                        delivered = send_instance_message(special, 0x0240000000000A45, {}, bot.entity)
                        self.assertEqual(delivered, 1)
                    self.assertEqual(npc_control.start(match, f.b), bot.entity)
                    f.b.mover.position[2] = z
                    f.b.mover.body = None
                    for _ in range(380):
                        if gap:
                            f.run.frame += 1
                            match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                        else:
                            f.press(0)
                        if actor.entity not in fight.translocators.entries or f.health(f.b) < 200:
                            break
                    self.assertEqual(actor.entity in fight.translocators.entries, allied)
                    self.assertEqual(actor.entity in fight.vitals, allied)
                    if allied:
                        self.assertEqual(fight.vitals[actor.entity].total, 5)
                        self.assertLess(f.health(f.b), 200)
                    else:
                        self.assertFalse(actor.body_script.component.instances)
                        impact = f.run.frame
                        ability = actor.cast.state(2)
                        self.assertEqual(ability.last // f.a.body_script.frame_ms, impact - 1)
                        npc_control.stop(match)
                        TranslocatorTests.advance_to(f, impact + 29 + int(gap), gap)
                        self.assertAlmostEqual(ability.remaining(), 5.52, places=3)
                    self.assertEqual(f.health(f.a), 200)

    def test_hosted_npc_melee_hits_owner_without_selecting_nearby_beacon(self):
        for key, reach, damage in (("1", 1.5, 30), ("5", 1.0, 80)):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                actor = TranslocatorTests.cast(self, f)
                f.press(0, 300)
                self.assertTrue(actor.settled)
                x, y, z = actor.position
                # Place the device at the centre of the authored melee search.
                bot.position = (x, y - 1.5, z - reach)
                bot.yaw = 0
                f.a.mover.position[:] = (x + 0.5, 0, z)
                f.a.mover.body = None
                f.press(0, 2)
                bot.body_script.component.edges([(31, True)])
                f.press(0, 35)
                self.assertEqual(f.health(f.a), 200 - damage)
                self.assertEqual(f.run.match.combat.vitals[actor.entity].total, 5)

    def test_actual_npc_fire_reacquires_recall_destination_after_cover_is_removed(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                actor = TranslocatorTests.cast(self, f)
                f.press(0, 180)
                self.assertTrue(actor.settled)
                x, _, z = actor.position
                wall_z = (z + bot.position[2]) / 2
                self.assertGreater(z - wall_z, 1)
                f.geometry((-5, 0, wall_z, 5, 0, wall_z, 5, 4, wall_z),
                           (-5, 0, wall_z, 5, 4, wall_z, -5, 4, wall_z))
                component = bot.body_script.component
                if key == "3":
                    special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
                actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
                match = f.run.match
                self.assertEqual(npc_control.start(match, f.a), bot.entity)
                for _ in range(380):
                    f.press(0)
                    if f.health(f.a) < 200:
                        break
                self.assertLess(f.health(f.a), 200)
                self.assertTrue(bot.attack_input)
                self.assertEqual(match.combat.vitals[actor.entity].total, 5)
                old_yaw = bot.yaw

                f.press(16)
                for _ in range(60):
                    f.press(0)
                    if f.a.mover.position[2] > wall_z:
                        break
                self.assertAlmostEqual(f.a.mover.position[0], x, places=2)
                self.assertAlmostEqual(f.a.mover.position[2], z, places=2)
                after_recall = f.health(f.a)
                f.press(0, 60)
                self.assertNotIn(actor.entity, match.combat.translocators.entries)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(npc_control.button(bot), component.held)
                self.assertEqual(bot.attack_target, f.a.entity)
                self.assertEqual(f.health(f.a), after_recall)

                f.geometry()
                f.press(0)
                self.assertTrue(bot.attack_input)
                self.assertAlmostEqual((bot.yaw - old_yaw) % 65536, 32768, delta=1)
                for _ in range(380):
                    if f.health(f.a) < after_recall:
                        break
                    f.press(0)
                self.assertLess(f.health(f.a), after_recall)

    def test_autonomous_npcs_drop_recalled_target_under_cover_and_reacquire(self):
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("4", 25), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                f = fixture.SombraCombatTests()
                f.setUp()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = 0
                f.b.spawned = False
                for player in f.run.players:
                    player.client.bot_stream = lambda body: None
                actor = TranslocatorTests.cast(self, f)
                f.press(0, 180)
                self.assertTrue(actor.settled)
                x, _, z = actor.position
                wall_z = z - 1
                f.geometry((x - 5, 0, wall_z, x + 5, 0, wall_z, x + 5, 4, wall_z),
                           (x - 5, 0, wall_z, x + 5, 4, wall_z, x - 5, 4, wall_z))
                f.a.mover.position[:] = (x, 0, z - 6)
                f.a.mover.body = None
                bot = npc.spawn(match, self.catalog[key], (x, 0, z - 2.25),
                                team=0, autonomous=True)
                component = bot.body_script.component
                component.set(9948, 0)
                f.press(0, 75)
                self.assertIsNone(bot.attack_target)
                if key == "3":
                    special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)

                bot.team = 1
                if key == "4":
                    # Starting out of the pin's reach keeps recall available.
                    f.press(0, 5)
                    self.assertIsNone(bot.attack_target)
                    self.assertNotIn(bot.entity, fight.pins)
                else:
                    for _ in range(170):
                        f.press(0)
                        if f.health(f.a) < 200:
                            break
                    self.assertEqual(bot.attack_target, f.a.entity)
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(f.health(f.a), 200 - damage)
                self.assertEqual(fight.vitals[actor.entity].total, 5)

                f.press(16)
                for _ in range(60):
                    f.press(0)
                    if actor.entity not in fight.translocators.entries:
                        break
                self.assertNotIn(actor.entity, fight.translocators.entries)
                self.assertAlmostEqual(f.a.mover.position[0], x, places=2)
                self.assertAlmostEqual(f.a.mover.position[2], z, places=2)
                covered_health = f.health(f.a)
                f.press(0, 60)
                self.assertEqual(f.health(f.a), covered_health)
                self.assertIsNone(bot.attack_target)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(npc_control.button(bot), component.held)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(sum(u.entity == actor.entity and u.op == world.OP_DESTROY
                                     for u in f.a.client.entities), 1)

                f.geometry()
                f.press(0)
                self.assertEqual(bot.attack_target, f.a.entity)
                self.assertTrue(bot.attack_input)
                for _ in range(220):
                    if f.health(f.a) < covered_health:
                        break
                    f.press(0)
                self.assertEqual(f.health(f.a), max(0, covered_health - damage))
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), f.a.body)
                elif key == "3":
                    self.assertTrue(fight.is_dead(f.a))
                    self.assertEqual(fight.vitals[f.a.body].killer, bot.entity)
                    f.press(0)
                    self.assertIsNone(bot.attack_target)
                    self.assertFalse(bot.attack_input)

    def test_actual_npc_lethal_weapon_hit_retires_beacon_on_death_frame(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                for _ in range(20):
                    f.run.step({f.a: (0, 0, 16)})
                match = f.run.match
                fight = match.combat
                actor, = fight.translocators.entries.values()
                f.press(0, 180)
                self.assertTrue(actor.settled)
                if key == "3":
                    special = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
                actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
                f.health(f.a)
                vitals = fight.vitals[f.a.body]
                vitals.now[world.HEALTH_POOL] = 1
                self.assertEqual(npc_control.start(match, f.a), bot.entity)
                for _ in range(380):
                    self.assertIn(actor.entity, fight.translocators.entries)
                    f.press(0)
                    if fight.is_dead(f.a):
                        break
                self.assertTrue(fight.is_dead(f.a))
                self.assertEqual(vitals.killer, bot.entity)
                self.assertEqual(vitals.death_tick, f.run.frame)
                self.assertNotIn(actor.entity, fight.translocators.entries)
                self.assertFalse(actor.body_script.component.instances)
                self.assertEqual(sum(u.entity == actor.entity and u.op == world.OP_DESTROY
                                     for u in f.a.client.entities), 1)

    def test_actual_npc_lethal_weapon_hit_absorbed_preserves_beacon_for_recall(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                f.b.spawned = True
                self.assertTrue(match.switch_hero(f.b, fixture.heroes()[reinhardt_barrier.ZARYA]))
                f.b.mover.position[:] = (3, 0, 0)
                f.b.mover.body = None
                for _ in range(20):
                    f.run.step({f.a: (0, 0, 16)})
                actor, = fight.translocators.entries.values()
                f.press(0, 180)
                self.assertTrue(actor.settled)
                if key == "3":
                    special = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)

                actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
                if key == "3":
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    f.press(0, 48)
                yaw, pitch = actual_npcs.aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.b: (yaw, pitch, 16)})
                actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, f.a)
                self.assertEqual(f.health(f.a), 200)
                fight.vitals[f.a.body].now[world.HEALTH_POOL] = 1
                if key != "3":
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)

                for _ in range(170):
                    f.press(0)
                    if bubble.vitals.total < 200 or fight.is_dead(f.a):
                        break
                self.assertLess(bubble.vitals.total, 200)
                self.assertEqual(f.health(f.a), 1)
                self.assertIs(fight.translocators.entries[actor.entity], actor)
                self.assertEqual(fight.vitals[actor.entity].total, 5)
                npc_control.stop(match)
                f.press(0, 150)
                self.assertNotIn(bubble.entity, fight.barriers)
                self.assertIn(actor.entity, fight.translocators.entries)

                f.press(16)
                f.press(0, 60)
                self.assertNotIn(actor.entity, fight.translocators.entries)
                self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)
                self.assertEqual(f.health(f.a), 1)
                self.assertEqual(sum(u.entity == actor.entity and u.op == world.OP_DESTROY
                                     for u in f.a.client.entities), 1)

    def test_actual_assassin_lethal_pin_retires_beacon_on_death_frame(self):
        f, bot = actual_npcs.SombraActualNpcTests.make(self, "4")
        actor = TranslocatorTests.cast(self, f)
        f.press(0, 300)
        self.assertTrue(actor.settled)
        x, _, z = actor.position
        bot.position = (x, 0, z - 1)
        f.a.mover.position[:] = (x, 0, z + 1)
        f.a.mover.body = None
        actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        f.press(0, 8)
        fight = f.run.match.combat
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertEqual(f.health(f.a), 175)
        self.assertEqual(fight.vitals[actor.entity].total, 5)
        vitals = fight.vitals[f.a.body]
        vitals.now[world.HEALTH_POOL] = 1
        for _ in range(380):
            self.assertIn(actor.entity, fight.translocators.entries)
            f.press(0)
            if fight.is_dead(f.a):
                break
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(vitals.killer, bot.entity)
        self.assertEqual(vitals.death_tick, f.run.frame)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertFalse(actor.body_script.component.instances)
        self.assertEqual(sum(u.entity == actor.entity and u.op == world.OP_DESTROY
                             for u in f.a.client.entities), 1)

    def test_actual_assassin_pin_targets_owner_and_blocks_recall_until_release(self):
        f, bot = actual_npcs.SombraActualNpcTests.make(self, "4")
        actor = TranslocatorTests.cast(self, f)
        f.press(0, 300)
        self.assertTrue(actor.settled)
        x, _, z = actor.position
        bot.position = (x, 0, z - 1)
        f.a.mover.position[:] = (x, 0, z + 1)
        f.a.mover.body = None
        actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        f.press(0, 8)
        fight = f.run.match.combat
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertEqual(f.health(f.a), 175)
        self.assertEqual(fight.vitals[actor.entity].total, 5)
        f.press(16, 12)
        self.assertIn(actor.entity, fight.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], z + 1, places=2)
        npc_control.stop(f.run.match)
        f.press(0, 20)
        self.assertNotIn(bot.entity, fight.pins)
        f.press(16)
        f.press(0, 60)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], z, places=2)

    def test_actual_npc_capture_rejects_throw_and_discards_released_input(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                match = f.run.match
                fight = match.combat
                if key == "4":
                    actual_npcs.SombraActualNpcTests.press(f, bot, 0, 2)
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    f.press(0, 8)
                    self.assertEqual(f.health(f.a), 175)
                else:
                    bot.yaw = -32768
                    component = bot.body_script.component
                    component.edges([(35, True)])
                    f.press(0, 75)
                    component.edges([(35, False)])
                self.assertEqual(fight.pins[bot.entity], f.a.body)

                instances = f.a.body_script.component.instances.values()
                root = next(i for i in instances if i.graph.index == 0x0C19)
                throw = root.state(2)
                f.press(16, 12)
                self.assertEqual(fight.pins[bot.entity], f.a.body)
                self.assertFalse(fight.translocators.entries)
                self.assertFalse(throw.flags & throw.RUNNING)
                self.assertEqual(throw.remaining(), 0)

                f.press(0)
                self.assertEqual(fight.pins[bot.entity], f.a.body)
                if key == "4":
                    npc_control.stop(match)
                f.press(0, 250)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.translocators.entries)
                self.assertFalse(root.find_var(expr.INSTANCE, 5875).value())

                actor = TranslocatorTests.cast(self, f)
                self.assertEqual(root.vars[5875].value(), expr.Entity(actor.entity))
                self.assertEqual(fight.vitals[actor.entity].total, 5)

    def test_actual_heavy_capture_blocks_recall_until_victim_graph_releases(self):
        f, bot = actual_npcs.SombraActualNpcTests.make(self, "5")
        actor = TranslocatorTests.cast(self, f)
        f.press(0, 300)
        self.assertTrue(actor.settled)
        fight = f.run.match.combat
        bot.yaw = -32768
        component = bot.body_script.component
        component.edges([(35, True)])
        f.press(0, 75)
        component.edges([(35, False)])
        victim = f.a.body_script.component
        child, = [item for item in victim.instances.values() if item.graph.index == 0x1407]
        self.assertEqual(child.source_entity(), bot.entity)
        self.assertEqual(fight.pins[bot.entity], f.a.body)

        f.press(16, 12)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertEqual(fight.vitals[actor.entity].total, 5)
        self.assertGreater(abs(f.a.mover.position[2] - actor.position[2]), 1)

        f.press(0, 250)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(child.id, victim.instances)
        self.assertIn(actor.entity, fight.translocators.entries)
        f.press(16)
        f.press(0, 60)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)

    def test_actual_throw_lands_without_damaging_any_of_the_five_npc_classes(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                before = f.run.match.combat.vitals[bot.entity].total
                f.press(16, 20)
                actors = list(f.run.match.combat.translocators.entries.values())
                self.assertEqual(len(actors), 1)
                actor = actors[0]
                f.press(0, 300)
                self.assertTrue(actor.settled)
                self.assertEqual(f.run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(f.run.match.combat.vitals[actor.entity].total, 5)
                f.press(16)
                f.press(0, 60)
                self.assertFalse(f.run.match.combat.translocators.entries)
                self.assertAlmostEqual(f.a.mover.position[2], actor.position[2], places=2)
                self.assertEqual(f.run.match.combat.vitals[bot.entity].total, before)


if __name__ == "__main__":
    unittest.main()
