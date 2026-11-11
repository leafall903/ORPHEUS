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
    def test_actual_chain_hook_flight_is_causal_stored_state(self):
        from ow174.game.commands import Command

        session, _, players = self.scene()
        hog = players[0]
        session.apply('switch_hero', {'account': hog.account_lo, 'hero': 0x02E0000000000040})
        match = hog.match
        for index in range(37):
            frame = match.tick + 1
            match.update(frame * 0.016, frame)
            hog.take_commands([Command(frame, yaw=0, pitch=0, buttons=0x08 if index == 25 else 0, extra=0)])
        hook = match.combat.chain_hooks[hog.body]
        self.assertGreater(hook.remaining, 0)
        before = snapshot_digest(self.capture(session))
        hook.remaining -= 1
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_actual_scrap_ball_nested_payload_is_causal_stored_state(self):
        from ow174.game.commands import SECONDARY, Command

        session, _, players = self.scene()
        hog = players[0]
        session.apply('switch_hero', {'account': hog.account_lo, 'hero': 0x02E0000000000040})
        match = hog.match
        for _ in range(25):
            frame = match.tick + 1
            match.update(frame * 0.016, frame)
            hog.take_commands([Command(frame, yaw=0, pitch=0, buttons=0, extra=0)])
        frame = match.tick + 1
        match.update(frame * 0.016, frame)
        hog.take_commands([Command(frame, yaw=0, pitch=0, buttons=SECONDARY, extra=0)])
        ball, = match.combat.rockets
        self.assertEqual(ball.definition, 0x0400000000000295)
        self.assertIsNotNone(ball.split)
        before = snapshot_digest(self.capture(session))
        ball.split = dataclasses.replace(ball.split, speed=ball.split.speed + 1)
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_matrix_catalog_is_cold_read_only_and_loaded_contents_are_causal(self):
        from ow174.game import matrix

        session, _, _ = self.scene()
        with mock.patch.object(matrix, '_tag_table', None):
            with mock.patch.object(matrix, 'read_tags',
                                   side_effect=AssertionError('export must not load tags')):
                cold = snapshot_digest(self.capture(session))
            matrix._tag_table = {0x0400000000000236: frozenset()}
            loaded = snapshot_digest(self.capture(session))
            self.assertNotEqual(cold, loaded)
            matrix._tag_table[0x0400000000000236] = frozenset({0x0D60000000000145})
            self.assertNotEqual(loaded, snapshot_digest(self.capture(session)))

    def test_direct_body_owner_context_and_ruleset_target_cache_are_causal(self):
        from ow174.game.dva import HERO

        session, _, players = self.scene()
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': HERO})
        registry = player.body_script.world.dva_queries
        before = snapshot_digest(self.capture(session))
        registry.owner_context.environment_present = False
        owner_changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, owner_changed)
        instance = next(iter(player.body_script.component.instances.values()))
        instance.ruleset_target_entity = player.entity
        self.assertNotEqual(owner_changed, snapshot_digest(self.capture(session)))

    @unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME'), 'requires actual mech metadata')
    def test_timed_actor_clock_anchor_is_causal_stored_state(self):
        from ow174.game import dva, npc

        session, _, players = self.scene()
        match = players[0].match
        actor = npc.spawn(match, npc.Definition(dva.HERO, dva.MECH, 'Timed mech', 300, 300),
                          players[0].mover.position, team=1, manual=False, script_clock=(7000, 7))
        before = snapshot_digest(self.capture(session))
        actor.spawner['dva_clock']['world_ms'] += 1
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_dva_registry_context_and_callbacks_are_causal_stored_state(self):
        from ow174.game.dva import HERO

        session, _, players = self.scene()
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': HERO})
        context = player.body_script.world.dva_queries.context
        before = snapshot_digest(self.capture(session))
        context.environment_present = False
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_manual_pin_registry_is_causal_stored_state(self):
        session, _, players = self.scene()
        fight = players[0].match.combat
        before = snapshot_digest(self.capture(session))
        fight.pins[0xC0000FFF] = players[0].body
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_expired_control_interval_is_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        fight.control_history[player.body] = [(fight.tick - 2, fight.tick)]
        self.assertFalse(fight.is_stunned(player))
        self.assertTrue(fight.is_stunned(player, tick=fight.tick - 1))
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_thawed_view_history_is_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        sample = (1.0, (fight.tick - 2) * 16, fight.tick * 16, 8192, 1024)
        fight.frost.freeze_history[player.body] = [sample]
        self.assertIsNone(fight.frost.sample(player, fight.tick))
        self.assertEqual(fight.frost.sample(player, fight.tick - 1), sample)
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_healing_modifier_source_activation_and_expiry_are_causal_state(self):
        from ow174.game.healing import Modifier

        session, _, players = self.scene()
        player = players[0]
        service = player.match.combat.healing
        previous = snapshot_digest(self.capture(session))
        effect = Modifier(player.body, 100, 350)
        service.anti[player.body] = effect
        for field, value in (('source', player.body+1), ('start', 101), ('end', 351)):
            current = snapshot_digest(self.capture(session))
            self.assertNotEqual(previous, current)
            previous = current
            setattr(effect, field, value)
        current = snapshot_digest(self.capture(session))
        self.assertNotEqual(previous, current)
        service.amp[player.body] = Modifier(player.body, 104, 350)
        self.assertNotEqual(current, snapshot_digest(self.capture(session)))

    @unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA'), 'requires external verified barrier mesh')
    def test_barrier_geometry_hp_and_recovery_clock_are_causal_state(self):
        from ow174.game.barriers import Barrier

        session, _, players = self.scene()
        player = players[0]
        service = player.match.combat.barriers
        initial = snapshot_digest(self.capture(session))
        service.triangles[0][0][0] += .01  # private scene copy, never the external input
        geometry_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(initial, geometry_digest)
        barrier = Barrier(player, player.body, 1, 1600, 1600, 0, 0, 0)
        service.owners[player.body] = barrier
        owner_digest = snapshot_digest(self.capture(session))
        barrier.hp = 1552
        hp_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(owner_digest, hp_digest)
        barrier.blocked_until = 5.0
        self.assertNotEqual(hp_digest, snapshot_digest(self.capture(session)))

    def test_projected_bubble_hp_and_expiry_are_causal_state(self):
        from ow174.game.barriers import PROJECTED, Bubble

        session, _, players = self.scene()
        player = players[0]
        service = player.match.combat.barriers
        bubble = Bubble(player, player.body, PROJECTED, 1, 16, 2.0, 200)
        service.bubbles[player.body, PROJECTED] = bubble
        before = snapshot_digest(self.capture(session))
        bubble.hp -= 48
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, damaged)
        bubble.expires += 0.5
        self.assertNotEqual(damaged, snapshot_digest(self.capture(session)))

    def test_extra_shields_conversion_counter_and_decay_clock_are_causal_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        before = snapshot_digest(self.capture(session))
        vitals.bonus_shields = vitals.bonus_shields_max = 12.0
        shield_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, shield_digest)
        fight.sigma.conversions[(player.body, 1)] = 2
        counter_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(shield_digest, counter_digest)
        fight.sigma.last_tick = fight.tick + 1
        self.assertNotEqual(counter_digest, snapshot_digest(self.capture(session)))

    def test_zenyatta_orb_recovery_and_los_history_are_causal_state(self):
        from ow174.game.zenyatta import HERO, Orb

        session, _, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": HERO})
        service = player.match.combat.zenyatta
        owner = service.owner(player)
        initial = snapshot_digest(self.capture(session))
        owner.harmony = Orb(player.body, 1.0)
        orb_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(orb_digest, initial)
        owner.ready['harmony'] = 2.0
        ready_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(ready_digest, orb_digest)
        player.body_script.world.zenyatta_charge_ammo = 1
        charge_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(charge_digest, ready_digest)
        player.body_script.emp_suppressed_buttons = 0x20
        self.assertNotEqual(snapshot_digest(self.capture(session)), charge_digest)
        del player.body_script.emp_suppressed_buttons
        self.assertEqual(snapshot_digest(self.capture(session)), charge_digest)

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

    def test_finite_zen_orb_initial_catalog_and_stored_state_replay_match(self):
        from ow174.game import matrix

        self.assertIsNotNone(matrix.loaded_tags())  # bound before the first controlled root
        session, sent = self.make()
        peers, players = journal_tests.ServerJournalTests().connected(session, sent, two=False)
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': 0x02E0000000000020})
        peers[0].commands(player.body_script.frame+1, 1, 1)
        self.assertTrue(any(r.definition == 0x0400000000000236 for r in player.match.combat.rockets))
        session.tick(101.2)
        document = session.finish()
        self.assertEqual(document['state_profile'], 'registered-domain-v1')
        result = _ReplaySession(document, session.registry.resolve).run()
        self.assertTrue(result['stored_state_verified'])
        self.assertFalse(result['complete_battle_replay'])

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
