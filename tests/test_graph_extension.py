"""External graph extensions bind to one base and cannot replace existing metadata."""

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game.script import graph


class GraphExtensionTests(unittest.TestCase):
    def load(self, extra, *, base=None):
        base = base or {"graphs": [{"index": 1}], "entities": {"existing": {}}, "bodies": {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = gzip.compress(json.dumps(base).encode())
            (root / "base.gz").write_bytes(raw)
            extra = {"format_version": 1, "base_sha256": hashlib.sha256(raw).hexdigest(), **extra}
            (root / "extra.gz").write_bytes(gzip.compress(json.dumps(extra).encode()))
            with patch.object(graph, "GRAPHS_PATH", root / "base.gz"), patch.dict(
                    os.environ, {"ORPHEUS_GRAPH_EXTENSION": str(root / "extra.gz")}):
                result = graph.load_bundle(root / "base.gz", root / "extra.gz")
            self.assertEqual((root / "base.gz").read_bytes(), raw)
            return result

    def test_additive_extension_leaves_base_file_unchanged(self):
        result = self.load({"graphs": [{"index": 2}], "entities": {"new": {"graphs": []}}})
        self.assertEqual([row["index"] for row in result["graphs"]], [1, 2])
        self.assertEqual(set(result["entities"]), {"existing", "new"})

    def test_wrong_base_and_replacement_are_rejected(self):
        for extra in (
            {"base_sha256": "wrong", "graphs": [], "entities": {}},
            {"graphs": [{"index": 1}], "entities": {}},
            {"graphs": [{"index": 2}, {"index": 2}], "entities": {}},
            {"graphs": [], "entities": {"existing": {}}},
            {"graphs": [{"index": 2, "target": "0x0580000000000003"}], "entities": {}},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.load(extra)

    def test_controller_associations_require_real_distinct_present_entities(self):
        body, controller = "0x0400000000002279", "0x040000000000227A"
        base = {"graphs": [], "entities": {body: {}}, "bodies": {}}
        extra = {"graphs": [], "entities": {controller: {}},
                 "npc_controllers": {body: {"definition": controller}}}
        self.assertEqual(self.load(extra, base=base)["npc_controllers"], extra["npc_controllers"])
        for source, target in ((body, body), (body, "missing"), (body, {}),
                               ("0x0400000000001111", controller)):
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                self.load({**extra, "npc_controllers": {source: {"definition": target}}}, base=base)
        with self.assertRaises(ValueError):
            self.load(extra, base={**base, "npc_controllers": {body: {"definition": controller}}})


if __name__ == "__main__":
    unittest.main()
