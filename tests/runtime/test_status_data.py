"""External status bundle validation; loading is not status acceptance."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from ow174.game.script import status_data


class StatusDataTests(unittest.TestCase):
    def load(self, **changes):
        data = {"status_supplement_version": 1, "target_build": "1.74.0.0.104319",
                "graphs": [], "status_definitions": {}}
        data.update(changes)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json.gz"
            path.write_bytes(gzip.compress(json.dumps(data).encode()))
            return status_data.load(str(path))

    def test_empty_declared_supplement_remains_explicit(self):
        self.assertEqual(self.load()["status_definitions"], {})

    def test_wrong_build_or_format_does_not_enter_runtime(self):
        for changes in ({"target_build": "other"}, {"status_supplement_version": 2}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "target build104319"):
                self.load(**changes)

    def test_duplicate_or_invalid_graph_indexes_are_rejected(self):
        for rows in ([{"index": 1}, {"index": 1}], [{"index": True}], [{"index": 0}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "graph index"):
                self.load(graphs=rows)

    def test_status_definition_requires_its_real_type_and_identity(self):
        for key, value in (("0x04A0000000000000", {"$": "STU_76F2C784"}),
                           ("0x0580000000000267", {"$": "STU_76F2C784"}),
                           ("0x04A00000000000A4", {"$": "unknown"})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load(status_definitions={key: value})
