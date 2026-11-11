"""Stored domain hashes detect hidden causal state without executing the game."""

import copy
import dataclasses
import os
import sys
import tempfile
import types
import unittest
import weakref
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_server_journal as journal_tests
from test_server_journal import MAP, FixedSource, Peer, acknowledge_ownership, pick

from ow174.game import (
    ana_biotic,
    ana_sleep,
    barriers,
    combat,
    earthshatter,
    graviton,
    mei_blizzard,
    npc,
    sigma_grasp,
    zarya_barrier,
)
from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER, find_hero
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import graph, message_receive, runtime
from ow174.game.script.driver import ULT_COST, BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement

SIGMA_PROFILE = Path(__file__).resolve().parents[3] / "0040" / "sigma_collision_174.json"
ASSASSIN_CONTROLLER_BUNDLE = Path(__file__).resolve().parents[2] / "runtime-inputs" / "assassin-controller-graphs-104319.json.gz"


class ServerSnapshotTests(unittest.TestCase):
    def scene(self, two=False):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, two=two)
        return session, peers, players

    @staticmethod
    def capture(session):
        return export_server_state(session.server, archives=session._matches.values(),
                                   collision_provider=session._collision_reads, assets=session.registry)

    def test_pending_reinhardt_charge_is_causal_between_body_creates(self):
        session, _, players = self.scene()
        player = players[0]
        before = snapshot_digest(self.capture(session))
        player.retained_ult_charge = 85.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        player.retained_ult_charge = None
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_turret_carrier_and_landed_health_are_causal_stored_state(self):
        session, peers, players = self.scene()
        player = players[0]
        peer = peers[0]
        hero = find_hero("Torbjorn")
        now = 100.928
        peer.message(21616, pick(player.entity, hero.guid), now=now)
        self.assertEqual(player.hero.guid, hero.guid)
        fight = player.match.combat

        def step(buttons=0):
            nonlocal now
            peer.commands(player.body_script.frame + 1, 1, buttons, now=now)
            now += .016
            session.tick(now)
            peer.drain()
            peer.send(now=now)

        for _ in range(20):
            step()
        for _ in range(12):
            step(8)
            if any(rocket.turret_instance is not None for rocket in fight.rockets):
                break
        [carrier] = [rocket for rocket in fight.rockets if rocket.turret_instance is not None]
        launched = snapshot_digest(self.capture(session))
        previous = carrier.left
        carrier.left -= .125
        self.assertNotEqual(snapshot_digest(self.capture(session)), launched)
        carrier.left = previous
        self.assertEqual(snapshot_digest(self.capture(session)), launched)
        for _ in range(95):
            step()
            if fight.torbjorn_turrets.devices:
                break
        [device] = fight.torbjorn_turrets.devices.values()
        landed = snapshot_digest(self.capture(session))
        device.health -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), landed)
        device.health += 1
        self.assertEqual(snapshot_digest(self.capture(session)), landed)
        vitals = fight.vitals_of(fight._target_entity(player.body))
        before = snapshot_digest(self.capture(session))
        vitals.last_damage_source = device.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        vitals.last_damage_source = None
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_typed_body_message_packet_is_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        match = player.match
        world = player.body_script.world
        packet = message_receive.Packet(0x024000000000F105, player.body, player.body,
                                       ((0x0A400000000000A3, (17, 23)),))
        initial = snapshot_digest(self.capture(session))
        self.assertTrue(match.queue_body_packet(packet, (player.body,), world))
        with mock.patch.object(message_receive, "deliver", side_effect=AssertionError("no packet delivery")):
            queued = self.capture(session)
        self.assertNotEqual(snapshot_digest(queued), initial)
        self.assertTrue(any(node["type"] == "ow174.game.script.message_receive.Packet" for node in queued["nodes"]))
        self.assertIs(match._body_messages[0][3], packet)
        match.deliver_body_messages()
        self.assertFalse(match._body_messages)

    @unittest.skipUnless(ASSASSIN_CONTROLLER_BUNDLE.is_file(), "authorized Assassin controller graphs required")
    def test_mounted_assassin_controller_is_in_passive_server_storage(self):
        def clear():
            graph.graph.cache_clear()
            graph._records.cache_clear()
            graph._data.cache_clear()

        self.enterContext(mock.patch.object(graph, "GRAPHS_PATH", ASSASSIN_CONTROLLER_BUNDLE))
        self.addCleanup(clear)
        clear()
        session, _, players = self.scene()
        actor = npc.spawn(players[0].match, npc.Definition(0x02E00000000001BB, 0x0400000000001EBD,
                                                         "Assassin", 500), (0, 1, 2), team=1)
        controller = actor.body_script.assassin_controller
        self.assertIsNotNone(controller)
        packet = message_receive.Packet(0x024000000000F106, controller.entity, actor.entity)
        self.assertTrue(players[0].match.queue_body_packet(packet, (actor.entity,), controller.world))
        with mock.patch.object(runtime.Component, "run_frame", side_effect=AssertionError("no controller update")):
            document = self.capture(session)
        self.assertTrue(any(node["type"] == "ow174.game.script.bot.AssassinControllerWorld"
                            for node in document["nodes"]))
        self.assertTrue(any(node["type"] == "ow174.game.script.entity_filter.Context"
                            for node in document["nodes"]))
        self.assertTrue(any(node["type"] == "ow174.game.script.message_receive.Packet"
                            for node in document["nodes"]))
        self.assertIs(actor.body_script.assassin_controller, controller)
        self.assertIs(players[0].match._body_messages[0][3], packet)

    @unittest.skipUnless(SIGMA_PROFILE.is_file(), "prepared Sigma C015 mesh required")
    def test_sigma_barrier_owner_team_is_causal_stored_state(self):
        self.enterContext(mock.patch.dict(os.environ, {"ORPHEUS_SIGMA_COLLISION": str(SIGMA_PROFILE)}))
        session, peers, (attacker, sigma) = self.scene(two=True)
        peers[1].message(21616, pick(sigma.entity, sigma_grasp.SIGMA), now=101.0)
        session.tick(101.0)
        self.assertEqual(sigma.hero.guid, sigma_grasp.SIGMA)
        manager = sigma.match.combat.sigma_barriers
        actor = manager.actors[sigma.body]
        manager.set_mode(actor, 3)
        self.assertEqual(list(manager.enemies(attacker)), [actor])
        before = snapshot_digest(self.capture(session))

        original_team = sigma.team
        sigma.team = attacker.team
        self.assertEqual(list(manager.enemies(attacker)), [])
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

        sigma.team = original_team
        self.assertEqual(list(manager.enemies(attacker)), [actor])
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_charge_heading_and_drive_release_mode_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        fight.charge_drives[player.body] = (7, .25)
        before = snapshot_digest(self.capture(session))
        fight.charge_drives[player.body] = (7, .5)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        fight.charge_drives[player.body] = (7, .25)
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        player.mover.horizontal_driven = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_possessor_query_context_is_stored_without_evaluating_the_relation(self):
        from ow174.game.script import entity_queries

        session, _, players = self.scene()
        player = players[0]
        world = player.body_script.world
        context = world._possessor_query = entity_queries.Context(
            player.body, True, True, True, lookup=world._lookup_possessor,
            watch_environment=None, watch_possession=None, watch_possessor=world._watch_possessor,
        )
        with mock.patch.object(runtime.Instance, 'evaluate',
                               side_effect=AssertionError('no query execution')):
            before = snapshot_digest(self.capture(session))
            context.retired = True
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            context.retired = False
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_capture_victim_deduplication_and_control_deadlines_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        capture = combat.ChargeContact(3, 0xA0000301, {(0xA0000301, 1000)})
        fight.charge_contacts[player.body] = capture
        fight.controls[0xA0000301, player.body, "pin"] = 4.0
        before = snapshot_digest(self.capture(session))
        capture.hit.add((0xA0000301, 1001))
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        capture.hit.remove((0xA0000301, 1001))
        capture.pinned = None
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        capture.pinned = 0xA0000301
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        fight.controls[0xA0000301, player.body, "pin"] = 5.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_npc_impulse_physics_is_part_of_causal_server_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        physics = Mover((0, 1, 3), world=player.mover.world, data=player.mover.data)
        fight.knockbacks[0xC0001001] = physics
        before = snapshot_digest(self.capture(session))
        physics.impulse_active = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        physics.impulse_active = False
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        physics.add_impulse((1, 2, 3), 1000)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_external_npc_pose_flags_and_gravity_are_replicated_and_stored(self):
        session, _, players = self.scene()
        player = players[0]
        definition = npc.Definition(SOLDIER, player.hero.body, "Pose fixture", 200)
        bot = npc.spawn(player.match, definition, (0, 1, 3), team=1)
        before = snapshot_digest(self.capture(session))
        bot.movement_flags = mover_module.IN_AIR
        airborne = snapshot_digest(self.capture(session))
        self.assertNotEqual(airborne, before)
        self.assertEqual(bot.movement(1000).flags, mover_module.IN_AIR)
        bot.movement_gravity = 4.0
        self.assertEqual(bot.movement(1000).gravity, 4.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), airborne)

    def test_ground_front_blockers_and_hit_history_are_passive_causal_storage(self):
        session, _, players = self.scene()
        player = players[0]
        wave = earthshatter.Wave(
            player, player.body, 1, 1, (0, 1, 0), 0, 40, 20, 50, 1.75, 2.5,
            50, 250, (0, 1, 3), 1.75, 0x0B90000000000027, 10,
        )
        player.match.combat.shatters[player.body] = wave
        before = snapshot_digest(self.capture(session))
        wave.tags = (0x0D800000000001FD,)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        wave.tags = ()
        wave.near_tags = (0x0D800000000001FD,)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        wave.near_tags = ()
        wave.blocked.add(50)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        wave.blocked.clear()
        wave.hit.add((0xA0000301, 1000))
        victim_history = snapshot_digest(self.capture(session))
        self.assertNotEqual(victim_history, before)
        wave.hit.clear()
        wave.hit.add((0xA0000301, 1001))
        self.assertNotEqual(snapshot_digest(self.capture(session)), victim_history)
        wave.hit.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        wave.shield_hit.add((0xA0000301, 1000, 1))
        first_raise = snapshot_digest(self.capture(session))
        self.assertNotEqual(first_raise, before)
        wave.shield_hit.clear()
        wave.shield_hit.add((0xA0000301, 1000, 2))
        self.assertNotEqual(snapshot_digest(self.capture(session)), first_raise)
        wave.shield_hit.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        wave.shield_poses.append(((0, 1, 5), 0, 0, .7, 2.6, 0xA0000301))
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_penetrating_projectile_victim_history_is_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        rocket = combat.Rocket(player, player.team, (0.0, 2.0, 0.0), (0.0, 0.0, 1.0), 25.0, 4.0,
                               combat.HitDef(100), None, radius=0.85, penetrating=True)
        player.match.combat.rockets.append(rocket)
        before = snapshot_digest(self.capture(session))
        rocket.direct.tags = (0x0D800000000001FD,)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        rocket.direct.tags = ()
        rocket.hit_targets.add((0xC0001001, 1000))
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        rocket.hit_targets.clear()
        rocket.hit_targets.add((-0xA0000301, (1000, 1)))
        shield_history = snapshot_digest(self.capture(session))
        self.assertNotEqual(shield_history, before)
        rocket.hit_targets.clear()
        rocket.hit_targets.add((-0xA0000301, (1001, 1)))
        self.assertNotEqual(snapshot_digest(self.capture(session)), shield_history)
        rocket.hit_targets.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        rocket.charge_origin = (player.body, 1000)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        rocket.charge_origin = None
        rocket.definition = 0x04000000000001EA
        defined = snapshot_digest(self.capture(session))
        self.assertNotEqual(defined, before)
        rocket.reflected = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), defined)

    def test_in_flight_helix_knockback_rings_are_causal_stored_state(self):
        session, (peer,), (player,) = self.scene()
        self.assertEqual(player.hero.guid, SOLDIER)
        now = 100.928
        fight = player.match.combat
        for _ in range(12):
            peer.commands(player.body_script.frame + 1, 1, 2, now=now)
            now += .016
            session.tick(now)
            peer.drain()
            peer.send(now=now)
            if fight.rockets:
                break
        [rocket] = fight.rockets
        self.assertEqual(rocket.definition, 0x040000000000072C)
        rings = rocket.blast.knockback_rings
        self.assertEqual(rings, ((0.5, 4, 6), (3, 3, 1.5)))
        before = snapshot_digest(self.capture(session))
        rocket.blast.knockback_rings = ()
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        rocket.blast.knockback_rings = rings
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_actual_hook_flight_is_causal_stored_state(self):
        session, (peer,), (player,) = self.scene()
        now = 100.928
        hero = find_hero("Roadhog")
        peer.message(21616, pick(player.entity, hero.guid), now=now)
        self.assertEqual(player.hero.guid, hero.guid)
        fight = player.match.combat
        for offset in range(42):
            peer.commands(player.body_script.frame + 1, 1, 8 if offset == 16 else 0, now=now)
            now += .016
            session.tick(now)
            peer.drain()
            peer.send(now=now)
            if fight.hooks.flights:
                break
        flight = fight.hooks.flights[player.body]
        self.assertEqual(flight.damage.amount, 30)
        self.assertIsNone(flight.target)
        before = snapshot_digest(self.capture(session))
        travelled = flight.travelled
        flight.travelled += .125
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        flight.travelled = travelled
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_grasp_pool_and_pending_decay_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        vitals = fight.vitals_of(fight._target_entity(player.body))
        before = snapshot_digest(self.capture(session))
        vitals.grasp_shields = vitals.grasp_max = 60
        granted = snapshot_digest(self.capture(session))
        self.assertNotEqual(granted, before)
        vitals.grasp_decay = (fight.now + 3, 5.0)
        pending = snapshot_digest(self.capture(session))
        self.assertNotEqual(pending, granted)
        vitals.grasp_activation = (1, 2)
        self.assertNotEqual(snapshot_digest(self.capture(session)), pending)

    def test_reinhardt_frost_progress_is_causal_stored_state(self):
        session, _, players = self.scene(two=True)
        player, source = players[:2]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        fight.frost[player.body] = {(source.body, 1000): (0.2, fight.now + 1)}
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_ice_wall_pillar_health_and_expiry_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        walls = player.match.combat.walls
        walls.create(player, ((0, 1, 3),), (1, 0, 0))
        [piece] = walls.pieces.values()
        created = snapshot_digest(self.capture(session))
        piece.health -= 100
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, created)
        piece.expires += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

    def test_blizzard_flight_area_and_pulse_deadline_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        field = player.match.combat.blizzard
        before = snapshot_digest(self.capture(session))
        flight = mei_blizzard.Flight(player, player.body, player.team, (0, 2, 3), (1, 2, 3), 9.8, 60, 1000)
        field.flights.append(flight)
        launched = snapshot_digest(self.capture(session))
        self.assertNotEqual(launched, before)
        area = mei_blizzard.Area(70, player, player.body, player.team, (0, 1, 3), 1000, 5250, 1000)
        field.areas[70] = area
        landed = snapshot_digest(self.capture(session))
        self.assertNotEqual(landed, launched)
        area.frost_due[player.body] = 1200
        self.assertNotEqual(snapshot_digest(self.capture(session)), landed)

    def test_sleep_deadline_and_waking_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        sleep = ana_sleep.Sleep(fight.tick, fight.tick + 313)
        fight.sleeps[player.body] = sleep
        sleeping = snapshot_digest(self.capture(session))
        self.assertNotEqual(sleeping, before)
        sleep.end -= 100
        shorter = snapshot_digest(self.capture(session))
        self.assertNotEqual(shorter, sleeping)
        sleep.waking = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), shorter)

    def test_grenade_healing_windows_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        vitals = fight.vitals_of(fight._target_entity(player.body))
        before = snapshot_digest(self.capture(session))
        ana_biotic.refresh(vitals.healing_block, fight.now)
        blocked = snapshot_digest(self.capture(session))
        self.assertNotEqual(blocked, before)
        ana_biotic.refresh(vitals.healing_boost, fight.now)
        self.assertNotEqual(snapshot_digest(self.capture(session)), blocked)

    def test_zarya_barrier_pool_and_activation_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        bubble = zarya_barrier.Bubble(player.body, fight.now + 2)
        fight.zarya_bubbles[player.body] = bubble
        active = snapshot_digest(self.capture(session))
        self.assertNotEqual(active, before)
        bubble.health -= 85
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, active)
        bubble.source_retired = True
        retired_source = snapshot_digest(self.capture(session))
        self.assertNotEqual(retired_source, damaged)
        fight.zarya_casts[player.body, 5] = 2
        self.assertNotEqual(snapshot_digest(self.capture(session)), retired_source)

    def test_zarya_beam_last_accepted_frame_is_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        fight.zarya_beam_frames[player.body] = fight.tick
        before = snapshot_digest(self.capture(session))
        fight.zarya_beam_frames[player.body] += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_graviton_area_clock_and_pull_ownership_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        field = graviton.Field(player, player.body, player.team, (0, 1, 2), fight.tick + 250, fight.tick)
        fight.gravitons.fields.append(field)
        landed = snapshot_digest(self.capture(session))
        self.assertNotEqual(landed, before)
        field.last_tick += 1
        advanced = snapshot_digest(self.capture(session))
        self.assertNotEqual(advanced, landed)
        fight.gravitons.pulled.add(player.body)
        pulling = snapshot_digest(self.capture(session))
        self.assertNotEqual(pulling, advanced)
        mover = Mover((0, 1, 3), world=player.mover.world, data=player.mover.data)
        fight.gravitons.npc_movers[0xC0001001] = mover
        self.assertNotEqual(snapshot_digest(self.capture(session)), pulling)

    def test_authored_winston_primal_bonus_is_causal_stored_state(self):
        session, peers, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000009})
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        peers[0].commands(script.frame + 1, 1, 32, now=101.0)
        primal = player.match.combat.winston_primal
        self.assertEqual(primal.bonus[player.body], 500)
        before = snapshot_digest(self.capture(session))
        primal.bonus[player.body] = 499
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_authored_winston_jump_flight_is_causal_stored_state(self):
        session, peers, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000009})
        peers[0].commands(player.body_script.frame + 1, 1, 8, now=101.0)
        flights = player.match.combat.winston_jump.flights
        instance, frame = flights[player.body]
        self.assertEqual(instance.graph.index, 0x010A)
        before = snapshot_digest(self.capture(session))
        flights[player.body] = instance, frame + 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_winston_tesla_frame_is_causal_stored_state(self):
        session, peers, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000009})
        peers[0].commands(player.body_script.frame + 1, 20, 1, now=101.0)
        frames = player.match.combat.tesla_frames
        self.assertIn(player.body, frames)
        before = snapshot_digest(self.capture(session))
        frames[player.body] += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_authored_winston_dome_is_causal_stored_state(self):
        session, peers, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000009})
        peers[0].commands(player.body_script.frame + 1, 20, 16, now=101.0)
        manager = player.match.combat.winston_bubbles
        [dome] = manager.actors.values()
        self.assertEqual(dome.health, 700)
        before = snapshot_digest(self.capture(session))
        manager.damage(dome, 100)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    @unittest.skipUnless(
        Path("D:/overwatch 1.7.4/verification/hero-lanes/0007/runtime-inputs/"
             "reinhardt_collision_174.json").is_file(), "prepared barrier descriptor required",
    )
    def test_barrier_geometry_and_resource_lifetime_are_in_passive_stored_state(self):
        path = "D:/overwatch 1.7.4/verification/hero-lanes/0007/runtime-inputs/reinhardt_collision_174.json"
        with mock.patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": path}):
            session, _, players = self.scene()
        fight = players[0].match.combat
        self.assertIsNotNone(fight.barrier_geometry)
        shield = barriers.Barrier(1600.0, 1600.0, 10.0, 12.0, active=True)
        fight.barriers[players[0].body] = shield
        before = snapshot_digest(self.capture(session))
        shield.current -= 20
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        shield.current += 20
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        shield.broken_until = 17.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_export_never_evaluates_or_sends_and_keeps_rng_queue_and_allocation_order(self):
        session, _, players = self.scene()
        script = players[0].body_script
        component = script.component
        rng = component.rng.getstate()
        counter = component.seq.next_value
        instances = [(i.id, list(i.states), list(i.queue)) for i in component.instances.values()]
        with (
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no variable evaluation")),
            mock.patch.object(runtime.State, "payload", side_effect=AssertionError("no payload evaluation")),
            mock.patch.object(BodyScript, "snapshot", side_effect=AssertionError("no driver snapshot")),
            mock.patch.object(Mover, "snapshot", side_effect=AssertionError("no mover simulation snapshot")),
            mock.patch.object(session.server.sock, "sendto", side_effect=AssertionError("no send")),
            mock.patch.object(runtime.Instance, "state", side_effect=AssertionError("no lazy allocation")),
        ):
            first = self.capture(session)
            self.assertEqual(snapshot_digest(first), snapshot_digest(self.capture(session)))
        self.assertEqual(component.rng.getstate(), rng)
        self.assertEqual(component.seq.next_value, counter)
        actual = [(i.id, list(i.states), list(i.queue)) for i in component.instances.values()]
        self.assertEqual(actual, instances)
        self.assertFalse(first["restorable"])

    def test_hidden_vm_counter_transport_and_physics_changes_change_digest(self):
        session, _, players = self.scene()
        player, client = players[0], players[0].client
        initial = snapshot_digest(self.capture(session))
        client.expected_in += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        client.expected_in -= 1
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns[player.body] = player.match.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns.pop(player.body)
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        next(player.body_script.component.seq)
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, initial)
        player.mover.corrected = 17
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

    def test_equal_world_content_with_different_match_mover_alias_is_distinguished(self):
        session, _, players = self.scene()
        player = players[0]
        first = snapshot_digest(self.capture(session))
        # Same static bytes but a different identity: next use(match.world) resets the body.
        player.mover.world = flat_world(0.0)
        self.assertIsNot(player.mover.world, player.match.collision_world)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_equal_data_clone_is_distinguished_from_the_canonical_lazy_table(self):
        session, _, players = self.scene()
        player = players[0]
        first = snapshot_digest(self.capture(session))
        original = player.mover.data
        self.assertIs(mover_module.loaded_mover_table()[player.hero.body], original)
        replacement = dataclasses.replace(original)
        player.mover.data = player.mover.values = replacement
        self.assertEqual(replacement, original)
        self.assertIsNot(replacement, original)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_cross_mover_mutable_physics_alias_is_rejected(self):
        session, _, players = self.scene(two=True)
        players[1].mover.position = players[0].mover.position
        with self.assertRaisesRegex(ServerSnapshotError, "shared across Mover"):
            self.capture(session)

    def test_queue_movement_callback_closure_and_stream_alias_are_stored_without_building(self):
        session, _, players = self.scene()
        player = players[0]
        player.client.queue_entities([EntityUpdate(player.body, movement=Movement((1, 2, 3)))])
        # A real source builder is stored, not executed at hash time.
        record = EntityUpdate(0xD0000010, OP_UPDATE, player.match._game_mode_create)
        player.client.queue_entities([record])
        with mock.patch("ow174.game.match.world.create", side_effect=AssertionError("no builder execution")):
            first = snapshot_digest(self.capture(session))
        function = Match._game_mode_create
        constants = tuple(75 if value == 114 else value for value in function.__code__.co_consts)
        changed = types.FunctionType(function.__code__.replace(co_consts=constants), function.__globals__)
        changed.__qualname__ = function.__qualname__
        record.build = types.MethodType(changed, player.match)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        base = Snapshot(100)
        player.body_script.stream.base = base
        player.body_script.stream.pending = {1: (base, set())}
        shared = snapshot_digest(self.capture(session))
        player.body_script.stream.pending = {1: (copy.deepcopy(base), set())}
        self.assertNotEqual(snapshot_digest(self.capture(session)), shared)

    def test_weak_generation_values_only_project_strong_domain_records(self):
        session, _, players = self.scene()
        client = players[0].client
        self.assertIs(type(client._entity_record_generations), weakref.WeakKeyDictionary)
        before = snapshot_digest(self.capture(session))
        outside = EntityUpdate(0xA0000120)
        client._entity_record_generations[outside] = 15
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        client.entities.append(outside)
        strong = snapshot_digest(self.capture(session))
        client._entity_record_generations[outside] = 16
        self.assertNotEqual(snapshot_digest(self.capture(session)), strong)

    def test_unknown_fields_and_callback_classes_are_rejected(self):
        session, _, players = self.scene()
        client = players[0].client
        client.new_unregistered_field = 7
        with self.assertRaises(ServerSnapshotError):
            self.capture(session)
        del client.new_unregistered_field
        client.entities.append(EntityUpdate(1))
        client.entities[-1].stream = object()
        with self.assertRaises(ServerSnapshotError):
            self.capture(session)


class StoredJournalTests(unittest.TestCase):
    @unittest.skipUnless(SIGMA_PROFILE.is_file(), "prepared Sigma C015 mesh required")
    def test_stored_replay_preserves_deployed_sigma_barrier_and_hammer_contact(self):
        self.enterContext(mock.patch.dict(os.environ, {"ORPHEUS_SIGMA_COLLISION": str(SIGMA_PROFILE)}))
        session, sent = self.make()
        roster = [(1, "Reinhardt", combat.REINHARDT, 0, False), (2, "Sigma", sigma_grasp.SIGMA, 1, False)]
        handoffs = session.create_match(MAP, roster)
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47300 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        rein, sigma = session.server.matches[0].players
        hero_ids = (combat.REINHARDT, sigma_grasp.SIGMA)
        for peer, player, hero in zip(peers, (rein, sigma), hero_ids, strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        session.tick(100.816)
        self.assertTrue(rein.spawned and sigma.spawned)
        fight = rein.match.combat
        peers[1].commands(sigma.body_script.frame + 1, 30, 0, yaw=-32768, now=100.816)
        session.tick(101.280, missed=28)
        peers[1].commands(sigma.body_script.frame + 1, 12, 2, yaw=-32768, now=101.280)
        session.tick(101.472, missed=11)
        peers[1].commands(sigma.body_script.frame + 1, 1, 0, yaw=-32768, now=101.472)
        actor = fight.sigma_barriers.actors[sigma.body]
        self.assertEqual((actor.mode, actor.health), (3, 700))
        peers[0].commands(rein.body_script.frame + 1, 43, 0, now=101.472)
        peers[0].commands(rein.body_script.frame + 1, 32, 1, now=101.472)
        self.assertEqual((actor.mode, actor.health), (3, 615))
        self.assertEqual(fight.vitals_of(fight._target_entity(sigma.body)).total, 400)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        restored = replay.server.matches[0].players[1]
        restored_fight = restored.match.combat
        restored_actor = restored_fight.sigma_barriers.actors[restored.body]
        self.assertEqual((restored_actor.mode, restored_actor.health), (3, 615))
        self.assertEqual(restored_actor.position, actor.position)
        self.assertEqual(restored_fight.vitals_of(restored_fight._target_entity(restored.body)).total, 400)

    def make(self):
        sent = []

        def send(data, address):
            sent.append((data, address))
            return len(data)

        session = RecordingServerSession(
            environment=FixedSource(), collision_provider=StaticCollisionProvider(flat_world(0.0)),
            sendto=send, verify_stored_state=True,
        )
        return session, sent

    def test_stored_profile_replays_creation_raw_spawn_and_vm_state(self):
        with mock.patch("socket.socket", side_effect=AssertionError("no sockets")):
            session, sent = self.make()
            (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
            peer = Peer(session, handoff, sent, ("127.0.0.1", 47000))
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
            acknowledge_ownership(session, (peer,))
            session.tick(100.8)
            player = session.server.matches[0].players[0]
            peer.message(21616, pick(player.entity, SOLDIER), now=100.8)
            document = session.finish()
            self.assertEqual(document["state_profile"], "registered-domain-v1")
            self.assertTrue(all(len(row["state_before"]) == len(row["state_after"]) == 64
                                for row in document["roots"]))
            replay = _ReplaySession(document, session.registry.resolve)
            result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        self.assertFalse(result["complete_battle_replay"])
        damaged = copy.deepcopy(document)
        damaged["roots"][-1]["state_after"] = "0" * 64
        with self.assertRaises(ServerJournalError):
            _ReplaySession(damaged, session.registry.resolve).run()

    def test_unrecorded_hidden_queue_change_cannot_be_sealed_even_without_wire_effect(self):
        session, _ = self.make()
        session.create_match(MAP, [])
        session.server._next_conn += 1
        with self.assertRaisesRegex(ServerJournalError, "unrecorded stored"):
            session.finish()

    def test_two_player_stored_graphs_match_through_damage_death_respawn_and_hero_change(self):
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "A", SOLDIER, 0, False), (2, "B", SOLDIER, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47100 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player in zip(peers, players, strict=True):
            peer.message(21616, pick(player.entity, SOLDIER), now=100.8)
        peers[0].commands(players[0].body_script.frame + 1, 60, 1)
        self.assertTrue(players[1].match.combat.is_dead(players[1]))
        dead_body = players[1].body
        session.tick(105.0, missed=200)
        self.assertNotEqual(players[1].body, dead_body)
        self.assertFalse(players[1].match.combat.is_dead(players[1]))
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000002})
        session.report_callbacks()
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].players
        self.assertEqual([p.body for p in copied], [p.body for p in players])
        self.assertEqual(copied[0].hero.guid, players[0].hero.guid)

    def test_stored_replay_preserves_actual_hammer_impulse_and_hit_deduplication(self):
        session, sent = self.make()
        arena = dataclasses.replace(
            MAP, spawns=(MAP.spawns[0], dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 3.0))),
        )
        roster = [(1, "Reinhardt", combat.REINHARDT, 0, False), (2, "Target", SOLDIER, 1, False)]
        handoffs = session.create_match(arena, roster)
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47200 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        rein, victim = session.server.matches[0].players
        peers[0].message(21616, pick(rein.entity, combat.REINHARDT), now=100.8)
        peers[1].message(21616, pick(victim.entity, SOLDIER), now=100.8)
        session.tick(100.816)
        self.assertTrue(rein.spawned and victim.spawned)
        fight = rein.match.combat
        self.assertIn(victim.body, fight.history)
        peers[0].commands(rein.body_script.frame + 1, 30, 0, now=100.816)
        peers[0].commands(rein.body_script.frame + 1, 32, 1, now=100.816)
        health = fight.vitals[victim.body]
        self.assertEqual(health.total, 115)
        self.assertGreater(victim.mover.velocity[0], 0)
        self.assertTrue(victim.mover.impulse_active)
        hit_records = fight.hammer_hits[rein.body].values()
        key = ("body", (victim.body, fight.replacement_ticks.get(victim.body)))
        self.assertTrue(any(key in hits for _, hits in hit_records))
        peers[1].commands(victim.body_script.frame + 1, 12, 0, now=100.816)
        peers[0].commands(rein.body_script.frame + 1, 12, 1, now=100.816)
        self.assertEqual(health.total, 115)
        self.assertGreater(victim.mover.position[0], 0)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        restored = replay.server.matches[0].players[1]
        restored_fight = restored.match.combat
        self.assertEqual(restored.mover.position, victim.mover.position)
        self.assertEqual(restored.mover.velocity, victim.mover.velocity)
        self.assertEqual(restored_fight.vitals_of(restored_fight._target_entity(restored.body)).total, 115)

    def test_unrecorded_change_between_roots_fails_before_running_the_next_tick(self):
        session, _ = self.make()
        session.create_match(MAP, [])
        session.server.tick += 1
        before = session.server.tick
        with self.assertRaisesRegex(ServerJournalError, "between controlled roots"):
            session.tick(100.8)
        self.assertEqual(session.server.tick, before)
        with self.assertRaises(ServerJournalError):
            session.finish()


class MoverTableTests(unittest.TestCase):
    def test_peek_is_cold_without_io_and_canonical_identity_is_cached(self):
        original = mover_module.loaded_mover_table()
        self.addCleanup(setattr, mover_module, "_mover_table", original)
        mover_module._movers.cache_clear()
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("peek must not read")):
            self.assertIsNone(mover_module.loaded_mover_table())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movers.json"
            path.write_text('{"0x04000000000003CF":{"run_forward":7.0}}', encoding="utf-8")
            with mock.patch.object(mover_module, "MOVERS_PATH", path):
                first = mover_module.mover_data(None)
                self.assertEqual(first.run_forward, 7.0)
                self.assertIs(mover_module.mover_data(0x04000000000003CF), first)
                self.assertIs(mover_module.loaded_mover_table()[0x04000000000003CF], first)
                path.write_text('{}', encoding="utf-8")
                self.assertIs(mover_module.mover_data(None), first)
                mover_module._movers.cache_clear()
                self.assertEqual(mover_module.mover_data(None).run_forward, 5.5)


if __name__ == "__main__":
    unittest.main()
