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

from ow174.game import combat, graviton, interception, reinhardt_barrier, zarya
from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import expr, runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def test_fan_seed_state_is_causal_and_generated_table_caches_are_not(self):
        from ow174.game import mccree, weapon_spread
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, mccree.HERO), now=100.9)
        self.assertEqual(player.hero.guid, mccree.HERO)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x01D2)
        state = weapon.state(weapon.graph.node(37).state)
        state.spread_ramp = 0.0

        def sample():
            return mccree.fan_direction(player, state, (0, 0, 1), 0, state.start,
                                        (state.counter, state.start))

        vector = sample()
        first = snapshot_digest(self.capture(session))
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        self.assertEqual(first, snapshot_digest(self.capture(session)))
        self.assertEqual(vector, sample())
        state.counter += 1
        self.assertNotEqual(first, snapshot_digest(self.capture(session)))
        self.assertNotEqual(vector, sample())

    def test_eye_profiles_are_causal_input_and_export_never_queries_geometry(self):
        session, _, _ = self.scene()
        before = snapshot_digest(self.capture(session))
        original = combat.EYE_PROFILES
        changed = ((0x04000000000003BF, 1.7, 1.12), *original[1:])
        with mock.patch.object(combat, "eye_height", side_effect=AssertionError("no geometry query")):
            with mock.patch.object(combat, "EYE_PROFILES", changed):
                self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            self.assertEqual(snapshot_digest(self.capture(session)), before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ZARYA_NPC_DATA"), "requires actual NPC graphs")
    def test_assassin_controller_target_pin_and_health_clock_are_causal_without_execution(self):
        import test_game_zarya_npcs as fixtures

        from ow174.game import npc

        fixtures.ZaryaFiveNpcTests.setUpClass()
        try:
            session, _, players = self.scene()
            player = players[0]
            definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Assassin")
            bot = npc.spawn(player.match, definition, (0, 1, 2), team=1)
            world = bot.body_script.component.world
            before = snapshot_digest(self.capture(session))
            world.manual_target = player.body
            targeted = snapshot_digest(self.capture(session))
            self.assertNotEqual(before, targeted)
            player.match.combat.pins[bot.entity] = player.body
            pinned = snapshot_digest(self.capture(session))
            self.assertNotEqual(targeted, pinned)
            world.controller_entity += 2
            controlled = snapshot_digest(self.capture(session))
            self.assertNotEqual(pinned, controlled)
            instance = next(i for i in bot.body_script.component.instances.values()
                            if i.graph.index == 0x1157)
            state = instance.state(instance.graph.node(15).state)
            clocked = snapshot_digest(self.capture(session))
            state.last_applied += 16
            changed = snapshot_digest(self.capture(session))
            self.assertNotEqual(clocked, changed)
            with mock.patch.object(runtime.Instance, "evaluate", side_effect=AssertionError("no evaluation")):
                self.assertEqual(snapshot_digest(self.capture(session)), changed)
            bot.apply_impulse((0, 8, 5), player.match.tick, player.match.collision_world,
                              category=combat.MOVER)
            moving = snapshot_digest(self.capture(session))
            self.assertNotEqual(moving, changed)
            bot.impulse_mover.velocity = (0, 7, 5)
            self.assertNotEqual(snapshot_digest(self.capture(session)), moving)
        finally:
            fixtures.ZaryaFiveNpcTests.tearDownClass()

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

    def test_pending_nano_admission_changes_causal_digest(self):
        session, _, players = self.scene(two=True)
        caster, target = players
        fight = caster.match.combat
        before = snapshot_digest(self.capture(session))
        fight.pending_nano[fight.tick + 1] = [(caster.body, (expr.Entity(target.body),), 500)]
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_zarya_field_barrier_and_consumed_beam_clock_are_causal_state(self):
        session, _, players = self.scene(two=True)
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        field = graviton.Field(player, player.body, player.team, (0.0, 1.0, 0.0), 1, 251, 6.0)
        fight.zarya_gravitons.fields.append(field)
        created = snapshot_digest(self.capture(session))
        self.assertNotEqual(created, before)
        field.end_tick += 1
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, created)
        fight.zarya_charge_ready[player.body] = 33
        cast_lock = snapshot_digest(self.capture(session))
        self.assertNotEqual(cast_lock, changed)
        fight.zarya_barriers.active[player.body] = zarya.Barrier((1, 1, 16), 200.0)
        shielded = snapshot_digest(self.capture(session))
        self.assertNotEqual(shielded, cast_lock)
        fight.zarya_barriers.active[player.body].remaining = 100.0
        consumed = snapshot_digest(self.capture(session))
        self.assertNotEqual(consumed, shielded)
        projected = zarya.Barrier((2, 1, 16), 153.0, players[1].body)
        fight.zarya_barriers.projected[player.body] = projected
        projected_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(projected_digest, consumed)
        projected.recipient = player.body
        reassigned = snapshot_digest(self.capture(session))
        self.assertNotEqual(reassigned, projected_digest)
        fight.zarya_barriers.cooldown_deadlines[player.body, True] = 120
        cooldown = snapshot_digest(self.capture(session))
        self.assertNotEqual(cooldown, reassigned)
        fight.zarya_melee_ready[player.body] = 119
        melee = snapshot_digest(self.capture(session))
        self.assertNotEqual(melee, cooldown)
        fight.zarya_secondary_ready[player.body] = 118
        secondary = snapshot_digest(self.capture(session))
        self.assertNotEqual(secondary, melee)
        fight.beam_seen[player.body] = ((1, 1, 16), 32)
        clocked = snapshot_digest(self.capture(session))
        self.assertNotEqual(clocked, secondary)
        fight.zarya_beam_world_ms[player.body] = 32
        beam_world = snapshot_digest(self.capture(session))
        self.assertNotEqual(beam_world, clocked)
        fight.zarya_recovery.bodies[player.body] = zarya.Recovery(1, 2)
        recovering = snapshot_digest(self.capture(session))
        self.assertNotEqual(recovering, beam_world)
        fight.zarya_recovery.bodies[player.body].processed_tick = 3
        recovered = snapshot_digest(self.capture(session))
        self.assertNotEqual(recovered, recovering)
        fight.zarya_charge_tick += 1
        charged = snapshot_digest(self.capture(session))
        self.assertNotEqual(charged, recovered)
        player.body_script.world.phase_deadline = fight.tick + 10
        self.assertNotEqual(snapshot_digest(self.capture(session)), charged)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires actual shield mesh")
    def test_reinhardt_barrier_pool_and_external_geometry_are_causal_and_read_only(self):
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, reinhardt_barrier.HERO), now=100.9)
        self.assertEqual(player.hero.guid, reinhardt_barrier.HERO)
        service = player.match.combat.reinhardt_barriers
        shield = service.sync(player)
        self.assertEqual(shield.current, 1600)
        with mock.patch.object(service.__class__, "sync", side_effect=AssertionError("read-only capture")):
            before = snapshot_digest(self.capture(session))
            shield.current -= 47
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "requires Orisa 4062 mesh")
    def test_orisa_deployment_flight_pending_and_active_storage(self):
        from ow174.game.collision_fingerprint import world_fingerprint
        from ow174.game.commands import Command

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E000000000013E), now=100.95)
        self.assertEqual(player.hero.guid, 0x02E000000000013E)
        match = player.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.mover.position[:] = [0, 0, 0]
        player.mover.body = None
        fight = match.combat
        service = fight.reinhardt_barriers

        def step(buttons=0):
            tick = match.tick + 1
            match.update(tick * 0.016, tick)
            player.take_commands([Command(tick, yaw=0, pitch=0, buttons=buttons, extra=0)])

        step(16)
        for _ in range(120):
            if fight.rockets:
                break
            step()
        rocket, = fight.rockets
        self.assertIsNotNone(rocket.orisa_launch)
        flying = snapshot_digest(self.capture(session))
        launch = rocket.orisa_launch
        rocket.orisa_launch = (1.0, 0.0, 0.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), flying)
        rocket.orisa_launch = launch

        for _ in range(120):
            if service.orisa_pending:
                break
            step()
        due, pending, position = service.orisa_pending[0]
        self.assertIs(pending, rocket)
        self.assertGreater(due, match.tick)
        queued = snapshot_digest(self.capture(session))
        service.orisa_pending[0] = (due + 1, pending, position)
        self.assertNotEqual(snapshot_digest(self.capture(session)), queued)
        service.orisa_pending[0] = (due, pending, position)

        while match.tick < due:
            step()
        actor, = service.orisa.values()
        self.assertFalse(service.orisa_pending)
        with mock.patch.object(runtime.Instance, "evaluate", side_effect=AssertionError("read-only capture")):
            active = self.capture(session)
            self.assertIn(world_fingerprint(actor.geometry), active["assets"])
            self.assertIs(session.registry.resolve(world_fingerprint(actor.geometry)), actor.geometry)
            before = snapshot_digest(active)
            service.damage(actor, 47)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_grasp_pool_grant_token_and_decay_clock_are_causal_and_read_only(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        before = snapshot_digest(self.capture(session))
        vitals.grant_temporary_shields(interception.SHIELD_TAG, 28.2, 400)
        granted = snapshot_digest(self.capture(session))
        self.assertNotEqual(granted, before)
        pool = vitals.temporary_shields[interception.SHIELD_TAG]
        pool.now -= 1
        spent = snapshot_digest(self.capture(session))
        self.assertNotEqual(spent, granted)
        fight.grasp_health.grants[player.body] = (1, 2, 3)
        token = snapshot_digest(self.capture(session))
        self.assertNotEqual(token, spent)
        fight.grasp_health.grants[player.body] = (1, 3, 3)
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, token)
        decay = interception.ShieldDecay(180, 0, 5)
        fight.grasp_health.decays[player.body] = decay
        clocked = snapshot_digest(self.capture(session))
        self.assertNotEqual(clocked, changed)
        for field in ("starts_tick", "last_tick", "rate"):
            original = getattr(decay, field)
            setattr(decay, field, original + 1)
            self.assertNotEqual(snapshot_digest(self.capture(session)), clocked)
            setattr(decay, field, original)
        with mock.patch.object(interception.GraspShields, "update",
                               side_effect=AssertionError("no shield simulation")):
            self.assertEqual(snapshot_digest(self.capture(session)), clocked)
        self.assertEqual(pool.now, 27.2)

    def test_reflected_projectile_authority_source_and_remaining_flight_are_causal(self):
        session, _, players = self.scene(two=True)
        a, b = players
        rocket = combat.Rocket(a, a.team, (0, 1, 0), (0, 0, 1), 25, 20,
                               combat.HitDef(amount=5), None, source_entity=a.body,
                               definition=0x04000000000008CE, gravity=9.8)
        a.match.combat.rockets.append(rocket)
        digest = snapshot_digest(self.capture(session))
        for name, value in (("shooter", b), ("team", rocket.team + 1),
                            ("source_entity", b.body), ("left", 19), ("flown", 1),
                            ("spawn", graviton.FIELD_ENTITY)):
            setattr(rocket, name, value)
            changed = snapshot_digest(self.capture(session))
            self.assertNotEqual(changed, digest, name)
            digest = changed
        with mock.patch.object(combat.Combat, "_fly_for", side_effect=AssertionError("no flight")):
            self.assertEqual(snapshot_digest(self.capture(session)), digest)
        self.assertEqual((rocket.left, rocket.flown, rocket.source_entity), (19, 1, b.body))
        starts = a.match.combat.projectile_defense_starts
        activation = (11, 10, 1, 400)
        starts[b.body] = {activation: 50}
        digest = snapshot_digest(self.capture(session))
        starts[b.body][activation] = 51
        self.assertNotEqual(snapshot_digest(self.capture(session)), digest)

    def test_projectile_definition_and_external_tag_catalog_are_causal_and_read_only(self):
        session, _, players = self.scene()
        player = players[0]
        rocket = combat.Rocket(player, player.team, (0, 1, 0), (0, 0, 1), 25, 2,
                               None, combat.HitDef(amount=47, self_scale=0.5),
                               definition=0x04000000000003DB)
        player.match.combat.rockets.append(rocket)
        before = snapshot_digest(self.capture(session))
        rocket.definition = 0x04000000000008CE
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(before, changed)
        rocket.blast.self_scale = 0.25
        scaled = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, scaled)
        with mock.patch.object(interception, "read_tags", side_effect=AssertionError("no tag file loading")):
            self.assertEqual(snapshot_digest(self.capture(session)), scaled)
            with mock.patch.object(interception, "_TAGS", types.MappingProxyType({
                    0x04000000000003DB: frozenset({interception.EXCLUDED_TAG})})):
                self.assertNotEqual(snapshot_digest(self.capture(session)), scaled)
            self.assertEqual(snapshot_digest(self.capture(session)), scaled)

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
