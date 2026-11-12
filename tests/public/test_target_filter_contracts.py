"""The target-filter contract: which entities a filter's six masks admit."""

import json
import os
import unittest
from pathlib import Path

from ow174.game.combat import filter_admits

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class TargetFilterContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((FIXTURES / "target-filter-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["case_id"], "target-filter.entity-and-relation-masks.v1")
        self.assertEqual(self.contract["target_build"], 104319)
        self.assertEqual(self.contract["evidence"], "client-code-reference")

    def test_every_case_has_one_expected_answer(self):
        cases = [case["id"] for case in self.contract["input"]["cases"]]
        self.assertEqual(sorted(cases), sorted(self.contract["expected"]))
        self.assertEqual(len(cases), len(set(cases)))

    def test_the_masks_admit_what_the_contract_expects(self):
        for case in self.contract["input"]["cases"]:
            with self.subTest(case=case["id"]):
                admitted = filter_admits(tuple(case["masks"]), case["bits"], case["hostile"], case["own"])
                self.assertIs(admitted, self.contract["expected"][case["id"]])


if __name__ == "__main__":
    unittest.main()
