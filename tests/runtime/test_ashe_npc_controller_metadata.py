"""Execute extracted NPC AI-root bootstrap without pretending the control bridge exists."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import graph, runtime
from ow174.game.script.driver import create_initial_graphs

BASE = Path(os.environ.get("ORPHEUS_PVE_NPC_DIR", ".")) / "game_graphs_174.json.gz"
EXTRA = Path(os.environ.get("ORPHEUS_PVE_CONTROLLER_EXTENSION", "."))


@unittest.skipUnless(BASE.is_file() and EXTRA.is_file(), "requires explicit actual NPC AI-root metadata")
class NpcControllerMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = graph.load_bundle(BASE, EXTRA)
        cls.bundle_patch = patch.object(graph, "_data", return_value=cls.bundle)
        cls.bundle_patch.start()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    @classmethod
    def tearDownClass(cls):
        cls.bundle_patch.stop()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def test_all_five_body_associations_retain_separate_actual_ai_roots(self):
        for body, ai in ((0x1D40, 0x1D41), (0x2279, 0x227A), (0x1E7D, 0x1E7E),
                         (0x1EBD, 0x1EBE), (0x1EBB, 0x1EBC)):
            with self.subTest(body=hex(body)):
                association = graph.npc_controller(0x0400000000000000 | body)
                self.assertEqual(int(association["definition"], 16), 0x0400000000000000 | ai)
                self.assertEqual(len(association["source_sha256"]), 64)
                record = graph.entity_body(int(association["definition"], 16))
                self.assertIn(0x142A, [row["graph"] for row in record["graphs"]])
                self.assertTrue(all(graph.graph(row["graph"]) is not None for row in record["graphs"]))

    def test_enforcer_authored_ai_root_bootstrap_writes_eligibility_not_a_forced_predicate(self):
        component = runtime.Component(0xC1000001, world=runtime.World(0x02E00000000001CE,
                                                                    PRACTICE_RANGE_MODE))
        component.frame, component.now = 1000, 16000
        record = graph.entity_body(0x040000000000227A)
        for instance in create_initial_graphs(component, record):
            instance.init()
        component.run_frame(1000)
        self.assertIsNone(component.fault)
        slot = component.entity_var(9134, create=False)
        self.assertIsNotNone(slot)
        self.assertTrue(slot.value())
        sources = [(link.state.instance.graph.index, link.state.node.pos) for link in slot.links]
        self.assertIn((0x13EA, 31), sources)
        # This proves an actual AI-root writer only. No body/controller/root
        # query relationship or damaging Enforcer/Assassin attack is mounted.


if __name__ == "__main__":
    unittest.main()
