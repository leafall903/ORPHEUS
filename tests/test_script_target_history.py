"""History floor and generation boundaries in explicit native-style samples."""

import unittest

from ow174.game.script.target_history import SphereHistory, SphereSample


class SphereHistoryTests(unittest.TestCase):
    def history(self, generation=7, minimum=0, flagged=0):
        return SphereHistory((SphereSample(20, 7, (10, 0, 0, 1)),
                              SphereSample(10, generation, (0, 0, 0, 3))), minimum, flagged)

    def test_time_endpoints_interpolation_and_maximum_radius(self):
        history = self.history()
        self.assertEqual(history.at(0, False), (0, 0, 0, 3))
        self.assertEqual(history.at(30 * 16000, False), (10, 0, 0, 1))
        self.assertEqual(history.at(15 * 16000, False), (5, 0, 0, 3))
        self.assertEqual(history.at(20 * 16000, False), (10, 0, 0, 1))

    def test_generation_boundary_selects_newer_without_blending(self):
        self.assertEqual(self.history(generation=8).at(15 * 16000, False), (10, 0, 0, 1))

    def test_always_floor_and_flagged_floor_are_distinct(self):
        history = self.history(minimum=15 * 16000, flagged=20 * 16000)
        self.assertEqual(history.at(0, False), (5, 0, 0, 3))
        self.assertEqual(history.at(0, True), (10, 0, 0, 1))

    def test_empty_is_known_getter_failure_and_unknown_remains_error(self):
        self.assertIsNone(SphereHistory(()).at(0, True))
        with self.assertRaises(ValueError):
            SphereHistory().at(0, False)
        with self.assertRaises(ValueError):
            self.history().at(0, NotImplemented)


if __name__ == "__main__":
    unittest.main()
