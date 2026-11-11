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

from ow174.game import assassin_victim, combat, messages
from ow174.game import mover as mover_module
from ow174.game.bits import BitReader
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER
from ow174.game.link import KIND_FRAME, SYN, peek_connection
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
        player.match.combat.stuns[player.body] = player.match.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns.pop(player.body)
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        next(player.body_script.component.seq)
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, initial)
        player.mover.corrected = 17
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

    def test_shared_vm_definition_mutation_changes_the_next_capture(self):
        session, _, players = self.scene(two=True)
        left, right = (player.body_script.component for player in players)
        right_graphs = {instance.graph for instance in right.instances.values()}
        shared = next(instance.graph for instance in left.instances.values() if instance.graph in right_graphs)
        original = snapshot_digest(self.capture(session))
        self.assertEqual(session._state_digest(), original)
        self.assertEqual(session._state_digest(), original)
        shared.fields["snapshot_probe"] = [1]
        try:
            changed = session._state_digest()
            self.assertNotEqual(changed, original)
            self.assertEqual(changed, snapshot_digest(self.capture(session)))
            shared.fields["snapshot_probe"][0] = 2
            changed_again = session._state_digest()
            self.assertNotEqual(changed_again, changed)
            self.assertEqual(changed_again, snapshot_digest(self.capture(session)))
        finally:
            del shared.fields["snapshot_probe"]
        self.assertEqual(session._state_digest(), original)

    def test_equal_world_content_with_different_match_mover_alias_is_distinguished(self):
        session, _, players = self.scene()
        player = players[0]
        first = snapshot_digest(self.capture(session))
        # Same static bytes but a different identity: next use(match.world) resets the body.
        player.mover.world = flat_world(0.0)
        self.assertIsNot(player.mover.world, player.match.collision_world)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_genji_hit_ledgers_are_causal_snapshot_state(self):
        session, _, players = self.scene()
        fight = players[0].match.combat
        body = players[0].body
        first = snapshot_digest(self.capture(session))
        fight.genji_strikes[body] = (6, 1, (0.0, 1.0, 0.0), {0xC0001000})
        strike = snapshot_digest(self.capture(session))
        self.assertNotEqual(strike, first)
        fight.genji_blades[body] = {(10, 66): (1, {0xC0001001})}
        self.assertNotEqual(snapshot_digest(self.capture(session)), strike)
        fight.genji_strikes.clear()
        fight.genji_blades.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), first)
        players[0].genji_mobility.air_jump_used = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        players[0].genji_mobility.air_jump_used = False
        self.assertEqual(snapshot_digest(self.capture(session)), first)
        fight.genji_assists[0xC0001000] = {body: (players[0], 123)}
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        fight.genji_assists.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), first)

    def test_live_genji_projectile_extent_is_causal(self):
        session, peers, players = self.scene()
        player = players[0]
        self.assertTrue(session.apply("switch_hero", {"account": player.account_lo,
                                                      "hero": 0x02E0000000000029}))
        for tick in range(8):
            now = 100.96 + tick * 0.016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        peers[0].commands(player.body_script.frame + 1, 40, 2, now=101.2)
        rockets = player.match.combat.rockets
        self.assertEqual(len(rockets), 3)
        self.assertTrue(all(rocket.shooter is player for rocket in rockets))
        for rocket in rockets:
            self.assertAlmostEqual(rocket.direct.projectile_extent, .1)

        initial = snapshot_digest(self.capture(session))
        direct = rockets[0].direct
        rockets[0].direct = dataclasses.replace(direct, projectile_extent=0.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        rockets[0].direct = direct
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_live_piercing_spray_hit_ledger_is_causal(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, (genji, mei) = case.connected(session, sent, first_hero=0x02E0000000000029,
                                            second_hero=0x02E00000000000DD)
        fight = genji.match.combat
        peers[1].commands(mei.body_script.frame + 1, 1, 1, yaw=-32768, pitch=500, now=100.944)
        for tick in range(18):
            now = 100.96 + tick * combat.TICK
            session.tick(now)
            if fight.rockets and genji.body in fight.rockets[0].hit_entities:
                break
        rocket, = fight.rockets
        self.assertEqual(rocket.hit_entities, {genji.body})
        self.assertEqual(fight.vitals[genji.body].total, 197.25)
        self.assertIn(genji.body, fight.frost_hits)
        before = snapshot_digest(self.capture(session))
        rocket.hit_entities.clear()
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_live_sleep_dart_wake_floor_is_causal(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, (genji, ana) = case.connected(session, sent, first_hero=0x02E0000000000029,
                                            second_hero=0x02E000000000013B)
        fight = genji.match.combat
        for tick in range(70):
            now = 100.96 + tick * combat.TICK
            session.tick(now)
            if fight.is_asleep(genji):
                break
            peers[1].commands(ana.body_script.frame + 1, 1, 8 if tick == 0 else 0,
                              yaw=-32768, pitch=500, now=now)
        self.assertTrue(fight.is_asleep(genji))
        vitals = fight.vitals[genji.body]
        self.assertEqual(vitals.total, 195)
        self.assertEqual(vitals.sleep_wake_floor - fight.tick, combat.SLEEP_WAKE_FLOOR_TICKS)
        before = snapshot_digest(self.capture(session))
        vitals.sleep_wake_floor += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_temporary_pool_amount_and_decay_clock_are_causal(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        pool = vitals.add_temporary((22, 0, 1), 0x5847, 2, 52.2, player.match.tick)
        initial = snapshot_digest(self.capture(session))
        pool.now -= 1
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, initial)
        pool.last_tick += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

    def test_pin_source_and_victim_body_identity_are_causal(self):
        session, _, players = self.scene(two=True)
        fight = players[0].match.combat
        initial = snapshot_digest(self.capture(session))
        fight.pins[0xC0001001] = players[0].body
        pinned = snapshot_digest(self.capture(session))
        self.assertNotEqual(pinned, initial)
        fight.pins[0xC0001001] = players[1].body
        self.assertNotEqual(snapshot_digest(self.capture(session)), pinned)
        fight.pins.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        lease = assassin_victim.Lease(0xC0001001, 8, 16, 2, players[0].body,
                                      players[1].body_script.component, players[0].body_script.component, 20)
        fight.assassin_victims[lease.source] = lease
        victim_script = snapshot_digest(self.capture(session))
        self.assertNotEqual(victim_script, initial)
        lease.activation += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), victim_script)
        previous = lease.source_component
        lease.source_component = players[0].body_script.component
        self.assertNotEqual(snapshot_digest(self.capture(session)), victim_script)
        lease.source_component = previous
        fight.assassin_victims.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_live_pet_component_health_and_source_generation_are_causal(self):
        session, peers, players = self.scene()
        player = players[0]
        match = player.match
        session.apply("switch_hero", {"account": player.account_lo, "hero": 0x02E0000000000007})
        for tick in range(8):
            now = 100.96 + tick * 0.016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        peers[0].commands(player.body_script.frame + 1, 90, 0, now=101.2)
        peers[0].commands(player.body_script.frame + 1, 40, 2, now=101.2)
        self.assertEqual(len(match.combat.barriers), 1)
        barrier = next(iter(match.combat.barriers.values()))
        initial = snapshot_digest(self.capture(session))
        mesh, digest = barrier.mesh, barrier.geometry_digest
        barrier.mesh = (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        barrier.mesh = mesh
        barrier.geometry_digest = "changed geometry input"
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        barrier.geometry_digest = digest
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        barrier.vitals.now[0] -= 1
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, initial)
        barrier.activation += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

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

    def test_stored_journal_replays_in_flight_genji_fan(self):
        session, sent = self.make()
        genji = 0x02E0000000000029
        # At 10 m, the outer pellets clear the target and remain in flight after the centre hit.
        target_spawn = dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 10.0))
        fan_map = dataclasses.replace(MAP, spawns=(MAP.spawns[0], target_spawn))
        handoffs = session.create_match(fan_map, [(1, "A", genji, 0, False), (2, "B", SOLDIER, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47100 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        player, target = session.server.matches[0].players
        peers[0].message(21616, pick(player.entity, genji), now=100.8)
        peers[1].message(21616, pick(target.entity, SOLDIER), now=100.8)
        self.assertEqual(player.hero.guid, genji)
        fight = player.match.combat
        peers[0].commands(player.body_script.frame + 1, 16, 2, now=100.816)
        first_packet = len(sent)
        for tick in range(12):
            now = 100.832 + tick * 0.016
            session.tick(now)
            if tick == 0:
                self.assertEqual(len(fight.rockets), 3)
                self.assertTrue(all(rocket.flown > 0 for rocket in fight.rockets))
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(fight.vitals[target.body].total, 171)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 29)
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 2)

        hits = {}
        for data, address in sent[first_packet:]:
            if peek_connection(data) != peers[0].handoff.conn:
                continue
            self.assertEqual(address, peers[0].address)
            packet = peers[0].cipher.open(data)
            self.assertIsNotNone(packet)
            if packet.kind != KIND_FRAME:
                continue
            reader = BitReader(packet.payload)
            reader.var_a()
            reliable, _ = messages.read_channel(reader)
            hits.update((seq, value) for seq, msg_id, value in reliable if msg_id == combat.HIT)
        hit, = hits.values()
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": target.body}, "+0x4": 29, "+0x8": 0})

        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied_match = replay.server.matches[0]
        copied_player, copied_target = copied_match.players
        self.assertEqual(copied_match.combat.vitals[copied_target.body].total, 171)
        self.assertEqual(copied_player.body_script.component.entity_var(342).value(), 29)
        for fighter in (player, copied_player):
            weapon = next(i for i in fighter.body_script.component.instances.values() if i.graph.index == 0x0304)
            self.assertEqual(weapon.vars[53].value(), 27)
        copied = copied_match.combat.rockets
        self.assertEqual(len(copied), 2)
        for original, restored in zip(rockets, copied, strict=True):
            self.assertIs(restored.shooter, copied_player)
            self.assertEqual(restored.position, original.position)
            self.assertEqual(restored.direction, original.direction)
            self.assertEqual(restored.left, original.left)
            self.assertAlmostEqual(restored.direct.projectile_extent, .1)

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
