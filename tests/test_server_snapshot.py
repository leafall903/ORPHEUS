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
from test_game_graph_analysis import make_graph
from test_server_journal import MAP, FixedSource, Peer, acknowledge_ownership, pick

from ow174.game import ashe, mei_blizzard, npc, pools
from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import expr, filter_condition, graph, runtime
from ow174.game.script.controller_filter import ControllerFilterInputs
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.script.player_controller import DEFINITION as PLAYER_DEFINITION
from ow174.game.script.player_controller import ControllerVM
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def scene(self, two=False):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, two=two)
        return session, peers, players

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "requires 104319 Orisa barrier mesh")
    def test_landed_orisa_barrier_pool_and_child_clock_are_passive_storage(self):
        from ow174.game.commands import Command

        session, peers, (owner,) = self.scene()
        peers[0].message(21616, pick(owner.entity, 0x02E000000000013E), now=100.8)
        session.tick(100.960)
        manager = owner.match.combat.orisa_barriers
        for frame in range(35):
            session.tick(100.976 + frame * .016)
            command = Command(owner.body_script.frame + 1, pitch=8192, buttons=16 if not frame else 0)
            owner.take_commands([command])
            if manager.actors:
                break
        shield, = manager.actors.values()
        self.assertEqual(shield.health, 600)
        before = snapshot_digest(self.capture(session))
        shield.health -= 20
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        shield.health += 20
        shield.component.frame += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires 104319 Barrier Field mesh")
    def test_raised_reinhardt_barrier_pool_is_passive_storage(self):
        from ow174.game.commands import Command

        session, peers, (owner,) = self.scene()
        peers[0].message(21616, pick(owner.entity, 0x02E0000000000007), now=100.8)
        session.tick(100.960)
        host = owner.match.combat.reinhardt_barriers
        shield = None
        for frame in range(40):
            session.tick(100.976 + frame * .016)
            owner.take_commands([Command(owner.body_script.frame + 1, buttons=2)])
            shield = host.states.get(owner.body)
            if shield is not None and shield.active:
                break
        self.assertIsNotNone(shield)
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        before = snapshot_digest(self.capture(session))
        shield.current -= 40
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_active_earthshatter_front_is_passive_causal_storage(self):
        session, peers, (owner,) = self.scene()
        peers[0].message(21616, pick(owner.entity, 0x02E0000000000007), now=100.8)
        session.tick(100.960)
        fight = owner.match.combat
        for frame in range(45):
            session.tick(100.976 + frame * .016)
            owner.take_commands([Command(owner.body_script.frame + 1)])
        owner.body_script.set_ult_charge(1540)
        for frame in range(50):
            session.tick(101.696 + frame * .016)
            owner.take_commands([Command(owner.body_script.frame + 1, buttons=32 if not frame else 0)])
            if fight.earthshatters:
                break
        wave, = fight.earthshatters.values()
        self.assertEqual(wave.reach, 20)
        original = snapshot_digest(self.capture(session))
        travelled = wave.travelled
        wave.travelled += .25
        self.assertNotEqual(snapshot_digest(self.capture(session)), original)
        wave.travelled = travelled
        self.assertEqual(snapshot_digest(self.capture(session)), original)
        wave.blocked.add(1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), original)
        wave.blocked.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), original)

    @staticmethod
    def capture(session):
        return export_server_state(session.server, archives=session._matches.values(),
                                   collision_provider=session._collision_reads, assets=session.registry)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_PVE_CONTROLLER_EXTENSION", ".")).is_file(),
                         "requires actual external NPC controller metadata")
    def test_active_assassin_rate_and_body_binding_are_passive_causal_storage(self):
        from ow174.game import npc_control

        session, _, (player,) = self.scene()
        match = player.match
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000200}))
        host = player.controller_vm
        mask = (1 << 25) | (1 << 26) | (1 << 31) | (1 << 47)
        profile = ControllerFilterInputs(player.entity, (mask, 1 << (97 - 64)),
                                         0, 0x40001, provenance="declared snapshot fixture")
        host.bind_filter_inputs(profile)
        catalog = Path(os.environ["ORPHEUS_PVE_NPC_DIR"]) / "pve_npcs_174.json"
        definition = next(d for d in npc.load_catalog(catalog).values() if d.name == "Assassin")
        x, y, z = player.mover.position
        bot = npc.spawn(match, definition, (x, y, z + 2), team=1 - player.team)
        host.set_linked_target_sample(bot.entity, 0.25, 0.0, 90.0)
        host.enable_pin_effects()
        player.mover.yaw = player.mover.pitch = 0
        npc_control.start(match, player)
        for index in range(95):
            session.tick(100.944 + index * 0.016)
        component = bot.body_script.component
        attack = next(i for i in component.instances.values() if i.graph.index == 0x1157)
        rate = attack.node_state(attack.graph.node(15))
        self.assertTrue(rate.active)
        self.assertEqual(rate.target_body, player.body)
        services = host.component.world.pin_services
        manager = services.messages.host.manager
        channel, = manager.channels.values()
        sender, = channel.senders
        self.assertEqual(sender.state.node.pos, 44)
        effect, = host.component.instances.values()
        linked = effect.node_state(effect.graph.node(9))
        backend = host.component.world.numeric_modifiers
        self.assertTrue(linked.active)
        self.assertTrue(backend.records)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("passive snapshot")):
            before = snapshot_digest(self.capture(session))
            backend.source_frame += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            backend.source_frame -= 1
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            sender.sequence += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            sender.sequence -= 1
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            rate.last_applied += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            rate.last_applied -= 1
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            rate.target_body = 0
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            rate.target_body = player.body
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            component.world.match = None
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        component.world.match = match
        host.retire()
        self.assertTrue(profile.retired)
        self.assertTrue(services.messages.retired)
        self.assertFalse(manager.channels)
        self.assertFalse(services.registry.states)
        self.assertFalse(host.component.instances)

    @unittest.skipUnless(graph.entity_body(PLAYER_DEFINITION) is not None, "requires actual000E metadata")
    def test_controller_wire_reservation_is_passive_causal_and_rejects_live_collisions(self):
        session, _, players = self.scene()
        host = players[0].controller_vm
        original_ids, next_id = host.reserved_wire_ids, host.component.next_id
        with (mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")),
              mock.patch.object(ControllerVM, "validate_namespace",
                                side_effect=AssertionError("no host callback"))):
            before = snapshot_digest(self.capture(session))
            host.reserved_wire_ids = original_ids[:-1]
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            host.reserved_wire_ids = original_ids
            host.component.next_id = original_ids[0]
            with self.assertRaisesRegex(ServerSnapshotError, "reserved controller wire namespace"):
                self.capture(session)
            original_component = host.component

            class PoisonComponent:
                @property
                def instances(self):
                    raise AssertionError("no unregistered component getter")

            host.component = PoisonComponent()
            with self.assertRaisesRegex(ServerSnapshotError, "controller wire namespace component"):
                self.capture(session)
            host.component = original_component
            host.component.next_id = next_id
            host.component.create(make_graph(0xFFEE), instance_id=original_ids[0], init=False)
            with self.assertRaisesRegex(ServerSnapshotError, "reserved controller wire namespace"):
                self.capture(session)

    @unittest.skipUnless(graph.entity_body(PLAYER_DEFINITION) is not None, "requires actual000E metadata")
    def test_independent_player_controller_vm_is_causal_passive_domain_storage(self):
        session, _, players = self.scene()
        player = players[0]
        host = player.controller_vm
        self.assertIsNotNone(host)
        self.assertIsNot(host.component, player.body_script.component)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
        host.component.set(14695, True)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    @unittest.skipUnless(graph.entity_body(PLAYER_DEFINITION) is not None, "requires actual000E metadata")
    def test_explicit_controller_filter_inputs_are_causal_passive_host_state(self):
        session, _, players = self.scene()
        host = players[0].controller_vm
        profile = ControllerFilterInputs(host.component.entity,
                                         ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64)), 0, 0x40001,
                                         provenance="declared controller input provider")
        host.bind_filter_inputs(profile)
        with (mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")),
              mock.patch.object(ControllerFilterInputs, "bits", side_effect=AssertionError("no projection"))):
            before = snapshot_digest(self.capture(session))
            profile.update(replicated_bits=0x40021)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            before = snapshot_digest(self.capture(session))
            profile.retire()
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_deployed_world_barrier_health_and_hosted_timer_are_causal_storage(self):
        session, _, (player,) = self.scene()
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000009}))
        for offset in range(20):
            session.tick(101.0 + offset * 0.016)
            player.take_commands([Command(player.match.tick, buttons=16 if offset == 10 else 0)])
        barrier, = player.match.combat.world_barriers.actors.values()
        hosted = next(i for i in barrier.component.instances.values() if i.graph.index == 0x00FC)
        timer = hosted.states[11]
        self.assertTrue(timer.active)
        self.assertEqual(barrier.health, 700)
        baseline = snapshot_digest(self.capture(session))
        barrier.health -= 40
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        barrier.health += 40
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)
        timer.cur -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)

    def test_deployed_sigma_mesh_pool_pose_and_child_clock_are_passive_storage(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        session, _, (player,) = self.scene()
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": 0x02E000000000023B}))
        for offset in range(30):
            session.tick(101.0 + offset * 0.016)
            player.take_commands([Command(player.match.tick, buttons=2 if 5 <= offset < 20 else 0)])
        barriers = player.match.combat.sigma_barriers
        self.assertIsNotNone(barriers.mesh)
        shield, = barriers.actors.values()
        self.assertEqual(shield.mode, 3)
        self.assertEqual(shield.health, 700)
        self.assertEqual(shield.instance().graph.index, 0x1A2A)
        x, y, z = shield.position
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("passive snapshot")):
            baseline = snapshot_digest(self.capture(session))
            for obj, field, changed in ((shield, "health", 660), (shield, "position", (x, y, z + 1)),
                                        (shield.component, "frame", shield.component.frame + 1)):
                with self.subTest(field=field), mock.patch.object(obj, field, changed):
                    self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
                self.assertEqual(snapshot_digest(self.capture(session)), baseline)

    def test_ashe_effect_lifetimes_and_companion_gate_are_causal_stored_state(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        grenade = ashe.Grenade(player, player.body, player.team, (0, 2, 0), (0, 0, 1), 0, 125)
        fight.dynamite.grenades.append(grenade)
        before = snapshot_digest(self.capture(session))
        grenade.fuse += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        grenade.fuse -= 1
        grenade.absorb_damage = 75
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        burn = ashe.Burn(player, player.body, player.body, 0, 313, 16, 2.5)
        fight.dynamite.burns[(player.body, player.body)] = burn
        before = snapshot_digest(self.capture(session))
        burn.pulses += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        before = snapshot_digest(self.capture(session))
        with mock.patch.object(burn, "tags", (0x0D80000000003177,)):
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        fight.companions.unavailable_reason = "missing actor data"
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        impulse = ashe.Impulse((0, 5, 20), 1)
        fight.impulses.pending[player.body] = [impulse]
        before = snapshot_digest(self.capture(session))
        impulse.due_tick += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        fight.sleeps[player.body] = (fight.tick, fight.tick + 313)
        before = snapshot_digest(self.capture(session))
        fight.sleeps[player.body] = (fight.tick, fight.tick + 32)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        fight.sleep_views[player.body] = (0.0,) * 6
        before = snapshot_digest(self.capture(session))
        fight.sleep_views[player.body] = (0.5,) * 6
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        fight.frost_hits[player.body] = {(player.body, 0): (0.2, fight.tick, False)}
        before = snapshot_digest(self.capture(session))
        fight.frost_hits[player.body][(player.body, 0)] = (0.2285, fight.tick, False)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        before = snapshot_digest(self.capture(session))
        fight.frost_hits[player.body][(player.body, 0)] = (0.2285, fight.tick, True)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        fight.freezes[player.body] = (fight.tick + 82, 0, 0)
        before = snapshot_digest(self.capture(session))
        fight.freezes[player.body] = (fight.tick + 82, 1, 0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        at = fight.tick * 16
        area = mei_blizzard.Area(1, player, player.body, tuple(player.mover.position),
                                 at, at + 4250, at, {player.body: at + 200})
        fight.blizzard.areas[area.entity] = area
        before = snapshot_digest(self.capture(session))
        area.frost_due[player.body] += 200
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_GRAPH_EXTENSION", ".")).is_file(),
                         "requires actual external B.O.B. metadata")
    def test_active_bob_owner_gun_and_expiry_survive_passive_snapshot(self):
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E0000000000200), now=100.8)
        actor = player.match.combat.companions.summon(player)
        self.assertIsNotNone(actor)
        first = snapshot_digest(self.capture(session))
        actor.until += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        first = snapshot_digest(self.capture(session))
        actor.charge_until += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        first = snapshot_digest(self.capture(session))
        actor.phase = "settle"
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        first = snapshot_digest(self.capture(session))
        actor.bot.pitch = 100
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        player.match.combat.impulses.queue(actor.bot, (0, 5, 20))
        first = snapshot_digest(self.capture(session))
        self.assertNotIn(actor.bot.entity, player.match.combat.impulses.npcs)
        actor.mover.velocity = (0, 1, 2)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_PVE_CONTROLLER_EXTENSION", ".")).is_file(),
                         "requires actual external NPC controller metadata")
    def test_actual_enforcer_controller_and_foreign_watchers_are_causal_state(self):
        directory = Path(os.environ["ORPHEUS_PVE_NPC_DIR"])
        bundle = graph.load_bundle(directory / "game_graphs_174.json.gz",
                                   os.environ["ORPHEUS_PVE_CONTROLLER_EXTENSION"])
        with mock.patch.object(graph, "_data", return_value=bundle):
            graph._records.cache_clear()
            graph.graph.cache_clear()
            try:
                session, _, players = self.scene()
                player = players[0]
                definition = next(item for item in npc.load_catalog(directory / "pve_npcs_174.json").values()
                                  if item.name == "Enforcer")
                bot = npc.spawn(player.match, definition, (0, 0, 8), team=1)
                context = bot.body_script.control_context
                self.assertIsNotNone(context)
                root = context.controller_component
                slot = root.entity_var(9134, create=False)
                self.assertTrue(slot.watchers)
                with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
                    before = snapshot_digest(self.capture(session))
                root.next_id += 1
                self.assertNotEqual(snapshot_digest(self.capture(session)), before)
                before = snapshot_digest(self.capture(session))
                context.position = expr.Vec3(0, 1, 8)
                self.assertNotEqual(snapshot_digest(self.capture(session)), before)
                before = snapshot_digest(self.capture(session))
                context.target_entity = player.body
                self.assertNotEqual(snapshot_digest(self.capture(session)), before)
                before = snapshot_digest(self.capture(session))
                slot.base = "stored mutation only; no getter/evaluation"
                self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            finally:
                graph._records.cache_clear()
                graph.graph.cache_clear()

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_PVE_CONTROLLER_EXTENSION", ".")).is_file(),
                         "requires actual external NPC controller metadata")
    def test_shared_target_controller_watches_are_passive_causal_qualified_storage(self):
        directory = Path(os.environ["ORPHEUS_PVE_NPC_DIR"])
        bundle = graph.load_bundle(directory / "game_graphs_174.json.gz",
                                   os.environ["ORPHEUS_PVE_CONTROLLER_EXTENSION"])
        with mock.patch.object(graph, "_data", return_value=bundle):
            graph._records.cache_clear()
            graph.graph.cache_clear()
            try:
                session, _, players = self.scene()
                player = players[0]
                host = player.controller_vm
                profile = ControllerFilterInputs(player.entity,
                                                 ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64)),
                                                 0, 0x40001, provenance="declared coupled-controller input")
                host.bind_filter_inputs(profile)
                definition = next(item for item in npc.load_catalog(directory / "pve_npcs_174.json").values()
                                  if item.name == "Assassin")
                bindings = []
                for x in (-2, 2):
                    bot = npc.spawn(player.match, definition, (x, 0, 8), team=1)
                    context = bot.body_script.control_context
                    context.set_target(player.entity, host=host)
                    context.advance(context.controller_component.frame + 1)
                    context.controller_component.run_now()
                    instance = next(item for item in context.controller_component.instances.values()
                                    if item.graph.index == 0x13C2)
                    watcher = instance.node_state(instance.graph.node(10))
                    self.assertTrue(watcher.active)
                    condition = instance.graph.node(10).field("m_condition")["m_filter"]["m_condition"]
                    self.assertTrue(filter_condition.evaluate(
                        instance, condition, player.entity, watcher=watcher))
                    bindings.append((context, watcher))
                slot = host.component.entity_var(14695)
                self.assertEqual(set(slot.watchers), {watcher for _, watcher in bindings})
                with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
                    before = snapshot_digest(self.capture(session))
                    slot.base = True
                    self.assertNotEqual(snapshot_digest(self.capture(session)), before)
                    before = snapshot_digest(self.capture(session))
                    profile.update(replicated_bits=0x40021)
                    self.assertNotEqual(snapshot_digest(self.capture(session)), before)
                first, second = bindings
                first[0].retire()
                self.assertTrue(first[0].retired)
                self.assertEqual(host.component.world.readers, [second[0]])
                self.assertNotIn(first[1], slot.watchers)
                self.assertNotIn(slot, first[1].watched)
                self.assertIn(second[1], slot.watchers)
                self.assertIn(slot, second[1].watched)
                snapshot_digest(self.capture(session))
                # Corrupt the reverse edge: exporting must fail, not drop it.
                host.component.world.readers.append(second[0])
                with self.assertRaisesRegex(ServerSnapshotError, "duplicate VM coupling"):
                    self.capture(session)
                host.component.world.readers.pop()
                host.retire()
                self.assertEqual(host.component.world.readers, [])
                self.assertIsNone(second[0].target_host)
                self.assertNotIn(second[1], slot.watchers)
                self.assertNotIn(slot, second[1].watched)
                snapshot_digest(self.capture(session))
            finally:
                graph._records.cache_clear()
                graph.graph.cache_clear()

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

    def test_sigma_barrier_ashe_hit_replays_through_encrypted_commands(self):
        if graph.entity_body(0x0400000000002D25) is None or not os.environ.get("ORPHEUS_SIGMA_COLLISION"):
            self.skipTest("requires explicit build 104319 Sigma barrier entity and collision descriptor")
        ashe, sigma = 0x02E0000000000200, 0x02E000000000023B
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "Ashe", ashe, 0, False), (2, "Sigma", sigma, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47120 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        match = session.server.matches[0]
        shooter, defender = match.players
        peers[0].message(21616, pick(shooter.entity, ashe), now=100.8)
        peers[1].message(21616, pick(defender.entity, sigma), now=100.8)
        shield = match.combat.sigma_barriers.actors[defender.body]
        peers[1].commands(defender.body_script.frame + 1, 12, 2, yaw=-32768)
        session.tick(100.992, missed=11)
        peers[1].commands(defender.body_script.frame + 1, 1, 0, yaw=-32768, now=100.992)
        session.tick(101.008)
        self.assertEqual(shield.mode, 3)
        self.assertEqual(shield.health, 700)
        vitals = match.combat.vitals.get(defender.body)
        before = vitals.total if vitals is not None else sum(pools.body_pools(sigma))
        peers[0].commands(shooter.body_script.frame + 1, 12, 1, now=101.008)
        self.assertEqual(shield.health, 660)
        self.assertEqual(match.combat.vitals[defender.body].total, before)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].combat
        self.assertEqual(copied.sigma_barriers.actors[defender.body].health, 660)
        self.assertEqual(copied.vitals[defender.body].total, before)

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
            with (mock.patch.object(mover_module, "MOVERS_PATH", path),
                  mock.patch.dict(os.environ)):
                # This fixture owns a synthetic base; a caller's fingerprinted
                # external extension belongs only to the caller's actual base.
                os.environ.pop("ORPHEUS_MOVER_EXTENSION", None)
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
