"""PVE controller parents and the shared hero-selection tree use the authored state indexes."""

import unittest
from unittest.mock import patch

from ow174.game import heroselect, statescript
from ow174.game.content import PRACTICE_RANGE_MODE, SOLDIER
from ow174.game.script import decode, graph

MODE = 0x023000000000FFFE
WRAPPER = 0xFFEE


def wrapper(duplicate=False):
    nodes = []
    for pos in range(4 if duplicate else 3):
        nodes.append({
            "pos": pos, "class": "STU_37D754C8", "uid": pos, "rid": pos, "remote": pos,
            "state": pos, "client_only": int(pos == 0), "server_only": int(pos == 1),
            "fields": {"m_36F7B64C": {"m_36F7B64C": {"m_C974E369": {
                "m_graph": "0x0580000000000C90",
            }}}},
        })
    return graph.Graph({
        "index": WRAPPER, "header": {"m_statesBitCount": 2}, "fields": {},
        "items": len(nodes), "containers": {},
        "nodes": nodes, "unknown_nodes": [], "states": list(range(len(nodes))),
        "entries": [], "remote": list(range(len(nodes))), "sync_vars": [],
    })


class PVEControllerTests(unittest.TestCase):
    def setUp(self):
        self.definition = {"pve": True, "teams": {"0": {"controller": f"0x058000000000{WRAPPER:04X}"}}}

    def test_parent_and_child_subscripts_decode_with_distinct_state_and_presence_indexes(self):
        source = wrapper()
        original = graph.graph

        def lookup(index):
            return source if index == WRAPPER else original(index)

        with (
            patch.object(graph, "mode_scripts", return_value=self.definition),
            patch.object(graph, "graph", side_effect=lookup),
            patch.object(decode, "graph_of", side_effect=lookup),
        ):
            controller = heroselect.controller_of(MODE)
            self.assertEqual((controller.parent.state, controller.parent.bit), (2, 0))
            payload = heroselect.controller_frame(controller, 100, True, skins=True, hero=SOLDIER)
            wire = statescript.chunk(payload, 0, 1)
            frame = decode.read_chunk(wire.getvalue(), wire.count)
        self.assertEqual({i: v.graph for i, v in frame.instances.items()}, {
            1: WRAPPER, 2: 0xC90, 3: 0x288A, 4: 0x288B, 5: 0x288D,
        })
        self.assertEqual(frame.instances[2].parent, (1, 2))
        self.assertEqual(frame.instances[3].parent, (2, 119))
        self.assertEqual(frame.instances[4].parent, (3, 0))
        self.assertEqual(frame.instances[5].parent, (3, 1))
        self.assertTrue(frame.instances[1].states[2][0])
        self.assertEqual(frame.instances[1].states[2][1]["child"], 2)
        self.assertEqual(frame.instances[2].states[119][1]["child"], 3)

    def test_legacy_runtime_and_non_pve_modes_keep_the_existing_controller(self):
        with patch.object(graph, "mode_scripts", return_value=None):
            self.assertIs(heroselect.controller_of(MODE), heroselect.PVP)
        with patch.object(graph, "mode_scripts", return_value={"pve": False}):
            self.assertIs(heroselect.controller_of(MODE), heroselect.PVP)
        self.assertIs(heroselect.controller_of(PRACTICE_RANGE_MODE), heroselect.PRACTICE)

    def test_a_known_pve_mode_cannot_silently_use_an_unrelated_controller(self):
        for definition, source, error in (
            ({"pve": True, "teams": {}}, wrapper(), "no team 0 controller"),
            (self.definition, None, "is unavailable"),
            (self.definition, wrapper(duplicate=True), "one verified"),
        ):
            with (
                self.subTest(error=error),
                patch.object(graph, "mode_scripts", return_value=definition),
                patch.object(graph, "graph", return_value=source),
                self.assertRaisesRegex(ValueError, error),
            ):
                heroselect.controller_of(MODE)


if __name__ == "__main__":
    unittest.main()
