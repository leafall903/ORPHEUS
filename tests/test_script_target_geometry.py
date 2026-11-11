"""Broadphase envelope copying is separate from payload current bounds and BV validation."""

import unittest

from ow174.game.script.target_geometry import Record24Geometry, broadphase24


class TargetGeometryTests(unittest.TestCase):
    def test_valid_current_x_selects_distinct_opaque_envelope(self):
        envelope = bytes(range(32))
        self.assertIs(broadphase24(Record24Geometry(0, 0x3F800000, envelope)), envelope)
        self.assertIs(broadphase24(Record24Geometry(0x80000000, 0, envelope)), envelope)

    def test_inverted_or_unordered_current_x_fails_before_envelope_access(self):
        self.assertIsNone(broadphase24(Record24Geometry(0x3F800000, 0, NotImplemented)))
        self.assertIsNone(broadphase24(Record24Geometry(0x7FC00000, 0, NotImplemented)))
        self.assertIsNone(broadphase24(Record24Geometry(0, 0x7FC00000, NotImplemented)))
        self.assertIsNone(broadphase24(None))

    def test_unknown_record_or_successful_unavailable_output_remains_error(self):
        with self.assertRaises(ValueError):
            broadphase24(NotImplemented)
        with self.assertRaises(ValueError):
            broadphase24(Record24Geometry(0, 0, b""))


if __name__ == "__main__":
    unittest.main()
