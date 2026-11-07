"""PvE loading uses the authored remote readiness state and map mission binding."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node

from ow174.game import combat, pve, statescript
from ow174.game.script import decode, graph

MODE, MAP = 0x023000000000FFFE, 0x080000000000FFFE
ROOT, MISSION = 0xFFEE, 0xFFED
mission_script = graph.mission_script


def entrance_graph(index=ROOT, *, ready=True, remote=True, duplicate=False):
    nodes = [node(0, pve.SUBSCRIPT_CLASS, state=0, fields={
        "m_36F7B64C": {"m_36F7B64C": {"m_C974E369": {"m_graph": "0x0580000000000CB6"}}},
    })]
    if ready:
        nodes.append(node(1, pve.READY_CLASS, state=1, remote=0 if remote else -1))
    if duplicate:
        nodes.append(node(2, pve.READY_CLASS, state=2, remote=1))
    return make_graph(index, nodes, states=range(len(nodes)),
                      remote=range(1, len(nodes)) if remote else ())


class PVEEntranceTests(unittest.TestCase):
    def setUp(self):
        self.mode = {"pve": True, "graph": f"0x058000000000{ROOT:04X}", "scripted_loading": True}
        self.source = entrance_graph()
        self.original = graph.graph
        self.records = {ROOT: self.source}
        self.mission = None
        for patcher in (
            patch.object(graph, "mode_scripts", side_effect=lambda mode: self.mode),
            patch.object(graph, "mission_script", side_effect=lambda mode, map: self.mission),
            patch.object(graph, "graph", side_effect=self.lookup),
            patch.object(decode, "graph_of", side_effect=self.lookup),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def lookup(self, index):
        return self.records[index] if index in self.records else self.original(index)

    def read_frame(self):
        wire = statescript.chunk(combat.mode_frame({}, MODE, MAP), 0, 1)
        return decode.read_chunk(wire.getvalue(), wire.count)

    def test_direct_mode_frame_keeps_ui_and_activates_authored_ready_state(self):
        entrance = pve.entrance(MODE)
        self.assertEqual((entrance.graph.index, entrance.ui_state, entrance.ready_state), (ROOT, 0, 1))
        decoded = self.read_frame()
        self.assertEqual({i: v.graph for i, v in decoded.instances.items()}, {1: combat.MODE_UI, 2: ROOT})
        self.assertEqual(decoded.instances[2].states[1], (True, {}))
        self.assertEqual(decoded.instances[1].states[21], (True, {}))

    def test_missing_nonremote_and_ambiguous_ready_states_cannot_unlock_loading(self):
        for source in (
            entrance_graph(ready=False), entrance_graph(remote=False), entrance_graph(duplicate=True),
        ):
            with self.subTest(source=source):
                self.records[ROOT] = source
                self.assertIsNone(pve.entrance(MODE))

    def test_nonscripted_pve_can_enter_without_a_ready_state(self):
        self.mode["scripted_loading"] = False
        self.records[ROOT] = entrance_graph(ready=False)
        self.assertIsNone(pve.entrance(MODE).ready_state)

    def test_map_configured_mission_keeps_the_real_mode_root(self):
        self.records[ROOT] = make_graph(ROOT)
        self.records[MISSION] = entrance_graph(MISSION)
        self.assertIsNone(pve.entrance(MODE, MAP))
        self.mission = {"mode_graph": self.mode["graph"], "graph": f"0x058000000000{MISSION:04X}"}
        entrance = pve.entrance(MODE, MAP)
        self.assertEqual(entrance.mode_graph.index, ROOT)
        decoded = self.read_frame()
        self.assertEqual({i: v.graph for i, v in decoded.instances.items()}, {
            1: combat.MODE_UI, 2: ROOT, 3: MISSION,
        })
        self.assertEqual(decoded.instances[3].states[1], (True, {}))

    def test_mission_cannot_replace_an_unrelated_mode_or_name_an_invalid_graph(self):
        for reference, parent in ((f"0x058000000000{MISSION:04X}", "0x0580000000001234"),
                                  ("0x0400000000000001", self.mode["graph"]),
                                  ("0x0580000000010000", self.mode["graph"]),
                                  ("0x0580000000000000", self.mode["graph"]),
                                  ("invalid", self.mode["graph"])):
            with self.subTest(reference=reference, parent=parent):
                self.mission = {"mode_graph": parent, "graph": reference}
                self.assertIsNone(pve.entrance(MODE, MAP))

    def test_missing_or_non_pve_metadata_preserves_legacy_frame(self):
        baseline = combat.mode_frame({})
        for mode in (None, {"pve": False}):
            with self.subTest(mode=mode):
                self.mode = mode
                current = combat.mode_frame({}, MODE, MAP)
                self.assertEqual((current.count, current.getvalue()), (baseline.count, baseline.getvalue()))

    def test_mission_accessor_is_scoped_to_both_map_and_mode(self):
        binding = {"graph": "0x0580000000001234"}
        with patch.object(graph, "_data", return_value={"mission_scripts": {
            f"0x{MAP:016X}": {f"0x{MODE:016X}": binding},
        }}):
            self.assertIs(mission_script(MODE, MAP), binding)
            self.assertIsNone(mission_script(MODE + 1, MAP))
            self.assertIsNone(mission_script(MODE, MAP + 1))


if __name__ == "__main__":
    unittest.main()
