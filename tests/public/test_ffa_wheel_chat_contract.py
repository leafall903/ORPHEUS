import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import notices, statescript
from ow174.game.script import decode, expr
from ow174.game.script.graph import Graph
from tests.script_fixtures import node

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class FreeForAllWheelChatContractTests(unittest.TestCase):
    def test_original_chat_nodes_are_delivered_only_to_the_senders_controller(self):
        contract = json.loads((FIXTURES / "ffa-wheel-chat-v1.json").read_text(encoding="utf-8"))
        inputs, expected = contract["input"], contract["expected"]
        graph_index = int(inputs["graph_index"], 16)
        variable = expected["text_variable"]
        text = expr.Map(((expected["text_key"], expr.Asset(int(expected["acknowledge_text"], 16))),))
        chat_nodes = [node(position, "STU_7106C356") for position in expected["nodes"]]
        for number, action in enumerate(chat_nodes):
            action.update(rid=number, remote=number)
        definition = Graph({
            "index": graph_index,
            "header": {"m_nodesBitCount": 1, "m_remoteSyncNodesBitCount": 1,
                       "m_statesBitCount": 0, "m_syncVarsBitCount": 1},
            "fields": {}, "items": len(chat_nodes), "containers": {}, "nodes": chat_nodes,
            "unknown_nodes": [], "states": [], "entries": [], "remote": expected["nodes"],
            "sync_vars": [[variable, expr.INSTANCE, 0]],
        })
        sent, rival_sent = [], []
        controller = int(inputs["controller"], 16)
        player = SimpleNamespace(
            slot=0, has_body=True, body=int(inputs["pawn"], 16), entity=controller,
            client=SimpleNamespace(controller_ready=True, queue_entities=sent.extend),
            script=statescript.Stream(controller),
        )
        rival = SimpleNamespace(
            slot=1, client=SimpleNamespace(controller_ready=True, queue_entities=rival_sent.extend),
            script=statescript.Stream(int(inputs["rival_controller"], 16)),
        )
        match = SimpleNamespace(players=[player, rival], _newest_frame=lambda _: inputs["command_frame"])
        queue = notices.Notices(match)
        hello, acknowledge = expected["nodes"]
        queue.wheel_chat(player, graph_index, hello, ())
        queue.wheel_chat(player, graph_index, acknowledge, (((1, variable), (text, ())),))
        with patch.object(notices.graph, "graph", return_value=definition), \
                patch.object(decode, "graph_of", return_value=definition):
            queue.update(1.0)
            queue.update(2.0)
            self.assertEqual(len(sent), expected["actions_before_controller_ack"])
            player.script.arrived(sent[0].chunk_last)
            queue.update(3.0)
            client = decode.ClientBody()
            events = zip(sent, expected["chunk_stamps"], expected["nodes"], strict=True)
            for update, stamp, position in events:
                self.assertEqual(update.entity, controller)
                self.assertTrue(update.resend)
                frame = client.apply(update)
                self.assertTrue(frame.owner)
                self.assertEqual(frame.actions, [(stamp, expected["instance"], position)])
            self.assertEqual(client.vars[(expected["instance"], variable)], text)
        self.assertEqual(rival_sent, [])
