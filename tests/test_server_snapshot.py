"""Stored domain hashes detect hidden causal state without executing the game."""

import copy
import dataclasses
import json
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
from test_game_combat import aim
from test_server_journal import MAP, FixedSource, Peer, acknowledge_ownership, pick

from ow174.game import (
    combat, hanzo_control, matrix, orisa_barrier, sigma_barrier, sigma_grasp, winston_barrier, zarya_barrier,
)
from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def scene(self, two=False):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, two=two)
        return session, peers, players

    def test_orisa_throw_and_landed_shield_change_snapshot_without_running_vm(self):
        session, peers, players = self.scene(two=True)
        owner = players[1]
        service = owner.match.combat.orisa_barriers
        if service.geometry is None:
            self.skipTest("prepared build 104319 Orisa collision mesh required")
        peers[1].message(21616, pick(owner.entity, 0x02E000000000013E), now=100.8)
        peers[1].commands(owner.body_script.frame + 1, 100, 16)
        flight, = service.flights.values()
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            before = snapshot_digest(self.capture(session))
            script = owner.body_script
            anchor_tick, anchor_frame = script.orisa_pose_anchor
            script.orisa_pose_anchor = (anchor_tick + 1, anchor_frame)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            script.orisa_pose_anchor = (anchor_tick, anchor_frame)
            script.orisa_fortify_pending = True
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            script.orisa_fortify_pending = False
            flight.remaining -= 0.016
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            flight.remaining += 0.016

        session.tick(101.9, missed=65)
        shield, = service.actors.values()
        self.assertEqual(shield.health, 600)
        self.assertFalse(service.flights)
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            before = snapshot_digest(self.capture(session))
            shield.health -= 20
            damaged = snapshot_digest(self.capture(session))
            self.assertNotEqual(damaged, before)
            shield.component.frame += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

    def test_winston_pending_deployment_and_sphere_are_passive_causal_state(self):
        session, peers, players = self.scene(two=True)
        owner = players[1]
        peers[1].message(21616, pick(owner.entity, winston_barrier.HERO), now=100.8)
        peers[1].commands(owner.body_script.frame + 1, 1, 16)
        service = owner.match.combat.winston_barriers
        anchor = service.pending[owner.body]
        self.assertFalse(service.actors)
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            pending = snapshot_digest(self.capture(session))
            service.pending[owner.body] = (anchor[0] + 1, anchor[1])
            self.assertNotEqual(snapshot_digest(self.capture(session)), pending)
            service.pending[owner.body] = anchor
            self.assertEqual(snapshot_digest(self.capture(session)), pending)
        session.tick(101.9, missed=65)
        self.assertFalse(service.pending)
        sphere = service.actors[owner.body]
        self.assertEqual(sphere.health, 700)
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            deployed = snapshot_digest(self.capture(session))
            self.assertNotEqual(deployed, pending)
            sphere.health -= 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), deployed)
            sphere.health += 1
            sphere.expires += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), deployed)

    def test_sigma_experimental_barrier_snapshot_includes_actor_and_child_request(self):
        session, peers, players = self.scene(two=True)
        owner = players[1]
        service = owner.match.combat.sigma_barriers
        if service.geometry is None:
            self.skipTest("manifest-verified Sigma C015 model required")
        peers[1].message(21616, pick(owner.entity, sigma_barrier.HERO), now=100.8)
        actor, = service.actors.values()
        self.assertTrue(actor.child_requested)
        peers[1].commands(owner.body_script.frame + 1, 20, 0)
        peers[1].commands(owner.body_script.frame + 1, 16, 2)
        session.tick(101.9, missed=65)
        self.assertEqual(actor.phase, 2)
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            before = snapshot_digest(self.capture(session))
            position = actor.position
            actor.position = (position[0] + 1, *position[1:])
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            actor.position = position
            actor.child_requested = False
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            actor.child_requested = True
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            actor.vitals.damage(1)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_projected_zarya_shield_pool_recipient_and_idle_clock_are_causal(self):
        session, peers, players = self.scene(two=True)
        recipient, caster = players
        recipient.team = caster.team
        peers[1].message(21616, pick(caster.entity, zarya_barrier.HERO), now=100.8)
        fight = caster.match.combat
        x, y, z = recipient.mover.position
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        peers[1].commands(caster.body_script.frame + 1, 1, 16, yaw=yaw, pitch=pitch)
        service = fight.zarya_barriers
        stamp, health, body = service.projected[caster.body]
        self.assertEqual((health, body), (200, recipient.body))
        anchor = service.frame_anchors[caster.body]
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            before = snapshot_digest(self.capture(session))
            service.projected[caster.body] = (stamp, health - 1, body)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            service.projected[caster.body] = (stamp, health, caster.body)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            service.projected[caster.body] = (stamp, health, body)
            service.frame_anchors[caster.body] = (anchor[0] + 1, anchor[1])
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            service.frame_anchors[caster.body] = anchor
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    @staticmethod
    def capture(session):
        return export_server_state(session.server, archives=session._matches.values(),
                                   collision_provider=session._collision_reads, assets=session.registry)

    def test_explicit_ring_geometry_flag_is_passive_causal_state(self):
        session, _, players = self.scene()
        fight = players[0].match.combat
        before = snapshot_digest(self.capture(session))
        fight.configure_volley_geometry(reconstructed_patterns=True)
        with mock.patch.object(type(fight), '_shoot', side_effect=AssertionError('snapshot must not shoot')):
            enabled = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, enabled)
        fight.configure_volley_geometry(reconstructed_patterns=False)
        self.assertEqual(before, snapshot_digest(self.capture(session)))

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

    def test_stored_replay_reproduces_hanzo_arrow_absorption_and_sigma_conversion(self):
        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            self.skipTest("prepared build 104319 projectile tags required")
        admitted = matrix.load_admitted_definitions(Path(tags))
        hanzo = 0x02E0000000000005
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "Hanzo", hanzo, 0, False),
                                              (2, "Sigma", sigma_grasp.SIGMA, 1, False)])
        match = session.server.matches[0]
        session.apply("configure_projectile_absorption", {
            "match_id": match.id, "definitions": tuple(sorted(admitted)),
        })
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47200 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        shooter, defender = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, sigma_grasp.SIGMA), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        fight = match.combat
        first = shooter.body_script.frame + 1
        peers[0].commands(first, 70, 1)
        peers[1].commands(defender.body_script.frame + 1, 65, 0, yaw=-32768)
        peers[1].commands(defender.body_script.frame + 1, 1, 8, yaw=-32768)
        peers[1].commands(defender.body_script.frame + 1, 5, 0, yaw=-32768)
        instance = next(i for i in defender.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRAPH)
        self.assertTrue(instance.states[1].active)
        peers[0].commands(first + 70, 1, 0)
        session.tick(102.05, missed=77)
        session.tick(102.066)
        self.assertEqual(instance.find_var(0, 21312).value(), 125)
        self.assertFalse(fight.rockets)
        peers[1].commands(defender.body_script.frame + 1, 130, 0, yaw=-32768, now=102.066)
        self.assertFalse(instance.states[1].active)
        self.assertEqual(fight.vitals[defender.body].bonus_shields, 75)

        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0]
        self.assertEqual(copied.combat.vitals[copied.players[1].body].bonus_shields, 75)
        self.assertFalse(copied.combat.rockets)

    def test_stored_replay_reproduces_overlapping_zarya_shields_and_hanzo_hit(self):
        hanzo = 0x02E0000000000005
        arena = dataclasses.replace(
            MAP, team_sizes=(1, 2), free_for_all=False,
            spawns=(dataclasses.replace(MAP.spawns[0], position=(0.0, 0.0, -3.0)),
                    dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 1.0)), MAP.spawns[1]),
        )
        session, sent = self.make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Soldier", SOLDIER, 1, False),
                                                (3, "Zarya", zarya_barrier.HERO, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47400 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        match = session.server.matches[0]
        shooter, recipient, caster = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, SOLDIER, zarya_barrier.HERO), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        fight = match.combat
        service = fight.zarya_barriers
        x, y, z = recipient.mover.position
        point = (x, y + 1, z)
        yaw, pitch = aim(fight._eye(shooter), point)
        cast_yaw, cast_pitch = aim(fight._eye(caster), point)
        peers[0].commands(shooter.body_script.frame + 1, 70, 1, yaw=yaw, pitch=pitch)
        peers[2].commands(caster.body_script.frame + 1, 1, 8, yaw=cast_yaw, pitch=cast_pitch)
        self.assertIsNotNone(service.phase(caster))
        session.tick(100.816)
        peers[2].commands(caster.body_script.frame + 1, 1, 16,
                          yaw=cast_yaw, pitch=cast_pitch, now=100.816)
        self.assertEqual(service.projected[caster.body][1:], (200, recipient.body))
        session.tick(102.256, missed=89)
        self.assertIsNotNone(service.phase(caster))
        self.assertEqual(service.projected[caster.body][1:], (200, recipient.body))
        peers[0].commands(shooter.body_script.frame + 1, 1, 0, yaw=yaw, pitch=pitch, now=102.256)
        self.assertTrue(fight.rockets)
        for tick in range(1, 8):
            session.tick(102.256 + 0.016 * tick)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(service.projected[caster.body][1:], (75, recipient.body))
        self.assertEqual(fight.vitals[recipient.body].total, 200)
        session.tick(103.04, missed=39)
        self.assertIsNone(service.phase(caster))
        self.assertEqual(service.projected[caster.body][1:], (75, recipient.body))
        session.tick(104.48, missed=89)
        self.assertIsNone(service.projected_phase(caster))
        self.assertNotIn(caster.body, service.projected)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        _, copied_recipient, copied_caster = copied.players
        self.assertIsNone(copied.combat.zarya_barriers.phase(copied_caster))
        self.assertNotIn(copied_caster.body, copied.combat.zarya_barriers.projected)
        self.assertEqual(copied.combat.vitals[copied_recipient.body].total, 200)

    def test_stored_replay_reproduces_hanzo_bow_hitting_deployed_orisa_shield(self):
        if orisa_barrier.GEOMETRY_SHA256 is None:
            self.skipTest("prepared build 104319 Orisa collision mesh required")
        hanzo, orisa = 0x02E0000000000005, 0x02E000000000013E
        arena = dataclasses.replace(MAP, spawns=(
            dataclasses.replace(MAP.spawns[0], position=(0.0, 0.0, -3.0)), MAP.spawns[1],
        ))
        session, sent = self.make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Orisa", orisa, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47300 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        match = session.server.matches[0]
        shooter, defender = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, orisa), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        fight = match.combat
        service = fight.orisa_barriers
        peers[1].commands(defender.body_script.frame + 1, 1, 16, yaw=-32768, pitch=8192)
        self.assertTrue(service.pending(defender))
        self.assertFalse(service.flights)
        self.assertFalse(service.actors)
        session.tick(101.9, missed=65)
        shield = service.actors[defender.body]
        self.assertEqual(shield.health, 600)
        self.assertFalse(service.flights)
        self.assertNotIn(defender.body, fight.vitals)
        yaw, pitch = aim(fight._eye(shooter), (0, 0.8, 5))
        first = shooter.body_script.frame + 1
        peers[0].commands(first, 70, 1, yaw=yaw, pitch=pitch, now=101.9)
        peers[0].commands(first + 70, 1, 0, yaw=yaw, pitch=pitch, now=101.9)
        self.assertTrue(fight.rockets, (first, shooter.body_script.frame))
        session.tick(103.2, missed=80)
        for tick in range(1, 7):
            session.tick(103.2 + 0.016 * tick)
            if not fight.rockets:
                break
        self.assertEqual(shield.health, 475, (shield.position, tuple(shooter.mover.position),
                                             tuple(defender.mover.position), yaw, pitch))
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[defender.body].total, 450)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        copied_defender = copied.players[1].body
        self.assertEqual(copied.combat.orisa_barriers.actors[copied_defender].health, 475)
        self.assertEqual(copied.combat.vitals[copied_defender].total, 450)

    def test_stored_replay_reproduces_configured_sonic_attachment(self):
        self._configured_sonic(stored=True)

    def test_wire_replay_reproduces_configured_sonic_expiry(self):
        self._configured_sonic(stored=False)

    def _configured_sonic(self, *, stored):
        sonic_path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
        if not sonic_path:
            self.skipTest("explicitly extracted Sonic profile required")
        hanzo = 0x02E0000000000005
        arena = dataclasses.replace(MAP, free_for_all=False, spawns=(
            MAP.spawns[0], dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 6.0)),
        ))
        session, sent = self.make() if stored else journal_tests.ServerJournalTests().make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Soldier", SOLDIER, 1, False)])
        match = session.server.matches[0]
        session.apply("configure_hanzo_profiles", {
            "match_id": match.id, "sonic": json.loads(Path(sonic_path).read_text(encoding="utf-8")), "dragon": None,
        })
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47410 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        archer, target = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, SOLDIER), strict=True):
            peer.message(21616, pick(player.entity, hero), now=now)
        fight = match.combat
        yaw, pitch = aim(fight._eye(archer), fight._eye(target))
        for frames, buttons in ((1, 8), (1, 0), (70, 1), (1, 0)):
            peers[0].commands(archer.body_script.frame + 1, frames, buttons, yaw=yaw, pitch=pitch, now=now)
        arrow, = fight.rockets
        self.assertIs(arrow.sonar, fight.sonic_profile)
        now += 78 * combat.TICK
        session.tick(now, missed=77)
        for _ in range(12):
            if fight.sonars:
                break
            now += combat.TICK
            session.tick(now)

        def check_attached(active_match):
            source, victim = active_match.players
            combat_state = active_match.combat
            beacon, = combat_state.sonars
            self.assertEqual((beacon.source_entity, beacon.attached), (source.body, victim.body))
            self.assertTrue(combat_state.revealed_to(source, victim))
            self.assertEqual(combat_state.vitals[victim.body].total, 75)
            self.assertFalse(combat_state.rockets)

        def check_expired(active_match):
            source, victim = active_match.players
            combat_state = active_match.combat
            self.assertFalse(combat_state.sonars)
            self.assertFalse(combat_state.revealed_to(source, victim))
            self.assertEqual(combat_state.vitals[victim.body].total, 75)

        check_attached(match)
        if not stored:
            beacon, = fight.sonars
            reveal = beacon.statuses[target.body]
            for _ in range(24):
                if not fight.sonars:
                    break
                now += 180 * combat.TICK
                session.tick(now, missed=179)
                for peer in peers:
                    peer.commands(match.tick, 1, 0, now=now)
            check_expired(match)
            self.assertTrue(reveal.stopped)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertEqual(replay.run()["stored_state_verified"], stored)
        check = check_attached if stored else check_expired
        check(replay.server.matches[0])
        self.assertEqual(replay.server.matches[0].combat.sonic_profile, fight.sonic_profile)

    def test_wire_replay_reproduces_earned_configured_dragonstrike(self):
        self._earned_configured_dragonstrike(stored=False)

    def test_stored_replay_reproduces_earned_configured_dragonstrike(self):
        self._earned_configured_dragonstrike(stored=True)

    def _earned_configured_dragonstrike(self, *, stored):
        dragon_path = os.environ.get("ORPHEUS_HANZO_PROFILE")
        if not dragon_path:
            self.skipTest("explicitly extracted Dragonstrike profile required")
        hanzo, roadhog = 0x02E0000000000005, 0x02E0000000000040
        arena = dataclasses.replace(MAP, free_for_all=False, spawns=(
            MAP.spawns[0], dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 6.0)),
        ))
        session, sent = self.make() if stored else journal_tests.ServerJournalTests().make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Roadhog", roadhog, 1, False)])
        match = session.server.matches[0]
        session.apply("configure_hanzo_profiles", {
            "match_id": match.id, "sonic": None, "dragon": json.loads(Path(dragon_path).read_text(encoding="utf-8")),
        })
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47420 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        archer, target = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, roadhog), strict=True):
            peer.message(21616, pick(player.entity, hero), now=now)
        fight = match.combat
        charge = archer.body_script.component.entity_var(342)

        def command(player_index, frames, buttons=0, *, yaw=0, pitch=0, forward=0):
            player = match.players[player_index]
            peers[player_index].commands(
                player.body_script.frame + 1, frames, buttons, yaw=yaw, pitch=pitch, forward=forward, now=now,
            )

        def advance(frames):
            nonlocal now
            now += frames * combat.TICK
            session.tick(now, missed=frames - 1)
            for peer in peers:
                peer.commands(match.tick, 1, 0, now=now)

        def wait_for_living_target():
            for _ in range(8):
                if not fight.is_dead(target):
                    return
                advance(125)
            self.fail("Roadhog did not respawn in the recorded match")

        self.assertEqual(charge.value(), 0)
        for _ in range(22):
            if charge.value() == 1680:
                break
            wait_for_living_target()
            angles = aim(fight._eye(archer), fight._eye(target))
            before = charge.value()
            command(0, 70, 1, yaw=angles[0], pitch=angles[1])
            command(0, 1, yaw=angles[0], pitch=angles[1])
            advance(78)
            self.assertGreater(charge.value(), before)
        self.assertEqual(charge.value(), 1680)
        wait_for_living_target()
        command(1, 156, forward=127)
        advance(160)
        self.assertGreater(target.mover.position[2] - archer.mover.position[2], 15)
        target_pool = fight.vitals_of(next(item for item in fight._targets() if item.owner is target))
        before_dragon = target_pool.total
        angles = aim(fight._eye(archer), fight._eye(target))
        command(0, 1, 32, yaw=angles[0], pitch=angles[1])
        self.assertLess(charge.value(), 1)
        for _ in range(6):
            command(0, 16, yaw=angles[0], pitch=angles[1])
            if fight.rockets:
                break
        initial, = fight.rockets
        self.assertIs(initial.continuation, fight.dragon_profile)
        self.assertEqual(initial.source_entity, archer.body)
        self.assertEqual(target_pool.total, before_dragon)
        for _ in range(12):
            advance(16)
            if fight.dragons and target_pool.total < before_dragon:
                break

        def check_moving(active_match, expected_health):
            source, victim = active_match.players
            combat_state = active_match.combat
            flight, = combat_state.dragons
            self.assertEqual(flight.source_entity, source.body)
            self.assertEqual(flight.profile, combat_state.dragon_profile)
            self.assertEqual(combat_state.vitals[victim.body].total, expected_health)
            self.assertFalse(combat_state.rockets)

        self.assertLess(target_pool.total, before_dragon)
        self.assertFalse(initial.hit_entities)
        check_moving(match, target_pool.total)
        for _ in range(24):
            if not fight.dragons:
                break
            advance(180)

        def check_finished(active_match):
            source = active_match.players[0]
            combat_state = active_match.combat
            self.assertEqual(combat_state.dragon_profile, fight.dragon_profile)
            self.assertFalse(combat_state.dragons)
            self.assertFalse(combat_state.rockets)
            self.assertFalse(hanzo_control.casting(source.body_script))

        check_finished(match)
        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertEqual(replay.run()["stored_state_verified"], stored)
        check_finished(replay.server.matches[0])

    def test_stored_replay_reproduces_flux_lifting_and_slamming_hanzo(self):
        self.check_flux_replay()

    def test_encrypted_hero_switch_during_flux_replays_without_old_body_slam(self):
        self.check_flux_replay(replace_target=True)

    def check_flux_replay(self, *, replace_target=False):
        hanzo, sigma = 0x02E0000000000005, sigma_barrier.HERO
        session, sent = (journal_tests.ServerJournalTests().make(flat_world(0.0))
                         if replace_target else self.make())
        handoffs = session.create_match(dataclasses.replace(MAP, free_for_all=False), [
            (1, "Hanzo", hanzo, 0, False), (2, "Sigma", sigma, 1, False),
        ])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47370 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        match = session.server.matches[0]
        target, caster = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, sigma), strict=True):
            peer.message(21616, pick(player.entity, hero), now=now)

        def advance(frames):
            nonlocal now
            now += frames * combat.TICK
            session.tick(now, missed=frames - 1)

        for _ in range(15):
            advance(251)
            peers[1].commands(caster.body_script.frame + 251, 1, 0, yaw=-32768, now=now)
            peers[0].commands(match.tick, 1, 0, now=now)
        script = caster.body_script
        self.assertEqual(script.component.entity_var(342).value(), 1960)
        self.assertIsNotNone(target.body_script)
        fight = match.combat
        flux = next(i for i in script.component.instances.values() if i.graph.index == 0x1E41)
        point = tuple(target.mover.position)
        yaw, pitch = aim(fight._eye(caster), point)
        peers[1].commands(script.frame + 1, 1, 32, yaw=yaw, pitch=pitch, now=now)
        peers[1].commands(script.frame + 1, 79, 0, yaw=yaw, pitch=pitch, now=now)
        advance(80)
        peers[0].commands(match.tick, 1, 0, now=now)
        yaw, pitch = aim(fight._eye(caster), point)
        peers[1].commands(script.frame + 1, 1, 1, yaw=yaw, pitch=pitch, now=now)
        self.assertEqual(flux.vars[59].value(), 2)
        self.assertLess(sum((a - b) ** 2 for a, b in zip(flux.vars[2478].value(), point, strict=True)), 0.001)
        self.assertLess(script.component.entity_var(342).value(), 2)

        for _ in range(8):
            advance(30)
            peers[1].commands(script.frame + 1, 30, 0, now=now)
            peers[0].commands(target.body_script.frame + 1, 30, 0, now=now)
            if target.mover.position[1] > 3:
                break
        self.assertEqual(fight.vitals[target.body].total, 150)
        self.assertGreater(target.mover.position[1], 3)
        self.assertTrue(target.mover.airborne)
        effect, = [i for i in target.body_script.component.instances.values() if i.graph.index == 0x1DBC]
        self.assertEqual(effect.source_entity(), caster.body)
        if replace_target:
            old_body, old_script = target.body, target.body_script
            peers[0].message(21616, pick(target.entity, SOLDIER), now=now)
            self.assertNotEqual(target.body, old_body)
            self.assertTrue(old_script.retired)
            for _ in range(10):
                advance(30)
                peers[1].commands(script.frame + 1, 30, 0, now=now)
                peers[0].commands(target.body_script.frame + 1, 30, 0, now=now)
            self.assert_replayed_flux_replacement(session, match, old_body, target.body)
            return
        for _ in range(12):
            advance(30)
            peers[1].commands(script.frame + 1, 30, 0, now=now)
            peers[0].commands(target.body_script.frame + 1, 30, 0, now=now)
            if fight.vitals[target.body].total == 50:
                break
        self.assertEqual(fight.vitals[target.body].total, 50)
        self.assertFalse(target.mover.airborne)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        copied_target = copied.players[0]
        self.assertEqual(copied.combat.vitals[copied_target.body].total, 50)
        self.assertFalse(copied_target.mover.airborne)

    def assert_replayed_flux_replacement(self, session, match, old_body, replacement):
        document = session.finish()
        self.assertEqual(document["state_profile"], "wire-only")
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertEqual(replay.run()["status"], "matched_controlled_roots_and_wire")
        for arena in (match, replay.server.matches[0]):
            player = arena.players[0]
            fight = arena.combat
            self.assertEqual((player.body, player.hero.guid), (replacement, SOLDIER))
            self.assertFalse(player.mover.airborne)
            self.assertNotIn(old_body, fight.vitals)
            self.assertFalse(any(i.graph.index == 0x1DBC for i in player.body_script.component.instances.values()))
            target = next(t for t in fight._targets() if t.owner is player)
            self.assertEqual(fight.vitals_of(target).total, 200)

    def test_stored_replay_reproduces_accretion_interrupting_hanzo_bow(self):
        hanzo, sigma = 0x02E0000000000005, sigma_barrier.HERO
        arena = dataclasses.replace(MAP, free_for_all=False, spawns=(
            MAP.spawns[0], dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 6.0)),
        ))
        session, sent = self.make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Sigma", sigma, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47360 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        match = session.server.matches[0]
        target, caster = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, sigma), strict=True):
            peer.message(21616, pick(player.entity, hero), now=now)
        fight = match.combat
        yaw, pitch = aim(fight._eye(caster), fight._eye(target))
        peers[0].commands(target.body_script.frame + 1, 70, 1, now=now)
        peers[1].commands(caster.body_script.frame + 1, 20, 0, yaw=yaw, pitch=pitch, now=now)
        peers[1].commands(caster.body_script.frame + 1, 1, 16, yaw=yaw, pitch=pitch, now=now)
        peers[1].commands(caster.body_script.frame + 1, 49, 0, yaw=yaw, pitch=pitch, now=now)
        rock, = fight.rockets
        self.assertIsNotNone(rock.accretion_instance)
        now += 70 * combat.TICK
        session.tick(now, missed=69)
        for _ in range(20):
            if not fight.rockets:
                break
            now += combat.TICK
            session.tick(now)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[target.body].total, 130)
        deadline = fight.stuns[target.body]
        self.assertEqual(deadline - fight.tick, 50)
        self.assertTrue(target.mover.external_impulse)
        self.assertLess(target.mover.velocity[2], -4.5)
        peers[0].commands(target.body_script.frame + 1, 1, 0, now=now)
        self.assertFalse(fight.rockets)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        copied_target = copied.players[0]
        self.assertEqual(copied.combat.vitals[copied_target.body].total, 130)
        self.assertEqual(copied.combat.stuns[copied_target.body], deadline)
        self.assertTrue(copied_target.mover.external_impulse)
        self.assertFalse(copied.combat.rockets)

    def test_stored_replay_reproduces_sigma_experimental_barrier_intercepting_hanzo_bow(self):
        if sigma_barrier.configured_geometry() is None:
            self.skipTest("manifest-verified Sigma C015 model required")
        hanzo, sigma = 0x02E0000000000005, sigma_barrier.HERO
        arena = dataclasses.replace(MAP, spawns=(
            dataclasses.replace(MAP.spawns[0], position=(0.0, 0.0, -8.0)), MAP.spawns[1],
        ))
        session, sent = self.make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Sigma", sigma, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47340 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        match = session.server.matches[0]
        shooter, defender = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, sigma), strict=True):
            peer.message(21616, pick(player.entity, hero), now=now)
        fight = match.combat
        actor, = fight.sigma_barriers.actors.values()
        self.assertTrue(actor.child_requested)
        peers[1].commands(defender.body_script.frame + 1, 20, 0, yaw=-32768, now=now)
        peers[1].commands(defender.body_script.frame + 1, 16, 2, yaw=-32768, now=now)
        now += 36 * combat.TICK
        session.tick(now, missed=35)
        self.assertEqual(actor.phase, 2)
        now += 16 * combat.TICK
        session.tick(now, missed=15)
        peers[1].commands(defender.body_script.frame + 1, 3, 0, yaw=-32768, now=now)
        now += 3 * combat.TICK
        session.tick(now, missed=2)
        self.assertEqual(actor.phase, 3)

        yaw, pitch = aim(fight._eye(shooter), fight._eye(defender))
        first = shooter.body_script.frame + 1
        peers[0].commands(first, 70, 1, yaw=yaw, pitch=pitch, now=now)
        peers[0].commands(first + 70, 1, 0, yaw=yaw, pitch=pitch, now=now)
        self.assertEqual(len(fight.rockets), 1)
        now += 81 * combat.TICK
        session.tick(now, missed=80)
        for tick in range(1, 12):
            if not fight.rockets:
                break
            session.tick(now + combat.TICK * tick)
        self.assertFalse(fight.rockets)
        self.assertEqual(actor.vitals.total, 575)
        self.assertEqual(fight.vitals[defender.body].total, 400)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        copied_actor, = copied.combat.sigma_barriers.actors.values()
        self.assertEqual(copied_actor.vitals.total, 575)
        self.assertEqual(copied.combat.vitals[copied.players[1].body].total, 400)

    def test_stored_replay_reproduces_unposed_winston_shield_intercepting_hanzo_bow(self):
        hanzo, winston = 0x02E0000000000005, winston_barrier.HERO
        arena = dataclasses.replace(MAP, spawns=(
            dataclasses.replace(MAP.spawns[0], position=(0.0, 0.0, -8.0)), MAP.spawns[1],
        ))
        session, sent = self.make()
        handoffs = session.create_match(arena, [(1, "Hanzo", hanzo, 0, False),
                                                (2, "Winston", winston, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47310 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        match = session.server.matches[0]
        shooter, defender = match.players
        for peer, player, hero in zip(peers, match.players, (hanzo, winston), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        fight = match.combat
        service = fight.winston_barriers
        peers[1].commands(defender.body_script.frame + 1, 1, 16)
        self.assertIn(defender.body, service.pending)
        self.assertFalse(service.actors)
        session.tick(101.0, missed=10)
        bubble = service.actors[defender.body]
        self.assertFalse(service.pending)
        self.assertEqual(bubble.health, 700)
        yaw, pitch = aim(fight._eye(shooter), (0, 1, 5))
        first = shooter.body_script.frame + 1
        peers[0].commands(first, 70, 1, yaw=yaw, pitch=pitch, now=101.0)
        peers[0].commands(first + 70, 1, 0, yaw=yaw, pitch=pitch, now=101.0)
        self.assertEqual(len(fight.rockets), 1)
        session.tick(102.3, missed=80)
        for tick in range(1, 12):
            if not fight.rockets:
                break
            session.tick(102.3 + combat.TICK * tick)
        self.assertFalse(fight.rockets)
        self.assertEqual(bubble.health, 575)
        self.assertEqual(fight.vitals[defender.body].total, 500)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        body = copied.players[1].body
        self.assertFalse(copied.combat.winston_barriers.pending)
        self.assertEqual(copied.combat.winston_barriers.actors[body].health, 575)
        self.assertEqual(copied.combat.vitals[body].total, 500)

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
