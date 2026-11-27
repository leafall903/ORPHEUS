import copy
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "build_runtime_state_metadata.py"
BODY = "0x04000000000000AB"
OTHER_BODY = "0x04000000000000AC"
STOCK = "0x0000000000000000"
SKIN = "0x0A60000000000010"
POINT = "0x0DC000000000001A"
OTHER_POINT = "0x0DC000000000001B"


class ModelHardpointImportTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source = root / "input.gz"
        self.export = root / "positions.json"
        self.output = root / "enriched.gz"
        self.row = {"skin": STOCK, "hardpoints": [POINT, OTHER_POINT]}
        self.bundle = {
            "version": 1,
            "graphs": [{"value": 0.12345678901234567}],
            "bodies": {BODY: {"model_hardpoints": [self.row]}, OTHER_BODY: {}},
        }
        self.position = [1.1234567890123457, -0.015625, 2.75]
        self.stock = {**self.row, "reference_positions": {POINT: self.position}}

    def run_import(self, rows, output=None, build=104319):
        with gzip.open(self.source, "wt", encoding="utf-8") as handle:
            json.dump(self.bundle, handle)
        self.export.write_text(json.dumps({"build": build, "bodies": rows}), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--graph-bundle", str(self.source),
             "--model-hardpoints", str(self.export), "--output", str(output or self.output)],
            capture_output=True, text=True,
        )

    def test_adds_only_matching_positions_and_writes_reproducible_new_output(self):
        self.row["reference_positions"] = {OTHER_POINT.lower(): [0.5, 0, 0]}
        skin = {**self.stock, "skin": SKIN, "reference_positions": {POINT: [5, 0, 0]}}
        rows = {BODY.lower(): [self.stock, skin]}
        run = self.run_import(rows)
        self.assertEqual(run.returncode, 0, run.stderr)
        expected = copy.deepcopy(self.bundle)
        expected["bodies"][BODY]["model_hardpoints"][0]["reference_positions"][POINT] = self.position
        expected["bodies"][BODY]["model_hardpoints"].append(skin)
        self.assertEqual(json.loads(gzip.decompress(self.output.read_bytes())), expected)
        previous = self.output.read_bytes()
        alternate = self.output.with_name("second.gz")
        self.assertEqual(self.run_import(rows, alternate).returncode, 0)
        self.assertEqual(previous, alternate.read_bytes())
        for occupied in (self.source, self.export, self.output):
            self.assertNotEqual(self.run_import(rows, occupied).returncode, 0)
        self.assertEqual(json.loads(gzip.decompress(self.source.read_bytes())), self.bundle)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_invalid_or_conflicting_evidence_leaves_no_output(self):
        self.row["reference_positions"] = {POINT: self.position}
        for rows, build in [
            ({BODY: [self.stock]}, 104318),
            ({"0x0400000000000099": [self.stock]}, 104319),
            ({BODY: [self.stock], BODY.lower(): [self.stock]}, 104319),
            ({BODY: [self.stock, self.stock]}, 104319),
            ({BODY: [{**self.stock, "hardpoints": [POINT]}]}, 104319),
            ({BODY: [{**self.stock, "reference_positions": {POINT: [0, 0, 0]}}]}, 104319),
            ({BODY: [{**self.stock, "reference_positions": {OTHER_POINT: [True, 0, 0]}}]}, 104319),
            ({BODY: [{**self.stock, "reference_positions": {OTHER_POINT: [float("nan"), 0, 0]}}]}, 104319),
            ({BODY: [{**self.stock, "reference_positions": {"0x0DC000000000002C": [0, 0, 0]}}]}, 104319),
        ]:
            with self.subTest(rows=rows, build=build):
                self.assertNotEqual(self.run_import(rows, build=build).returncode, 0)
                self.assertFalse(self.output.exists())

    def test_first_inventory_for_body_without_model_metadata(self):
        result = self.run_import({OTHER_BODY: [self.stock]})
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads(gzip.decompress(self.output.read_bytes()))
        self.assertEqual(merged["bodies"][OTHER_BODY]["model_hardpoints"], [self.stock])
        self.assertEqual(merged["bodies"][BODY], self.bundle["bodies"][BODY])


if __name__ == "__main__":
    unittest.main()
