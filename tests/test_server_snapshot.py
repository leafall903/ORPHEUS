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
from ow174.game import projectiles, winston_barrier
from ow174.game.collision import MOVER, StaticCollisionProvider, flat_world
from ow174.game.combat import BioticDose, HealingModifier, Rocket, Sleep
from ow174.game.content import SOLDIER
from ow174.game.defenses import MEI, ORISA, REAPER, ZARYA
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def test_actual_helix_launch_tick_changes_the_causal_digest(self):
        session, peers, players = self.scene()
        player = players[0]
        fight = player.match.combat
        peers[0].commands(player.body_script.frame + 1, 1, 2)
        rocket, = fight.rockets
        self.assertIsNone(rocket.last_tick)
        self.assertEqual(rocket.launch_tick, fight.tick)
        emitted = snapshot_digest(self.capture(session))
        rocket.launch_tick += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), emitted)
        self.assertIsNone(rocket.last_tick)

    def test_canister_distance_and_rebound_count_change_the_causal_digest(self):
        session, _, players = self.scene()
        soldier = players[0]
        fight = soldier.match.combat
        rocket = Rocket(
            soldier, soldier.team, (0, 1, 0), (0, -1, 0), 50, 10, None, None,
            launch_tick=fight.tick, gravity=9.8, range_limit=150,
            rebound_limit=9, rebound_scale=.1,
        )
        fight.rockets.append(rocket)
        before = snapshot_digest(self.capture(session))
        rocket.distance = 1
        moved = snapshot_digest(self.capture(session))
        self.assertNotEqual(moved, before)
        rocket.rebounds = 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), moved)

    def test_field_airborne_gate_and_placement_change_the_causal_digest(self):
        session, _, players = self.scene()
        soldier = players[0]
        manager = soldier.match.combat.projectiles
        tick = soldier.match.tick
        shot = projectiles.Shot(
            soldier, soldier.body, 0x040000000000072D, tick, 1, 1, (1, tick),
            spawn=projectiles.FIELD_ENTITY, where=(0, 8, 0), field_due=tick, field_airborne=True,
        )
        manager.shots.append(shot)
        airborne = snapshot_digest(self.capture(session))
        shot.field_airborne = False
        landed = snapshot_digest(self.capture(session))
        self.assertNotEqual(landed, airborne)
        shot.where = (0, 0, .5)
        self.assertNotEqual(snapshot_digest(self.capture(session)), landed)

    def test_pending_biotic_rifle_dose_changes_the_causal_digest(self):
        session, _, players = self.scene()
        target = players[0]
        fight = target.match.combat
        dose = BioticDose(target, target.body, target.body, True, fight.tick, 70, None, fight.tick)
        before = snapshot_digest(self.capture(session))
        fight.biotic_doses.append(dose)
        pending = snapshot_digest(self.capture(session))
        self.assertNotEqual(pending, before)
        dose.applied = 10
        self.assertNotEqual(snapshot_digest(self.capture(session)), pending)

    def test_anti_heal_deadline_changes_the_causal_digest(self):
        session, _, players = self.scene()
        target = players[0]
        fight = target.match.combat
        modifier = HealingModifier(anti_from=fight.tick, anti_until=fight.tick + 250)
        fight.heal_modifiers[target.body] = modifier
        before = snapshot_digest(self.capture(session))
        modifier.anti_until -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_sleep_deadline_changes_the_causal_digest(self):
        session, _, players = self.scene()
        target = players[0]
        fight = target.match.combat
        sleep = Sleep(fight.tick, fight.tick + 313)
        fight.sleeps[target.body] = sleep
        before = snapshot_digest(self.capture(session))
        sleep.end -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_orisa_barrier_child_and_pool_are_causal_after_actual_deploy(self):
        session, peers, players = self.scene(two=True)
        owner = players[1]
        service = owner.match.combat.orisa_barriers
        if service.geometry is None:
            self.skipTest("original Orisa collision descriptor is required")
        peers[1].message(21616, pick(owner.entity, ORISA), now=100.8)
        peers[1].commands(owner.body_script.frame + 1, 100, 16)
        self.assertTrue(service.flights)
        session.tick(101.9, missed=65)
        shield, = service.actors.values()
        self.assertEqual(shield.health, 600)
        initial = snapshot_digest(self.capture(session))
        shield.health -= 20
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)

    def test_deployed_winston_dome_pool_and_child_clock_change_causal_digest(self):
        session, peers, players = self.scene(two=True)
        owner = players[1]
        peers[1].message(21616, pick(owner.entity, winston_barrier.HERO), now=100.8)
        peers[1].commands(owner.body_script.frame + 1, 40, 16)
        dome, = owner.match.combat.winston_barriers.actors.values()
        self.assertEqual(dome.health, 700)
        with mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM ran")):
            initial = snapshot_digest(self.capture(session))
            dome.health -= 20
            damaged = snapshot_digest(self.capture(session))
            dome.component.frame += 1
            advanced = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, initial)
        self.assertNotEqual(advanced, damaged)

    def test_particle_barrier_pool_changes_the_causal_digest(self):
        session, _, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": ZARYA})
        pools = player.match.combat.zarya_barriers.pools
        pools[player.body] = ((1, 1, 16000), 200.0)
        initial = snapshot_digest(self.capture(session))
        pools[player.body] = ((1, 1, 16000), 180.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        pools[player.body] = ((1, 1, 16000), 200.0)
        player.match.combat.zarya_barriers.projected[player.body] = ((2, 1, 16000), 180.0, 5)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)

    def test_cryo_healing_settlement_changes_the_causal_digest(self):
        session, _, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": MEI})
        samples = player.match.combat.cryo_samples
        initial = snapshot_digest(self.capture(session))
        samples[player.body] = ((1, 1, 16000), 10.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        samples.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_reaper_world_frame_anchor_changes_the_causal_digest(self):
        session, _, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": REAPER})
        script = player.body_script
        initial = snapshot_digest(self.capture(session))
        script.world_frame_anchor = (player.match.tick, script.frame)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        script.world_frame_anchor = None
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_manual_pin_identity_changes_digest_without_body_health_change(self):
        session, _, players = self.scene(two=True)
        player = players[0]
        fight = player.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        health = pool.total
        initial = snapshot_digest(self.capture(session))
        # Stored-state sensitivity only; actual1157 combat is tested separately.
        source = players[1].body
        fight.pins[source] = player.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        self.assertEqual(pool.total, health)
        fight.pins.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_barrier_pool_and_regen_deadline_change_digest_without_body_hp_change(self):
        session, _, players = self.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": 0x02E0000000000007})
        fight = player.match.combat
        state = fight.barriers.sync(player)
        if state is None:
            self.skipTest("explicit original barrier geometry required for barrier storage witness")
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        health = pool.total
        initial = snapshot_digest(self.capture(session))
        state.current -= 1.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        self.assertEqual(pool.total, health)
        state.current += 1.0
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        state.regen_after += .016
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        self.assertEqual(pool.total, health)

    def test_grasp_conversion_and_extra_pool_capacity_change_causal_digest(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        initial = snapshot_digest(self.capture(session))
        health = pool.total
        fight.grasp.conversions[(player.body, 0)] = 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        fight.grasp.conversions.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        pool.bonus_shields_max = 72
        self.assertEqual(pool.total, health)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        pool.bonus_shields_max = 0.0
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_npc_external_mover_hidden_physics_changes_causal_digest(self):
        from test_bots import make_bot

        session, _, players = self.scene()
        match = players[0].match
        actor = make_bot()
        actor.entity = max((bot.entity for bot in match.bots), default=actor.entity - 1) + 1
        match.bots.append(actor)
        actor.apply_impulse((4, 6, 0), match.tick, match.collision_world, category=MOVER)
        initial = snapshot_digest(self.capture(session))
        position = tuple(actor.position)
        before = actor.impulse_mover.spring
        actor.impulse_mover.spring += .01
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        self.assertEqual(actor.position, position)
        actor.impulse_mover.spring = before
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

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
