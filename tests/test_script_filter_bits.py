"""Native entity-filter bit stage; unknown engine relationships never pass."""

import unittest
from unittest.mock import Mock

from ow174.game.script import filter_bits


class FilterBitsTests(unittest.TestCase):
    def test_projection_selects_base_and_recomputes_only_status10(self):
        self.assertEqual(filter_bits.project(0x40011, None, status10=False), 0x40001)
        self.assertEqual(filter_bits.project(0, 0x840001, status10=True), 0x840011)
        self.assertEqual(filter_bits.project(0x840011, 0, status10=False), 0)
        self.assertEqual(filter_bits.project(1 << 63, None, status10=True), (1 << 63) | 0x10)
        with self.assertRaises(ValueError):
            filter_bits.project(0, None, status10=None)

    def test_any_all_and_excluded_masks_include_high_uint64_bits(self):
        bit = 1 << 63
        self.assertTrue(filter_bits.matches(bit | 3, bit, 3, 4))
        self.assertFalse(filter_bits.matches(bit | 1, bit, 3, 4))
        self.assertFalse(filter_bits.matches(bit | 7, bit, 3, 4))
        self.assertFalse(filter_bits.matches(3, bit, 3, 4))

    def test_native_wildcard_and_absent_component(self):
        self.assertTrue(filter_bits.matches(None, 0x3FFFFFFF, 0, 0))
        self.assertTrue(filter_bits.matches(1 << 63, 0x3FFFFFFF, 0, 0))
        self.assertFalse(filter_bits.matches(None, 0x40000, 0, 0))
        self.assertFalse(filter_bits.matches(None, 0x3FFFFFFF, 1, 0))
        self.assertFalse(filter_bits.matches(0, 0, 0, 0))

    def test_actual_assassin_masks_require_category_and_exclude_three_status_bits(self):
        # Actual13C2 nodes10/13: any40000, all0, excluded130; relations0.
        self.assertTrue(filter_bits.matches(0x40000 | 0x800000, 0x40000, 0, 0x130))
        self.assertFalse(filter_bits.matches(0x800000, 0x40000, 0, 0x130))
        for bit in (0x10, 0x20, 0x100):
            self.assertFalse(filter_bits.matches(0x40000 | bit, 0x40000, 0, 0x130))

    def test_relationship_calls_are_lazy_and_missing_source_skips_relation_masks(self):
        query = Mock(side_effect=AssertionError("unused relationship"))
        self.assertTrue(filter_bits.matches(1, 1, 0, 0, source_present=True, relationship=query))
        self.assertTrue(filter_bits.matches(1, 1, 0, 0, 7, 7, 7, relationship=query))
        query.assert_not_called()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            filter_bits.matches(1, 1, 0, 0, 1, source_present=True)

    def test_relation_any_all_exclusion_and_self_pointer_comparison(self):
        options = {"source_present": True, "relationship": lambda spectator: True}
        self.assertTrue(filter_bits.matches(0, 0, 0, 0, 2, **options))
        self.assertFalse(filter_bits.matches(0, 0, 0, 0, 1, **options))
        self.assertTrue(filter_bits.matches(0, 0, 0, 0, 1, same_entity=True, **options))
        self.assertTrue(filter_bits.matches(0, 0, 0, 0, 4, same_entity=True, **options))
        self.assertFalse(filter_bits.matches(1, 1, 0, 0, 0, 1, **options))
        self.assertFalse(filter_bits.matches(1, 1, 0, 0, 0, 0, 2, **options))

    def test_spectator_branch_is_supplied_to_relationship_service(self):
        query = Mock(return_value=False)
        for bits in (0, 1 << 28):
            self.assertTrue(filter_bits.matches(bits, 0, 0, 0, 1,
                                                source_present=True, relationship=query))
        self.assertEqual([call.args for call in query.call_args_list], [(False,), (True,)])

    def test_invalid_masks_and_unknown_relationship_answers_are_rejected(self):
        for value in (True, -1, 1 << 64, 1.0, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                filter_bits.matches(value, 1, 0, 0)
        with self.assertRaises(ValueError):
            filter_bits.matches(1, 1, 0, 0, 1, source_present=True, relationship=lambda _: None)
        with self.assertRaises(ValueError):
            filter_bits.matches(1, 1, 0, 0, same_entity=True)


if __name__ == "__main__":
    unittest.main()
