"""External mover bodies are immutable, complete and bound to exact base bytes."""

import gzip
import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from ow174.game.mover import MoverData, load_mover_bundle


class MoverExtensionTests(unittest.TestCase):
    def load(self, changes=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"0x1":{"stand_height":1.7}}'
            base = root / "base.json"
            base.write_bytes(raw)
            extra = {"format_version": 1, "base_mover_sha256": hashlib.sha256(raw).hexdigest(),
                     "movers": {"0x2": asdict(MoverData(stand_height=2.3, stand_radius=0.7))},
                     "mover_profiles": {"bob_charge": asdict(MoverData(run_forward=15))}}
            if changes:
                changes(extra)
            extension = root / "extra.gz"
            extension.write_bytes(gzip.compress(json.dumps(extra).encode()))
            result, profiles = load_mover_bundle(base, extension)
            self.assertEqual(profiles["bob_charge"].run_forward, 15)
            self.assertEqual(base.read_bytes(), raw)
            return result

    def test_body_geometry_adds_without_replacing_original(self):
        result = self.load()
        self.assertEqual(result[1].stand_height, 1.7)
        self.assertEqual((result[2].stand_height, result[2].stand_radius), (2.3, 0.7))

    def test_incompatible_or_malformed_additions_fail_closed(self):
        cases = [
            lambda extra: extra.update(base_mover_sha256="wrong"),
            lambda extra: extra.update(format_version=2),
            lambda extra: extra["movers"].update({"0x1": asdict(MoverData())}),
            lambda extra: extra["movers"]["0x2"].pop("stand_radius"),
            lambda extra: extra["movers"]["0x2"].update(unknown=1),
            lambda extra: extra["movers"]["0x2"].update(stand_height=float("nan")),
            lambda extra: extra["movers"]["0x2"].update(stand_height=-1),
            lambda extra: extra["movers"]["0x2"].update(ground_circle_rays=65),
            lambda extra: extra["movers"]["0x2"].update(ground_circle_rays=8.0),
            lambda extra: extra["movers"]["0x2"].update(character_collision=True),
            lambda extra: extra["mover_profiles"]["bob_charge"].update(run_forward=float("inf")),
            lambda extra: extra["mover_profiles"]["bob_charge"].pop("stand_radius"),
        ]
        for index, change in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.load(change)


if __name__ == "__main__":
    unittest.main()
