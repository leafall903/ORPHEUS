"""Stored domain hashes detect hidden causal state without executing the game."""

import copy
import dataclasses
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
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER, find_hero
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
        next(player.body_script.component.seq)
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, initial)
        player.mover.corrected = 17
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        before_control = snapshot_digest(self.capture(session))
        player.match.combat.stuns[player.body] = session.server.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), before_control)
        before_modifiers = snapshot_digest(self.capture(session))
        tick = session.server.tick
        player.match.combat.heal_modifiers[player.body] = {"anti": [(tick, tick + 250)]}
        self.assertNotEqual(snapshot_digest(self.capture(session)), before_modifiers)
        before_sleep = snapshot_digest(self.capture(session))
        player.match.combat.ana_sleeps[player.body] = (tick, tick + 313, False)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before_sleep)
        before_spatial = snapshot_digest(self.capture(session))
        player.body_script.world._spatial_query_context.namespace_present = False
        self.assertNotEqual(snapshot_digest(self.capture(session)), before_spatial)

    def test_active_authored_zarya_barrier_health_changes_stored_digest(self):
        session, _, players = self.scene()
        player = players[0]
        with mock.patch.object(player.match, "skin_of", return_value=player.skin):
            self.assertTrue(player.match.switch_hero(player, find_hero("zarya")))
        script = player.body_script
        for _ in range(30):
            script.command(script.frame + 1, 0)
        script.command(script.frame + 1, 8)
        barriers = player.match.combat.zarya_bubbles
        barriers.sync(player)
        barrier = barriers.for_target(player)
        self.assertIsNotNone(barrier)
        before = snapshot_digest(self.capture(session))
        barrier.health -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_zarya_beam_consumed_clock_changes_stored_digest(self):
        session, _, players = self.scene()
        player = players[0]
        with mock.patch.object(player.match, "skin_of", return_value=player.skin):
            self.assertTrue(player.match.switch_hero(player, find_hero("zarya")))
        fight = player.match.combat
        script = player.body_script
        for _ in range(30):
            script.command(script.frame + 1, 0)
        from ow174.game.commands import FIRE, Command

        for _ in range(10):
            command = Command(script.frame + 1, buttons=FIRE)
            script.command(command.frame, command.buttons)
            fight.command(player, command)
        activation, consumed = fight.beam_seen[player.body]
        before = snapshot_digest(self.capture(session))
        fight.beam_seen[player.body] = activation, consumed + script.component.frame_ms
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_frost_progress_and_freeze_deadline_change_stored_digest(self):
        from ow174.game.combat import Frost

        session, _, players = self.scene()
        player = players[0]
        now = session.server.tick * 16
        frost = Frost({0xA0000100: (.2, now, False)})
        before = snapshot_digest(self.capture(session))
        player.match.combat.frost[player.body] = frost
        slowed = snapshot_digest(self.capture(session))
        self.assertNotEqual(slowed, before)
        frost.frozen_until = now + 1300
        frozen = snapshot_digest(self.capture(session))
        self.assertNotEqual(frozen, slowed)
        frost.frozen_aim = 1024, 512
        self.assertNotEqual(snapshot_digest(self.capture(session)), frozen)

    def test_expired_input_lock_changes_stored_digest_without_live_control(self):
        from ow174.game.combat import FROST_SPRAY, InputLock

        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        tick = fight.tick
        before = snapshot_digest(self.capture(session))
        lock = InputLock(FROST_SPRAY, tick - 10, tick - 1, (0, 0))
        fight.input_locks[player.body] = [lock]
        saved = snapshot_digest(self.capture(session))
        self.assertNotEqual(saved, before)
        self.assertFalse(fight.is_stunned(player))
        lock.aim = 1024, 512
        self.assertNotEqual(snapshot_digest(self.capture(session)), saved)

    def test_graviton_field_clock_and_pull_change_stored_digest(self):
        from ow174.game.graviton import Field

        session, _, players = self.scene(two=True)
        caster, recipient = players[:2]
        host = caster.match.combat.zarya_gravitons
        before = snapshot_digest(self.capture(session))
        tick = session.server.tick
        field = Field(caster, caster.body, caster.team, (2., 1., 0.), tick, tick + 250, 6.)
        host.fields.append(field)
        deployed = snapshot_digest(self.capture(session))
        self.assertNotEqual(deployed, before)
        field.last_tick += 1
        advanced = snapshot_digest(self.capture(session))
        self.assertNotEqual(advanced, deployed)
        host.pulled.add(recipient.body)
        self.assertNotEqual(snapshot_digest(self.capture(session)), advanced)

    def test_temporary_shield_pool_and_decay_clock_change_stored_digest(self):
        from ow174.game.sigma_grasp import SHIELD_TAG, ShieldDecay

        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = fight.player_target(player)
        health = fight.vitals_of(target)
        initial = snapshot_digest(self.capture(session))
        health.grant_temporary_shields(SHIELD_TAG, 60, 400)
        granted = snapshot_digest(self.capture(session))
        self.assertNotEqual(initial, granted)
        fight.grasp_shields[player.body] = ShieldDecay(fight.now + 3, fight.now, 5)
        scheduled = snapshot_digest(self.capture(session))
        self.assertNotEqual(granted, scheduled)
        fight.grasp_shields[player.body].last += 0.016
        self.assertNotEqual(scheduled, snapshot_digest(self.capture(session)))

    def test_active_hook_target_and_pull_clock_change_stored_digest(self):
        from ow174.game.commands import Command
        from ow174.game.hook import ROADHOG

        session, peers, players = self.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, ROADHOG), now=100.8)
        match = caster.match
        fight = match.combat
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 10)
        target.mover.body = None
        peers[0].commands(caster.body_script.frame + 1, 1, 8)
        for _ in range(50):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            if fight.hooks.controlled(target.body):
                break
            caster.take_commands([Command(caster.body_script.frame + 1)])
        flight = fight.hooks.flights[caster.body]
        self.assertEqual(flight.target.entity, target.body)
        before = snapshot_digest(self.capture(session))
        flight.last_move_tick -= 1
        shifted = snapshot_digest(self.capture(session))
        flight.target = None
        self.assertNotEqual(before, shifted)
        self.assertNotEqual(shifted, snapshot_digest(self.capture(session)))

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
