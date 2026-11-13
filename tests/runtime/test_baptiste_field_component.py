"""Actual Field deployment owns one definition-backed, replicated component47."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_baptiste_field as fixture
import test_server_snapshot as server_fixture
from test_game_packet_budget import MemorySocket

from ow174.game import baptiste, body_vm_snapshot, observers, world
from ow174.game.link import LinkCipher
from ow174.game.script import expr, graph, runtime
from ow174.game.server import Client, GameServer

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "fixtures/behavior"
                       / "baptiste-field-component-v1.json").read_text(encoding="utf-8"))


class BaptisteFieldComponentTests(unittest.TestCase):
    def make(self):
        if graph.entity_script_component(CONTRACT["definition"]) is NotImplemented:
            self.skipTest("original29A9 component definition input required")
        return fixture.BaptisteFieldTests().make()

    def test_actual_cast_registers_original_graph_order_pose_and_retires_same_vm(self):
        run, owner, _, _, fight, _, _ = self.make()
        actor = fixture.BaptisteFieldTests().cast(run, owner, fight)
        self.assertIsInstance(actor.body_script, baptiste.FieldScript)
        component = actor.body_script.component
        self.assertIs(run.match.body_components[actor.entity], component)
        self.assertEqual([i.graph.index for i in component.instances.values()], CONTRACT["graphs"])
        deployed = next(i for i in component.instances.values() if i.graph.index == 0x1762)
        self.assertEqual(deployed.id, 5)
        self.assertEqual(deployed.source_entity(), actor.source_body)
        self.assertEqual(tuple(deployed.find_var(0, 17085).value()), tuple(map(expr.f32, actor.position)))
        self.assertEqual(tuple(component.world.position(actor.entity)), actor.position)
        self.assertIsNone(component.fault)
        self.assertGreater(observers.SceneBody(component).initial[0].count, 0)
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("export ran VM")):
            exported = body_vm_snapshot.export_component_vm(component)
            self.assertTrue(body_vm_snapshot.snapshot_digest(exported))
        fight.destroy_deployable(actor.entity)
        self.assertNotIn(actor.entity, run.match.body_components)
        self.assertFalse(component.instances)
        self.assertIsNone(component.world.match)
        self.assertNotIn(actor.entity, fight.baptiste.fields)

    def test_existing_and_loading_viewers_bind_same_component_after_create_ack(self):
        run, owner, _, _, fight, _, _ = self.make()
        server = GameServer()
        server.sock = MemorySocket()
        for number, viewer in enumerate(run.players, 1):
            client = Client(server, ("memory.test", 45000 + number), number,
                            LinkCipher(bytes([number]) * 32, bytes([number + 10]) * 32), viewer)
            viewer.client = client
            client.open = True
        actor = fixture.BaptisteFieldTests().cast(run, owner, fight)
        component = actor.body_script.component
        for viewer in run.players:
            client = viewer.client
            stream = client.bot_stream(actor.entity)
            self.assertIsNotNone(stream)
            self.assertIs(client._bot_scripts[actor.entity].source.component, component)
            self.assertIsNone(stream.base)
            self.assertFalse(any(u.entity == actor.entity and u.chunk is not None for u in client.entities))
            client._ack_entity_create(actor.entity)
            self.assertTrue(any(u.entity == actor.entity and u.chunk is not None for u in client.entities))
        # A fresh loading connection replays the existing actor and subsequently
        # binds its existing VM, without allocating a second deployment graph.
        late = run.players[-1]
        client = Client(server, ("memory.test", 45999), 99,
                        LinkCipher(b"a" * 32, b"b" * 32), late)
        late.client = client
        client.open = True
        before = tuple(component.instances)
        client.queue_entities(run.match.spawn_updates(late))
        fight.baptiste._sync_fields()
        self.assertEqual(tuple(component.instances), before)
        self.assertIs(client._bot_scripts[actor.entity].source.component, component)
        client._ack_entity_create(actor.entity)
        creates = [u for u in client.entities if u.entity == actor.entity and u.op == world.OP_CREATE]
        self.assertEqual(len(creates), 1)
        fight.destroy_deployable(actor.entity)
        self.assertIsNone(client.bot_stream(actor.entity))
        self.assertIsNone(client.actor_generation(actor.entity))

    def test_full_server_export_keeps_actual_field_component_source(self):
        if graph.entity_script_component(CONTRACT["definition"]) is NotImplemented:
            self.skipTest("original29A9 component definition input required")
        helper = server_fixture.ServerSnapshotTests()
        session, peers, players = helper.scene()
        player, = players
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": baptiste.HERO}))
        peers[0].commands(player.body_script.frame + 1, 60, 16, pitch=8192)
        session.tick(101.9, missed=65)
        actor, = player.match.combat.deployables.values()
        component = actor.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1762)
        source = instance.descriptor_source_entity
        self.assertEqual(source, actor.source_body)
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("export ran VM")):
            initial = server_fixture.snapshot_digest(helper.capture(session))
            instance.descriptor_source_entity = None
            self.assertNotEqual(server_fixture.snapshot_digest(helper.capture(session)), initial)
            instance.descriptor_source_entity = source
            self.assertEqual(server_fixture.snapshot_digest(helper.capture(session)), initial)
