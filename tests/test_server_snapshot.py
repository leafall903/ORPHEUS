"""Stored domain hashes detect hidden causal state without executing the game."""

import copy
import dataclasses
import json
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

from ow174.game import earthshatter, reinhardt_charge
from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.combat import HitDef
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
    def test_active_wave_and_charge_change_stored_combat_digest(self):
        session, _, (player, target) = self.scene(two=True)
        fight = player.match.combat
        state = next(
            state for instance in player.body_script.component.instances.values()
            for state in instance.states.values()
        )
        wave = earthshatter.Wave(
            shooter=player, state=state, origin=(0., 1., 0.), centre=(0., 1., 2.),
            yaw=0., speed=30., reach=20., cone=60., height=1., radius=1., stun_seconds=2.5,
            normal=HitDef(50), close=HitDef(120), started=fight.tick, fronts=[(0., 1., 0.)],
        )
        charge = reinhardt_charge.Charge(player=target, state=state, deadline=fight.tick + 50)
        before = snapshot_digest(self.capture(session))
        fight.shatters[player.body] = wave
        fight.charges[target.body] = charge
        active = snapshot_digest(self.capture(session))
        self.assertNotEqual(active, before)
        wave.hit.add((target.body, fight.replacement_ticks.get(target.body)))
        hit = snapshot_digest(self.capture(session))
        self.assertNotEqual(hit, active)
        charge.pinned = player.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), hit)

    def test_baptiste_deployable_effect_and_health_inputs_change_digest(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        actor = fight.create_deployable(
            player, 0x0400000000002B50, "Amplification Matrix", (0.0, 1.0, 4.0),
            health=0, radius=0, height=0, seconds=10,
        )
        fight.baptiste.matrices[actor.entity] = actor
        created = snapshot_digest(self.capture(session))
        self.assertNotEqual(created, before)
        actor.rotation = (0.0, 1.0, 0.0, 0.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), created)
        changed = snapshot_digest(self.capture(session))
        player.body_script.component.maximum_health += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        fight.stuns[player.body] = fight.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        fight.vitals[player.body].ult_healing_deficit += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        from ow174.game.combat import HitDef, Rocket

        rocket = Rocket(player, player.team, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 60, 60, None, None)
        fight.rockets.append(rocket)
        changed = snapshot_digest(self.capture(session))
        rocket.source_entity ^= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        rocket.definition = 0x04000000000029AD
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        rocket.deflections = 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        rocket.range_left = 490.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        from ow174.game.deflection import ProjectileTags

        changed = snapshot_digest(self.capture(session))
        fight.projectile_tags = ProjectileTags(json.dumps({
            "version": 1, "build": "1.74.0.0.104319", "entities": {"0x04000000000029AD": []},
        }).encode())
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        player.body_script.passive_charge_ms += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        from ow174.game.healing import Modifier

        fight.healing.anti[player.body] = Modifier(player.body, fight.tick, fight.tick + 250)
        changed = snapshot_digest(self.capture(session))
        fight.healing.anti[player.body].end += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        for mutate in (
            lambda: setattr(fight.absorption, 'last_tick', fight.tick + 100),
            lambda: fight.absorption.converted.update({player.body: (12, 3)}),
            lambda: fight.vitals[player.body].add_temporary((12, 6, 1), 0x5847, 43.2),
            lambda: setattr(rocket, 'direct', HitDef(24)),
            lambda: setattr(rocket.direct, 'health_operation', None),
        ):
            changed = snapshot_digest(self.capture(session))
            mutate()
            self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

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

    def test_stored_actual_baptiste_primary_replays_active_sigma_absorption(self):
        from ow174.game import absorption, deflection

        if not deflection.catalog().admitted(0x0400000000002CFA):
            raise RuntimeError('requires explicit003 interception metadata')
        bap, sigma = 0x02E0000000000221, absorption.SIGMA
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, 'Bap', bap, 0, False), (2, 'Sigma', sigma, 1, False)])
        peers = [Peer(session, handoff, sent, ('127.0.0.1', 47400 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, hero in zip(peers, players, (bap, sigma), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        peers[1].commands(players[1].body_script.frame + 1, 5, 8, yaw=-32768)
        session.tick(100.816)
        peers[0].commands(players[0].body_script.frame + 1, 1, 1, now=100.816)
        session.tick(100.832)
        fight = players[0].match.combat
        root = next(i for i in players[1].body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(root.state(1).active)
        self.assertEqual(root.vars[21312].value(), 24)
        self.assertEqual(fight.vitals[players[1].body].total, 400)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()['stored_state_verified'])
        copied = next(i for i in replay.server.matches[0].players[1].body_script.component.instances.values()
                      if i.graph.index == 0x1D02)
        self.assertTrue(copied.state(1).active)
        self.assertEqual(copied.vars[21312].value(), 24)
        self.assertEqual(replay.server.matches[0].combat.absorption.last_tick, fight.absorption.last_tick)

    def test_stored_actual_baptiste_grenade_replays_deflected_flight_authority(self):
        from ow174.game import deflection

        if not deflection.catalog().admitted(0x04000000000029AD):
            raise RuntimeError("requires explicit003 deflection metadata")
        bap, genji = 0x02E0000000000221, deflection.GENJI
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "Bap", bap, 0, False), (2, "Genji", genji, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47300 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, hero in zip(peers, players, (bap, genji), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        peers[1].commands(players[1].body_script.frame + 1, 5, 16, yaw=-32768)
        peers[0].commands(players[0].body_script.frame + 1, 1, 2)
        for now, missed in ((100.816, 0), (100.880, 3)):
            session.tick(now, missed=missed)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = players[0].match.combat
        rocket, = fight.rockets
        self.assertEqual(rocket.deflections, 1)
        self.assertIs(rocket.shooter, players[1])
        self.assertEqual((rocket.team, rocket.source_entity, rocket.definition),
                         (1, players[1].body, 0x04000000000029AD))
        self.assertIsNone(rocket.shot)
        self.assertGreater(rocket.left, 59.0)
        self.assertGreater(rocket.range_left, 0.0)
        self.assertLess(rocket.range_left, 500.0)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied, = replay.server.matches[0].combat.rockets
        self.assertEqual((copied.deflections, copied.team, copied.source_entity, copied.definition,
                          copied.position, copied.direction, copied.left, copied.flown, copied.range_left),
                         (rocket.deflections, rocket.team, rocket.source_entity, rocket.definition,
                          rocket.position, rocket.direction, rocket.left, rocket.flown, rocket.range_left))

    def test_stored_actual_ana_grenade_replays_active_received_healing_state(self):
        ana, bap = 0x02E000000000013B, 0x02E0000000000221
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "Ana", ana, 0, False), (2, "Bap", bap, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47200 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, hero in zip(peers, players, (ana, bap), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        peers[0].commands(players[0].body_script.frame + 1, 15, 0x10)
        # Input roots emit the grenade; scheduler roots advance its world flight.
        for now, missed in ((100.816, 0), (101.024, 12)):
            session.tick(now, missed=missed)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = players[0].match.combat
        self.assertIsNotNone(fight.healing.spec)
        self.assertIn(players[1].body, fight.healing.anti)
        self.assertEqual(fight.vitals[players[1].body].total, 140)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].combat
        self.assertEqual(copied.healing.spec, fight.healing.spec)
        self.assertEqual(copied.healing.anti, fight.healing.anti)
        self.assertEqual(copied.healing.amp, fight.healing.amp)

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

    def test_stored_real_projectile_preserves_emission_body_identity(self):
        session, sent = self.make()
        (handoff,) = session.create_match(MAP, [(1, "Projectile", SOLDIER, 0, False)])
        peer = Peer(session, handoff, sent, ("127.0.0.1", 47002))
        peer.send(flags=SYN)
        peer.send()
        peer.message(21601, {})
        acknowledge_ownership(session, (peer,))
        session.tick(100.8)
        player = session.server.matches[0].players[0]
        peer.message(21616, pick(player.entity, SOLDIER), now=100.8)
        peer.commands(player.body_script.frame + 1, 1, 2)
        rocket, = player.match.combat.rockets
        self.assertEqual(rocket.source_entity, player.body)
        self.assertEqual(rocket.shot.body, player.body)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].combat.rockets
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].source_entity, rocket.source_entity)
        self.assertEqual(copied[0].shot.body, rocket.shot.body)

    def test_stored_baptiste_passive_charge_keeps_fractional_host_phase(self):
        hero = 0x02E0000000000221
        session, sent = self.make()
        (handoff,) = session.create_match(MAP, [(1, "Baptiste", hero, 0, False)])
        peer = Peer(session, handoff, sent, ("127.0.0.1", 47003))
        peer.send(flags=SYN)
        peer.send()
        peer.message(21601, {})
        acknowledge_ownership(session, (peer,))
        session.tick(100.8)
        player = session.server.matches[0].players[0]
        peer.message(21616, pick(player.entity, hero), now=100.8)
        script = player.body_script
        self.assertIsNone(script.ult_seconds)
        self.assertEqual(script.passive_charge_ms, 0)
        peer.commands(script.frame + 1, 62, 0)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.assertEqual(script.passive_charge_ms, 992)
        peer.commands(script.frame + 1, 1, 0)
        self.assertEqual(script.component.vars[342].value(), 5)
        self.assertEqual(script.passive_charge_ms, 8)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].players[0].body_script
        self.assertIsNone(copied.ult_seconds)
        self.assertEqual(copied.passive_charge_ms, 8)
        self.assertEqual(copied.component.vars[342].value(), 5)

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
