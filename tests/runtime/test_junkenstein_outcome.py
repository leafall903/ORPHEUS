"""Terminal wire/ACK boundary only; these are not natural match outcome tests."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ow174.game import statescript, world
from ow174.game.script import graph
from ow174.game.server import Client


def peer():
    environment = SimpleNamespace(wall_time=lambda **_: 0.0)
    player = SimpleNamespace(
        name="Fixture",
        entity=0xA0000100,
        body_script=None,
        mode_script=statescript.Stream(world.GAME_MODE_ENTITY),
    )
    client = Client(SimpleNamespace(environment=environment), ("127.0.0.1", 1), 1, None, player)
    client._mode_create_acked = True
    player.mode_script.last = player.mode_script.delivered = player.mode_script.data_last = 1
    return client


class JunkensteinOutcomeTests(unittest.TestCase):
    def test_outcome_record_contains_native_winner_and_time_with_the_callout(self):
        client = peer()
        model = graph.graph(0x0C8C)
        frame = statescript.remote_delta([], {}, {2: model}, callout=(2, 2, 265))
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[2] / "fixtures/behavior/junkenstein-outcome-fields-v1.json"
            ).read_text()
        )
        result = fixture["winner"], fixture["elapsed_seconds"]
        client.queue_scene_mode_frame(frame, full=False, match_result=result)
        (record,) = client.entities
        self.assertEqual(record.op, world.OP_UPDATE)
        self.assertIsNotNone(record.chunk)
        # Native C114 has20fields; only7(s32 winner) and9(f32 time) are present.
        expected = bytes.fromhex(fixture["expected_component_update_hex"])
        self.assertEqual(record.build(0).getvalue(), expected)
        self.assertTrue(record.resend)
        self.assertIs(record.stream, client.player.mode_script)

    def test_fragment_loss_keeps_result_with_final_fragment_until_acked(self):
        client = peer()
        model = graph.graph(0x0C8C)
        frame = statescript.remote_delta([], {}, {2: model}, callout=(2, 2, 268))
        with mock.patch("ow174.game.server.MODE_FRAME_PART_BITS", 16):
            client.queue_scene_mode_frame(frame, full=False, match_result=(0, 32.0))
        records = client.entities[:]
        self.assertGreater(len(records), 1)
        self.assertTrue(all(not item.op for item in records[:-1]))
        last = records[-1]
        self.assertEqual(last.op, world.OP_UPDATE)
        original = last.chunk.getvalue(), last.build(0).getvalue()
        client.entities.clear()
        client.in_flight = {index: ([], [record]) for index, record in enumerate(records, 1)}
        client.acked(len(records), 0xFFFFFFFF)
        self.assertEqual(client.player.mode_script.delivered, 1)
        self.assertFalse(client.scene_mode_writable)
        client.acked(len(records) + 40, 0)
        self.assertEqual(client.entities, [last])
        self.assertEqual((last.chunk.getvalue(), last.build(0).getvalue()), original)
        client.entities.clear()
        client.in_flight[len(records) + 41] = ([], [last])
        client.acked(len(records) + 42, 1)
        self.assertEqual(client.player.mode_script.delivered, 2)
        self.assertTrue(client.scene_mode_writable)
        self.assertFalse(client._mode_fragments)

    def test_original_winner_expression_and_relative_scene_clock_feed_result(self):
        from ow174.game import junkenstein_scene
        from ow174.game.content import JUNKENSTEIN_MODES
        from ow174.game.script import runtime, scene
        from tests.public.test_script_startup import definition

        native_world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        native_world.bind(runtime.GAME_MODE_ENTITY)
        host = junkenstein_scene.JunkensteinSceneHost(native_world)
        self.addCleanup(host.close)
        component = host.add(runtime.GAME_MODE_ENTITY, definition())
        host.bind_match(SimpleNamespace(players=[]), None)
        self.assertTrue(host.advance(100))
        model = graph.graph(0x0C8C)
        root = component.create(model, init=False)
        root.initialized = True
        # Synthetic terminal-condition inputs exercise the original winner
        # expression in isolation; no match outcome or gate is forced.
        root.write((runtime.INSTANCE, 8617), True)
        root.write((runtime.INSTANCE, 11776), False)
        component.frame, component.now = 1100, 17600
        with mock.patch.object(root, "follow", return_value=True):
            root.run_node(model.node(20), None)
        self.assertEqual(root.find_var(runtime.INSTANCE, 7525).value(), 0)
        root.run_node(model.node(265), None)
        self.assertEqual(host.match_result, (0, 16.0))
        from ow174.game.content import JUNKENSTEIN_MAP
        from ow174.game.match import Match

        owner = Match.__new__(Match)
        owner.game_map = SimpleNamespace(map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        owner.scene_host = host
        with mock.patch("ow174.game.match.world.create", wraps=world.create) as create:
            owner._game_mode_create(0)
        fields = create.call_args.args[2][114]
        self.assertEqual((fields[1], fields[7], fields[9]), (JUNKENSTEIN_MODES[0], 0, 16.0))
        component.frame, component.now = 1200, 19200
        root.run_node(model.node(265), None)
        self.assertEqual(host.match_result, (0, 16.0))

    def test_retired_mode_generation_does_not_accept_old_result_ack(self):
        client = peer()
        model = graph.graph(0x0C8C)
        frame = statescript.remote_delta([], {}, {2: model}, callout=(2, 2, 265))
        client.queue_scene_mode_frame(frame, full=False, match_result=(0, 16.0))
        (record,) = client.entities
        client.entities.clear()
        client.in_flight[1] = ([], [record])
        client.queue_entities([world.EntityUpdate(world.GAME_MODE_ENTITY, world.OP_DESTROY)])
        client.acked(2, 1)
        self.assertEqual(client.player.mode_script.delivered, 0)
        self.assertFalse(client._mode_create_acked)
        self.assertNotIn(record, client.entities)
