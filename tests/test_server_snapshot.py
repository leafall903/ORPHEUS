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
from test_game_combat import aim
from test_server_journal import MAP, FixedSource, Peer, acknowledge_ownership, pick

from ow174.game import combat, cryo, npc, symmetra
from ow174.game import mover as mover_module
from ow174.game.collision import MovementOverlay, StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import nodes, runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.symmetra_barriers import Barrier
from ow174.game.symmetra_portals import PortalPair
from ow174.game.symmetra_turrets import Sentry
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
                                   collision_provider=session._collision_reads,
                                   assets=getattr(session, "registry", None))

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

    def test_sentry_timing_aura_identity_and_shield_timing_are_stored_read_only(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        bot = npc.spawn(player.match, npc.Definition(player.hero.guid, player.hero.body, "Fixture", 200),
                        (0, 0, 0), team=1)
        component = player.body_script.component
        instance = next(iter(component.instances.values()))
        sentry = Sentry(player, bot, (0, 0, 1), 20, 1,
                        attached_at=0.5, target=player.body, slow=(component, instance))
        fight.sentries.sentries.append(sentry)
        bot.combat_owner = player
        with (
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no variable evaluation")),
            mock.patch.object(runtime.Instance, "state", side_effect=AssertionError("no lazy allocation")),
        ):
            before = snapshot_digest(self.capture(session))
            sentry.last_tick += 1
            after = snapshot_digest(self.capture(session))
            self.assertNotEqual(after, before)
            sentry.slow = None
            cleared = snapshot_digest(self.capture(session))
            self.assertNotEqual(cleared, after)
            sentry.support_pillar = 0xC0000001
            attached = snapshot_digest(self.capture(session))
            self.assertNotEqual(attached, cleared)
            vitals = fight.vitals[bot.entity]
            vitals.last_damage_at = 17.0
            self.assertNotEqual(snapshot_digest(self.capture(session)), attached)

    def test_barrier_lifetime_plane_and_deployment_deduplication_are_causal_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        bot = npc.spawn(player.match, npc.Definition(player.hero.guid, player.hero.body, 'Fixture', 4000),
                        (0, 0, 3), team=1)
        wall = Barrier(player, bot, (0, 0, 1), 12, (player.body, 7, 1))
        fight.barriers.walls.append(wall)
        fight.barriers.seen.add(wall.key)
        with (
            mock.patch.object(runtime.Var, 'value', side_effect=AssertionError('no variable evaluation')),
            mock.patch.object(runtime.Instance, 'state', side_effect=AssertionError('no lazy allocation')),
            mock.patch.object(type(fight.barriers), 'update',
                              side_effect=AssertionError('no actor callback')),
        ):
            before = snapshot_digest(self.capture(session))
            wall.expires_at += .016
            after = snapshot_digest(self.capture(session))
            self.assertNotEqual(before, after)
            wall.normal = (1, 0, 0)
            rotated = snapshot_digest(self.capture(session))
            self.assertNotEqual(after, rotated)
            fight.barriers.seen.clear()
            self.assertNotEqual(rotated, snapshot_digest(self.capture(session)))

    def test_pin_target_and_projectile_impact_policy_are_causal_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        definition = npc.Definition(player.hero.guid, player.hero.body, 'Fixture', 50)
        bot = npc.spawn(player.match, definition, (0, 0, 4), team=1)
        shot = combat.Rocket(bot, bot.team, bot.position, (0, 0, -1), 80, 1.25, None, None)
        fight.rockets.append(shot)
        before = snapshot_digest(self.capture(session))
        shot.distance_falloff = True
        flying = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, flying)
        shot.implicit_absorbable = True
        eligible = snapshot_digest(self.capture(session))
        self.assertNotEqual(flying, eligible)
        shot.implicit_absorbable = False
        shot.motion = ((0, 0.5, False, 0.25, 2, 0.1, 0),)
        moving = snapshot_digest(self.capture(session))
        self.assertNotEqual(flying, moving)
        shot.concussion = (20, 5, ((8, 1, 1),))
        pushing = snapshot_digest(self.capture(session))
        self.assertNotEqual(moving, pushing)
        shot.definition = 0x0400000000000001
        tagged = snapshot_digest(self.capture(session))
        self.assertNotEqual(pushing, tagged)
        fight.pins[bot.entity] = player.body
        pinned = snapshot_digest(self.capture(session))
        self.assertNotEqual(tagged, pinned)
        bot.impulse_mover = Mover(bot.position, world=player.match.collision_world)
        moving_bot = snapshot_digest(self.capture(session))
        self.assertNotEqual(pinned, moving_bot)
        bot.impulse_mover.velocity = (0, 5, 20)
        self.assertNotEqual(moving_bot, snapshot_digest(self.capture(session)))

    def test_grasp_conversion_and_temporary_pool_are_causal_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        pool = fight.vitals_of(target)
        before = snapshot_digest(self.capture(session))
        fight.grasp.conversions[(player.body, 1)] = 1
        admitted = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, admitted)
        pool.bonus_shields = pool.bonus_shields_max = 72
        self.assertNotEqual(admitted, snapshot_digest(self.capture(session)))

    def test_held_barrier_pool_and_break_clock_are_causal_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        state = combat.reinhardt_barrier.BarrierState(100, 1600, 0, 2)
        fight.reinhardt_barriers.states[player.body] = state
        before = snapshot_digest(self.capture(session))
        state.current = 0
        broken = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, broken)
        state.broken_until = 5
        locked = snapshot_digest(self.capture(session))
        self.assertNotEqual(broken, locked)
        player.body_script.world.beam_barrier = 0xA00000F0
        self.assertNotEqual(locked, snapshot_digest(self.capture(session)))

    def test_winston_sphere_pool_and_expiry_are_causal_storage(self):
        session, _, players = self.scene()
        owner = players[0]
        fight = owner.match.combat
        actor = combat.winston_barrier.Bubble(owner, owner.body, (0, 1, 0), 700, 9)
        fight.winston_barriers.actors[owner.body] = actor
        before = snapshot_digest(self.capture(session))
        actor.health -= 120
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, damaged)
        actor.expires -= 1
        self.assertNotEqual(damaged, snapshot_digest(self.capture(session)))

    def test_phase_world_anchor_is_causal_storage(self):
        session, _, players = self.scene()
        owner = players[0]
        fight = owner.match.combat
        before = snapshot_digest(self.capture(session))
        fight.phase_clocks[owner.body] = (fight.tick, owner.body_script.frame)
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_early_projectile_respawn_body_is_causal_storage(self):
        session, _, players = self.scene()
        owner = players[0]
        fight = owner.match.combat
        before = snapshot_digest(self.capture(session))
        fight.early_projectile_respawns.add(owner.body)
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))
        fight.early_projectile_respawns.clear()
        self.assertEqual(before, snapshot_digest(self.capture(session)))
        del fight.early_projectile_respawns
        self.capture(session)  # Older snapshots can omit this optional field.

    def test_symmetra_charge_world_clock_is_causal_storage(self):
        session, _, players = self.scene()
        owner = players[0]
        self.assertTrue(session.apply("switch_hero", {"account": owner.account_lo, "hero": symmetra.HERO}))
        script = owner.body_script
        tick, charged_through = script.charge_clock
        before = snapshot_digest(self.capture(session))
        script.charge_clock = tick, charged_through + 1
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_portal_pair_and_transit_deadline_are_causal_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        definition = npc.Definition(player.hero.guid, player.hero.body, 'Fixture', 50, shields=250)
        endpoints = tuple(npc.spawn(player.match, definition, (0, 0, z), team=1) for z in (0, 8))
        pair = PortalPair(player, endpoints, 7, player.body, 1.5)
        fight.portals.pairs.append(pair)
        before = snapshot_digest(self.capture(session))
        pair.ready_at += .016
        pending = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, pending)
        fight.portals.transits[player.body] = (8, 2.5)
        self.assertNotEqual(pending, snapshot_digest(self.capture(session)))

    def test_placement_frame_markers_change_server_hash_without_running_callbacks(self):
        session, _, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": 1, "hero": symmetra.HERO})
        self.assertEqual(player.hero.guid, symmetra.HERO)
        component = player.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x156B)
        state = instance.node_state(instance.graph.node(57))
        self.assertIsInstance(state, nodes.Placement)
        with (
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no variable evaluation")),
            mock.patch.object(nodes.Placement, "tick", side_effect=AssertionError("no placement callback")),
            mock.patch.object(runtime.Instance, "state", side_effect=AssertionError("no lazy allocation")),
        ):
            before = snapshot_digest(self.capture(session))
            state.latest_frame = 17
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

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
    def test_recorded_ice_wall_movement_contact_and_cancel_replay(self):
        fixture = journal_tests.ServerJournalTests()
        session, sent = fixture.make()
        peers, (mei, sym) = fixture.connected(session, sent)
        session.apply("switch_hero", {"account": 1, "hero": cryo.HERO})
        session.apply("switch_hero", {"account": 2, "hero": symmetra.HERO})
        fight = mei.match.combat
        mx, my, mz = mei.mover.position
        sx, _, sz = sym.mover.position
        center = ((mx + sx) / 2, my, (mz + sz) / 2)
        yaw, pitch = aim(fight._eye(mei), center)

        def mei_command(buttons):
            peers[0].commands(mei.body_script.frame + 1, 1, buttons, yaw=yaw, pitch=pitch, now=101.0)

        for button in (16, 0, 1, 0):
            mei_command(button)
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        facing, _ = aim(fight._eye(sym), center)
        peers[1].commands(sym.body_script.frame + 1, 30, 0, forward=127, yaw=facing, now=101.0)
        self.assertIsInstance(sym.mover.world, MovementOverlay)
        self.assertIsNotNone(sym.mover.body)
        self.assertTrue(any(contact.shape in sym.mover.world.addition.engine.shapes
                            for contact in sym.mover.body.contacts))
        contact_root = len(session._roots)
        contact_digest = snapshot_digest(ServerSnapshotTests.capture(session))

        mei_command(16)
        self.assertFalse(fight.ice_wall.pieces)
        peers[1].commands(sym.body_script.frame + 1, 20, 0, forward=127, yaw=facing, now=101.0)
        self.assertIs(sym.mover.world, mei.match.collision_world)
        session.report_callbacks()
        end_digest = snapshot_digest(ServerSnapshotTests.capture(session))
        replay = _ReplaySession(session.finish(), session.registry.resolve)
        root = replay._root
        replayed_roots = 0
        contact_digests = []

        def run_root(operation, arguments):
            nonlocal replayed_roots
            result = root(operation, arguments)
            replayed_roots += 1
            if replayed_roots == contact_root:
                contact_digests.append(snapshot_digest(ServerSnapshotTests.capture(replay)))
            return result

        with mock.patch.object(replay, "_root", side_effect=run_root):
            self.assertEqual(replay.run()["status"], "matched_controlled_roots_and_wire")
        self.assertEqual(contact_digests, [contact_digest])
        self.assertEqual(snapshot_digest(ServerSnapshotTests.capture(replay)), end_digest)
        replay_match = replay.server.matches[0]
        self.assertFalse(replay_match.combat.ice_wall.pieces)
        self.assertEqual(replay_match.players[1].mover.position, sym.mover.position)

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
