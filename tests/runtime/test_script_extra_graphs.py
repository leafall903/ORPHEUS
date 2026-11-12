"""Missing entity/graph inputs can be supplied without altering canonical data."""

import copy
import unittest

from ow174.game.script import graph


class ExtraGraphBundleTests(unittest.TestCase):
    def setUp(self):
        self.base = {"graphs": [{"index": 1, "fields": {}}], "bodies": {}}

    def test_additions_and_identical_records_preserve_canonical_input(self):
        before = copy.deepcopy(self.base)
        extra = {"graphs": [{"index": 1, "fields": {}}, {"index": 2, "fields": {}}],
                 "entities": {"0x0400000000002658": {"graphs": [{"graph": 2}]}}}
        result = graph.merge_extra_bundle(self.base, extra)
        self.assertEqual([r["index"] for r in result["graphs"]], [1, 2])
        self.assertEqual(result["entities"], extra["entities"])
        self.assertEqual(self.base, before)

    def test_conflicting_graphs_and_entity_records_are_rejected(self):
        for extra in ({"graphs": [{"index": 1, "fields": {"changed": True}}]},
                      {"graphs": [{"index": True}]},
                      {"graphs": [{"index": 2}, {"index": 2}]},
                      {"bodies": {"replacement": {}}},
                      {"entities": {"not-a-guid": {}}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                graph.merge_extra_bundle(self.base, extra)
        self.base["entities"] = {"0x0400000000002658": {"graphs": []}}
        with self.assertRaises(ValueError):
            graph.merge_extra_bundle(self.base, {"entities": {
                "0x0400000000002658": {"graphs": ["changed"]}}})


if __name__ == "__main__":
    unittest.main()
