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

from ow174.game import mover as mover_module
from ow174.game import npc
from ow174.game import sigma_grasp
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import graph, runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "prepared Reinhardt collision required")
    def test_raised_reinhardt_barrier_pool_is_causal(self):
        session, _, players = self.scene()
        player, = players
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": find_hero("Reinhardt").guid}))
        for frame in range(32):
            session.tick(101.0 + frame * .016)
            player.take_commands([Command(session.server.tick, buttons=2, extra=0)])
        system = player.match.combat.barriers.reinhardt
        shield = system.actors[player.body]
        self.assertTrue(shield.active)
        self.assertEqual(shield.health, 1600)
        first = snapshot_digest(self.capture(session))
        shield.health -= 30
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared Enforcer controller bundle required")
    def test_enforcer_controller_gate_is_causal(self):
        from test_junkrat_npc_combat import clear_graph_cache

        with mock.patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])):
            clear_graph_cache()
            try:
                session, _, players = self.scene()
                player, = players
                catalog = npc.load_catalog(Path(os.environ["ORPHEUS_PVE_NPC_DATA"]) / "pve_npcs_174.json")
                definition = next(row for row in catalog.values() if row.name == "Enforcer")
                x, y, z = player.mover.position
                bot = npc.spawn(player.match, definition, (x, y, z + 6), team=1)
                controller = bot.body_script.component.world.enforcer_controller
                self.assertIsNotNone(controller)
                self.assertTrue(controller.host.entity_var(9134, create=False).value())
                first = snapshot_digest(self.capture(session))
                controller.host.set(9134, False)
                self.assertNotEqual(snapshot_digest(self.capture(session)), first)
            finally:
                clear_graph_cache()

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared NPC bundle required")
    def test_assassin_route_frontier_is_causal(self):
        from test_junkrat_npc_combat import clear_graph_cache

        from ow174.game import assassin, npc_navigation

        self.addCleanup(clear_graph_cache)
        with mock.patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])):
            clear_graph_cache()
            session, _, players = self.scene()
            player, = players
            match = player.match
            catalog = npc.load_catalog(Path(os.environ["ORPHEUS_PVE_NPC_DATA"]) / "pve_npcs_174.json")
            definition = next(row for row in catalog.values() if row.name == "Assassin")
            x, y, z = player.mover.position
            bot = npc.spawn(match, definition, (x, y, z + 5), team=1, auto_engage=True)
            target = next(row for row in match.combat._targets() if row.entity == player.body)
            world = match.collision_world
            search = npc_navigation.begin(bot.position, target, 3)
            bot.pursuit = npc_navigation.Pursuit(world, search)
            first = snapshot_digest(self.capture(session))
            data = mover_module.mover_data(bot.body)
            npc_navigation.advance(search, world, data, 3, 1, lambda *_: True)
            self.assertTrue(search.visited)
            after_route = snapshot_digest(self.capture(session))
            self.assertNotEqual(after_route, first)
            bot.lunge = assassin.Lunge(player.body, session.server.tick + 16, session.server.tick + 94)
            after_admission = snapshot_digest(self.capture(session))
            self.assertNotEqual(after_admission, after_route)
            bot.lunge.launched = True
            self.assertNotEqual(snapshot_digest(self.capture(session)), after_admission)

    def test_hosted_winston_barrier_pool_is_causal(self):
        session, peers, players = self.scene()
        player, = players
        peer, = peers
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": find_hero("Winston").guid}))
        for frame in range(17):
            session.tick(101.0 + frame * .016)
            peer.commands(session.server.tick, 1, 16 if frame == 0 else 0)
        actor, = player.match.combat.barriers.world.actors.values()
        self.assertEqual(actor.health, 700)
        first = snapshot_digest(self.capture(session))
        actor.health -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_zarya_barrier_pool_is_causal(self):
        session, peers, players = self.scene()
        player, = players
        peer, = peers
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": find_hero("Zarya").guid}))
        session.tick(101.0)
        peer.commands(session.server.tick, 1, 8)
        bubble = player.match.combat.barriers.sync(player)
        self.assertIsNotNone(bubble)
        first = snapshot_digest(self.capture(session))
        bubble.health -= 30
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_temporary_shield_pool_and_decay_are_causal(self):
        session, _, players = self.scene()
        player, = players
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        vitals.grant_temporary_shields(sigma_grasp.SHIELD_TAG, 72, 400)
        decay = sigma_grasp.ShieldDecay(103, 100, 5)
        fight.grasp_shields[player.body] = decay
        first = snapshot_digest(self.capture(session))
        decay.starts += .016
        second = snapshot_digest(self.capture(session))
        self.assertNotEqual(first, second)
        vitals.decay_temporary_shields(sigma_grasp.SHIELD_TAG, 1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), second)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_live_junkrat_actors_and_timers_are_explicit_causal_state(self):
        session, peers, players = self.scene()
        player, = players
        peer, = peers
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": find_hero("Junkrat").guid}))

        def emit(buttons, missed=0):
            session.tick(101.0, missed=missed)
            peer.commands(session.server.tick, 1, buttons)

        emit(8)
        emit(0, missed=29)
        system = player.match.combat.junkrat
        mine, = system.mines.values()
        emit(0x10)
        emit(0, missed=29)
        trap, = system.traps.values()
        player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
        emit(0x20)
        tire, = system.tire.actors.values()
        first = snapshot_digest(self.capture(session))
        mine.deadline += .016
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        mine.deadline -= .016
        trap.release_flag_seen = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        trap.release_flag_seen = False
        self.assertEqual(snapshot_digest(self.capture(session)), first)
        trap.duration += .016
        second = snapshot_digest(self.capture(session))
        self.assertNotEqual(second, first)
        tire.arm_tick += 1
        third = snapshot_digest(self.capture(session))
        self.assertNotEqual(third, second)
        tire.controls = dataclasses.replace(tire.controls, yaw=8192)
        fourth = snapshot_digest(self.capture(session))
        self.assertNotEqual(fourth, third)
        tire.jump_pending = True
        fifth = snapshot_digest(self.capture(session))
        self.assertNotEqual(fifth, fourth)
        if tire.climb is not None:
            tire.climb_ticks += 1
            tire.climbing = True
            self.assertNotEqual(snapshot_digest(self.capture(session)), fifth)

    def scene(self, two=False):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, two=two)
        return session, peers, players

    @staticmethod
    def capture(session):
        return export_server_state(session.server, archives=session._matches.values(),
                                   collision_provider=session._collision_reads, assets=session.registry)

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
        first = players[0].body_script.frame + 1
        due = first + 59 - session.server.tick
        if due > 0:
            session.tick(101.0, missed=due - 1)  # the firing window is due at receipt
        peers[0].commands(first, 60, 1)
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
