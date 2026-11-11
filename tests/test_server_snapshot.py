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

    @staticmethod
    def capture(session):
        return export_server_state(session.server, archives=session._matches.values(),
                                   collision_provider=session._collision_reads, assets=session.registry)

    def test_pending_authoritative_self_healing_changes_stored_digest(self):
        session, _, players = self.scene()
        script = players[0].body_script
        before = snapshot_digest(self.capture(session))
        script.self_healing = 0.32
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        script.self_healing = 0.0
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_world_expiry_retained_flight_is_stored_and_retires_with_the_body(self):
        from ow174.game import combat
        from ow174.game.commands import Command
        from ow174.game.script import regeneration

        session, _, players = self.scene()
        mercy = players[0]
        session.apply("switch_hero", {"account": 1, "hero": regeneration.MERCY})
        script = mercy.body_script
        for frame in range(script.frame + 1, script.frame + 36):
            mercy.take_commands([Command(frame)])
        script.set_ult_charge(1820)
        mercy.take_commands([Command(script.frame + 1, buttons=0x20)])
        self.assertEqual(mercy.mover.flight_scale, 1.0)
        world_frame = script.frame + 1000
        mercy.match.update(world_frame * combat.TICK, world_frame)
        self.assertIsNone(mercy.mover.flight_scale)
        retained = mercy.valkyrie_pose
        self.assertGreater(retained.through, script.frame)
        original = snapshot_digest(self.capture(session))
        mercy.valkyrie_pose = dataclasses.replace(retained, through=retained.through - 1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), original)
        mercy.valkyrie_pose = dataclasses.replace(retained, expires_ms=retained.expires_ms - 1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), original)
        mercy.valkyrie_pose = retained
        self.assertEqual(snapshot_digest(self.capture(session)), original)
        session.apply("switch_hero", {"account": 1, "hero": SOLDIER})
        self.assertFalse(hasattr(mercy, "valkyrie_pose"))

    def test_pending_mercy_projectile_keeps_damage_charge_origin_in_stored_digest(self):
        from ow174.game import combat
        from ow174.game.commands import FIRE, Command
        from ow174.game.script import charge, regeneration

        session, _, players = self.scene()
        mercy = players[0]
        session.apply("switch_hero", {"account": 1, "hero": regeneration.MERCY})
        first = mercy.body_script.frame
        for offset in range(1, 73):
            mercy.take_commands([Command(first + offset, yaw=16384,
                action=2 if offset == 36 else 0, buttons=FIRE if offset == 72 else 0)])
        rocket, = mercy.match.combat.rockets
        original = rocket.damage_charge
        self.assertEqual(original[0], mercy.body)
        self.assertEqual(len(original[1]), 5)
        with (
            mock.patch.object(combat.Combat, "_fly", side_effect=AssertionError("no projectile advance")),
            mock.patch.object(charge, "damaged", side_effect=AssertionError("no damage award")),
        ):
            before = snapshot_digest(self.capture(session))
            rocket.damage_charge = (original[0], original[1][:-1])
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            rocket.damage_charge = original
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_pending_boosted_rocket_keeps_exact_retired_caster_in_stored_digest(self):
        from test_game_combat import aim
        from test_game_mercy_boost import EFFECT_BUNDLE
        from test_game_mercy_npc import clear_graphs

        from ow174.game import combat
        from ow174.game.commands import SECONDARY, Command
        from ow174.game.script import graph, regeneration

        if not EFFECT_BUNDLE.is_file():
            self.skipTest("external extracted effect pack required")
        self.enterContext(mock.patch.object(graph, "GRAPHS_PATH", EFFECT_BUNDLE))
        clear_graphs()
        self.addCleanup(clear_graphs)
        session, _, players = self.scene(two=True)
        caster, shooter = players
        session.apply("switch_hero", {"account": 1, "hero": regeneration.MERCY})
        shooter.team = caster.team
        script = caster.body_script
        fight = caster.match.combat
        x, y, z = shooter.mover.position
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        first = script.frame
        for offset in range(1, 11):
            caster.take_commands([Command(first + offset, yaw=yaw, pitch=pitch, buttons=SECONDARY)])
        self.assertAlmostEqual(fight.damage_scale(shooter), 1.3)
        shooter.take_commands([Command(shooter.body_script.frame + 1, yaw=16384, buttons=SECONDARY)])
        rocket, = fight.rockets
        credit = rocket.boost_credit
        self.assertIs(credit[0], script)
        session.apply("switch_hero", {"account": 1, "hero": regeneration.MERCY})
        self.assertTrue(script.retired)
        self.assertIn(rocket, fight.rockets)
        with mock.patch.object(combat.Combat, "_fly", side_effect=AssertionError("no projectile advance")):
            before = snapshot_digest(self.capture(session))
            rocket.boost_credit = None
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            rocket.boost_credit = credit
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_pending_secondary_attack_is_stored_and_retires_with_its_shooter(self):
        from ow174.game import combat
        from ow174.game.commands import FIRE, Command
        from ow174.game.script import regeneration

        session, _, players = self.scene(two=True)
        mercy, shooter = players
        session.apply("switch_hero", {"account": 1, "hero": regeneration.MERCY})
        shooter.team = mercy.team
        script = mercy.body_script
        for frame in range(script.frame + 1, script.frame + 36):
            mercy.take_commands([Command(frame)])
        script.set_ult_charge(1820)
        mercy.take_commands([Command(script.frame + 1, buttons=0x20)])
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        start = ultimate.state(2).start // script.frame_ms
        world_frame = start + 958
        mercy.match.update(world_frame * combat.TICK, world_frame)
        shooter.take_commands([Command(start + 927, buttons=FIRE)])
        pending = mercy.match.combat.pending_attacks
        attack, = pending
        self.assertEqual(attack.waiting, (script,))
        self.assertIs(attack.source, shooter.body_script)
        with mock.patch.object(combat.Combat, "_commit_attack", side_effect=AssertionError("no attack")):
            before = snapshot_digest(self.capture(session))
            pending[0] = dataclasses.replace(attack, deadline=attack.deadline - 1)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            pending[0] = attack
            self.assertEqual(snapshot_digest(self.capture(session)), before)
        session.apply("switch_hero", {"account": 2, "hero": SOLDIER})
        self.assertFalse(mercy.match.combat.pending_attacks)

    def test_actual_support_effect_vm_is_stored_without_running_its_callbacks(self):
        import test_game_mercy_boost as boost_tests
        from test_game_combat import aim
        from test_game_mercy_npc import clear_graphs

        from ow174.game.commands import FIRE, Command
        from ow174.game.script import graph, support_effects

        if not boost_tests.EFFECT_BUNDLE.is_file():
            self.skipTest("explicit extracted effect pack required")
        self.enterContext(mock.patch.object(graph, "GRAPHS_PATH", boost_tests.EFFECT_BUNDLE))
        clear_graphs()
        self.addCleanup(clear_graphs)
        session, _, players = self.scene(two=True)
        healer, target = players
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000004})
        target.team = healer.team
        fight = healer.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        vitals = fight.vitals_of(body)
        vitals.damage(100)
        fight._sync_body_health(target, vitals)
        first = healer.body_script.frame
        for offset in range(1, 11):
            yaw, pitch = aim(fight._eye(healer), (body.feet[0], body.feet[1] + 1, body.feet[2]))
            healer.take_commands([Command(first + offset, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertTrue(fight.support_effects.attachments)
        item = next(iter(fight.support_effects.attachments.values()))
        with (
            mock.patch.object(support_effects.Effects, "advance",
                              side_effect=AssertionError("no effect tick")),
            mock.patch.object(support_effects.EffectWorld, "native", side_effect=AssertionError("no query")),
        ):
            before = snapshot_digest(self.capture(session))
            item.last_frame += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            item.last_frame -= 1
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_actual_healing_status_vm_is_stored_without_running_its_callbacks(self):
        from test_game_mercy_healing_status import STATUS_BUNDLE
        from test_game_mercy_npc import clear_graphs

        from ow174.game.script import graph, healing_status

        if not STATUS_BUNDLE.is_file():
            self.skipTest("explicit extracted status pack required")
        self.enterContext(mock.patch.object(graph, "GRAPHS_PATH", STATUS_BUNDLE))
        clear_graphs()
        self.addCleanup(clear_graphs)
        session, _, players = self.scene(two=True)
        ana, target = players
        session.apply("switch_hero", {"account": 1, "hero": 0x02E000000000013B})
        target.team = 1 - ana.team
        fight = ana.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        fight.healing_status.impact(ana, body)
        self.assertTrue(fight.healing_status.applied)
        item = next(iter(fight.healing_status.applied.values()))
        with (
            mock.patch.object(healing_status.Statuses, "update",
                              side_effect=AssertionError("no status tick")),
            mock.patch.object(healing_status.StatusWorld, "native", side_effect=AssertionError("no query")),
        ):
            before = snapshot_digest(self.capture(session))
            item.damage_scale = 1.3
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            item.damage_scale = 1.0
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_mercy_owned_scope_host_is_stored_without_dispatching_callbacks(self):
        session, _, players = self.scene()
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000004})
        host = players[0].body_script.world.scope_host
        with mock.patch.object(type(host), "drain", side_effect=AssertionError("no scope dispatch")):
            first = snapshot_digest(self.capture(session))
            host.frame += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), first)
            host.frame -= 1
            self.assertEqual(snapshot_digest(self.capture(session)), first)

    def test_resurrection_immunity_and_control_deadlines_are_stored_causal_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(target)
        first = snapshot_digest(self.capture(session))
        vitals.invulnerable_until = 200.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        vitals.invulnerable_until = 0.0
        vitals.control_locked_until = 100.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        vitals.control_locked_until = 0.0
        self.assertEqual(snapshot_digest(self.capture(session)), first)

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
