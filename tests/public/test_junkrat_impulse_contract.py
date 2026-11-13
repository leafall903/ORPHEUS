"""Synthetic, language-neutral ring boundary contract."""
import json
from pathlib import Path
import unittest

from ow174.game import junkrat


class JunkratImpulseContractTests(unittest.TestCase):
    def test_native_independent_axis_ring_contract(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/junkrat-impulse-rings-v1.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(distance=case["distance"]):
                self.assertEqual(junkrat.impulse_scales(case["rings"], case["distance"]),
                                 tuple(case["expected"]))
