import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_validation import GRAPH, PRACTICE_MODE, validate_pack, verify_manifest


class RuntimeValidationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "runtime"
        (self.root / "data").mkdir(parents=True)
        source = "0x058000000000002A"
        team = {"controller": source, "body": source}
        self.graphs = {
            "version": 1,
            "graphs": [{"index": 42}],
            "mode_scripts": {PRACTICE_MODE: {"graph": source, "teams": {"0": team, "1": team}}},
            "mode_variables": {PRACTICE_MODE: {"19": False}},
            "mode_rulesets": [{"mode": PRACTICE_MODE, "rules": [{"id": 30, "value": 12}]}],
            "bodies": {f"0x0400{i:012X}": {"target_tags": ["0x0D60000000000012"]} for i in range(1, 33)},
        }
        self.extra = self.root / "extra.bin"
        self.extra.write_bytes(b"fixture")
        self.write_graphs(self.graphs)

    def write_graphs(self, graphs):
        graph = self.root / GRAPH
        graph.write_bytes(gzip.compress(json.dumps(graphs).encode()))
        self.entries = {
            GRAPH: hashlib.sha256(graph.read_bytes()).hexdigest(),
            self.extra.name: hashlib.sha256(self.extra.read_bytes()).hexdigest(),
        }
        self.write_manifest(self.entries)

    def write_manifest(self, entries):
        (self.root / "manifest.json").write_text(json.dumps({
            "schema": "orpheus-runtime-copy-v1", "sha256": entries,
        }), encoding="utf-8")

    def test_checks_digests_and_enriched_practice_graph(self):
        self.assertIsNone(validate_pack(self.root))
        self.extra.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch for extra.bin"):
            validate_pack(self.root)
        self.extra.unlink()
        with self.assertRaises(ValueError):
            validate_pack(self.root)

    def test_supplement_manifest_is_independent_of_gameplay_metadata(self):
        (self.root / GRAPH).unlink()
        entries = {self.extra.name: self.entries[self.extra.name]}
        self.write_manifest(entries)
        self.assertEqual(verify_manifest(self.root), entries)
        with self.assertRaisesRegex(ValueError, "must include"):
            validate_pack(self.root)
        self.extra.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verify_manifest(self.root)
        self.write_manifest({})
        with self.assertRaisesRegex(ValueError, "nonempty"):
            verify_manifest(self.root)

    def test_finished_pack_requires_checksums_for_all_server_payloads(self):
        (self.root / "local-notes.txt").write_text("operator notes", encoding="utf-8")
        self.assertIsNone(validate_pack(self.root))
        for name in ("data/map_environment_174.json", "data/junkenstein/junkenstein-npcs.json",
                     "bnet/descriptors.pb", "collision/0688.rays.col"):
            with self.subTest(name=name):
                payload = self.root / name
                payload.parent.mkdir(parents=True, exist_ok=True)
                payload.write_bytes(b"optional test input")
                self.assertEqual(verify_manifest(self.root), self.entries)
                with self.assertRaisesRegex(ValueError, f"unlisted runtime payload: {name}"):
                    validate_pack(self.root)
                self.entries[name] = hashlib.sha256(payload.read_bytes()).hexdigest()
                self.write_manifest(self.entries)
                self.assertIsNone(validate_pack(self.root))

    def test_missing_checksum_and_invalid_graph_metadata(self):
        self.write_manifest({self.extra.name: self.entries[self.extra.name]})
        with self.assertRaisesRegex(ValueError, "must include"):
            validate_pack(self.root)
        for field, value in [
            ("mode_scripts", {}),
            ("mode_scripts", {PRACTICE_MODE: {"graph": "0x058000000000002B", "teams": {}}}),
            ("mode_variables", {PRACTICE_MODE: {}}),
            ("mode_rulesets", [{"mode": PRACTICE_MODE, "rules": []}]),
            ("bodies", {}),
            ("bodies", {**self.graphs["bodies"], "0x0400000000000001": {"target_tags": []}}),
        ]:
            with self.subTest(field=field, value=value):
                self.write_graphs({**self.graphs, field: value})
                with self.assertRaises(ValueError):
                    validate_pack(self.root)

    def test_rejects_path_aliases_and_traversal(self):
        outside = self.root.parent / "outside"
        outside.write_bytes(b"fixture")
        for name in ["../outside", "/outside", "C:/outside", "C:outside", "data/../outside",
                     r"..\outside", r"\\host\share", "./extra.bin", "extra.bin:stream", "NUL"]:
            with self.subTest(name=name):
                self.write_manifest({**self.entries, name: self.entries[self.extra.name]})
                with self.assertRaisesRegex(ValueError, "invalid manifest path"):
                    validate_pack(self.root)

    def test_directory_link_cannot_read_outside_runtime(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "extra.bin").write_bytes(b"fixture")
        link = self.root / "data/linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if os.name != "nt":
                self.skipTest(f"symlinks unavailable: {error}")
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "New-Item -ItemType Junction -Path $env:ORPHEUS_TEST_LINK "
                 "-Target $env:ORPHEUS_TEST_TARGET -ErrorAction Stop | Out-Null"],
                env={**os.environ, "ORPHEUS_TEST_LINK": str(link), "ORPHEUS_TEST_TARGET": str(outside)},
                check=True, capture_output=True,
            )
        self.write_manifest({**self.entries, "data/linked/extra.bin": self.entries[self.extra.name]})
        with self.assertRaisesRegex(ValueError, "inside the runtime"):
            validate_pack(self.root)
        self.write_manifest(self.entries)
        self.assertEqual(verify_manifest(self.root), self.entries)
        with self.assertRaisesRegex(ValueError, "payload directory escapes"):
            validate_pack(self.root)


if __name__ == "__main__":
    unittest.main()
