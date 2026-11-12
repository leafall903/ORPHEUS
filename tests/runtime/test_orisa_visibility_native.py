"""Compare the sample backend against separately executed original CA4450 cases."""

import json
import os
import unittest
from pathlib import Path

from ow174.game.script import expr, target_visibility


class OrisaVisibilityContractTests(unittest.TestCase):
    def test_unknown_services_are_not_treated_as_clear_or_native_failure(self):
        sample = {"m_start": 1, "m_end": 2}
        with self.assertRaises(target_visibility.VisibilityUnavailable):
            target_visibility.sample_segments([sample], origin=expr.Vec3(0, 0, 0),
                                              evaluate=lambda cfg: NotImplemented,
                                              ray=lambda a, b: False, test_offset_leg=True)
        with self.assertRaises(target_visibility.VisibilityUnavailable):
            target_visibility.sample_segments([sample], origin=expr.Vec3(0, 0, 0),
                                              evaluate=lambda cfg: expr.Vec3(0, 0, 0),
                                              ray=lambda a, b: NotImplemented, test_offset_leg=True)


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_VISIBILITY_NATIVE"), "offline native cases required")
class OrisaNativeVisibilityTests(unittest.TestCase):
    def test_configured_sample_results_order_and_cache_match_executed_native_cases(self):
        source = Path(os.environ["ORPHEUS_ORISA_VISIBILITY_NATIVE"])
        document = json.loads(source.read_text(encoding="utf-8"))
        self.assertEqual(document["build"], "1.74.0.0.104319")
        self.assertEqual(document["function_rva"], "CA4450")
        self.assertEqual(document["function_sha256"],
                         "07b6edebd9b126ca34308ec5d50d6fbf745eb9f71e08a7bdbd4d1d5ed4ec7dd3")
        self.assertEqual(len(document["rows"]), document["cases"])
        self.assertGreaterEqual(document["cases"], 75)
        for number, case in enumerate(document["rows"]):
            with self.subTest(native_case=number):
                self.compare(case)

    def compare(self, case):
        options = case["input"]
        origin = expr.Vec3(*options["origin"])
        samples, values = [], {}
        for index in range(6):
            keys = [(index, kind) for kind in range(3)]
            for key, value in zip(keys, ((10 + index, 20, 30), (0.25 * (index + 1), 0.5, 0.75),
                                         (40 + index, 50, 60)), strict=True):
                values[key] = expr.Vec3(*value)
            if options["fractional_values"]:
                for key, value in zip(keys, ((-0.1, 0.2, -1 / 3), (0.2, 0.3, 0.4),
                                             (0.1, -0.2, 1 / 3)), strict=True):
                    values[key] = expr.Vec3(*value)
            samples.append({"m_start": None if options["missing_start"] else keys[0],
                            "m_83BE3F26": keys[1] if options["offsets"] else None,
                            "m_end": None if options["missing_end"] else keys[2]})
        samples = None if not options["array"] else samples[:options["count"]]
        trace, evaluated = [], []

        def evaluate(key):
            evaluated.append(list(key))
            return values[key]

        def ray(start, end):
            self.assertLess(len(trace), len(case["trace"]), "extra ray request")
            expected = case["trace"][len(trace)]
            self.assertEqual(list(start), expected["start"])
            self.assertEqual(list(end), expected["end"])
            trace.append(expected)
            return expected["blocked"]

        result = target_visibility.sample_segments(samples, origin=origin, evaluate=evaluate, ray=ray,
                                                   test_offset_leg=options["check_leg"])
        self.assertEqual(result.rejected, case["rejected"])
        self.assertEqual(trace, case["trace"])
        self.assertEqual(evaluated, case["evaluated_fields"])
        if result.segment is not None:
            self.assertTrue(case["cache_valid"])
            self.assertEqual(list(result.segment[0]), case["cache_start"])
            self.assertEqual(list(result.segment[1]), case["cache_end"])
        else:
            self.assertFalse(case["cache_valid"])


if __name__ == "__main__":
    unittest.main()
