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

    def test_status_primary_component_false_overrides_true_fallbacks(self):
        self.assertFalse(filter_bits.status10(component57=False, component51=True, component28_flags=0x2000))
        self.assertTrue(filter_bits.status10(component57=True, component51=False, component28_flags=0))
        self.assertTrue(filter_bits.status10(component57=None, component51=True, component28_flags=None))
        self.assertTrue(filter_bits.status10(component57=None, component51=None, component28_flags=0x2001))
        self.assertFalse(filter_bits.status10(component57=None, component51=False, component28_flags=0xDFFF))
        self.assertFalse(filter_bits.status10(component57=None, component51=None, component28_flags=None))
        for values in ((NotImplemented, False, 0), (None, 1, 0), (None, None, -1), (None, None, True)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                filter_bits.status10(component57=values[0], component51=values[1],
                                     component28_flags=values[2])

    def test_primary_status_missing_collection_suppression_and_bypass(self):
        self.assertTrue(filter_bits.status57(component4_present=False, collection_flags=0, suppress=True))
        self.assertTrue(filter_bits.status57(component4_present=True, collection_flags=None, suppress=True))
        self.assertFalse(filter_bits.status57(component4_present=True, collection_flags=4, suppress=True))
        self.assertTrue(filter_bits.status57(component4_present=True, collection_flags=4, suppress=False))
        self.assertTrue(filter_bits.status57(component4_present=True, collection_flags=4,
                                             suppress=True, bypass=True))
        self.assertFalse(filter_bits.status57(component4_present=True, collection_flags=0xFB, suppress=False))
        for flags in (-1, 256, True, NotImplemented):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                filter_bits.status57(component4_present=True, collection_flags=flags, suppress=False)

    def test_health_status_float32_epsilon_and_unordered_comparisons(self):
        self.assertFalse(filter_bits.status51(value78=0, value70=0))
        self.assertFalse(filter_bits.status51(value78=float("nan"), value70=0))
        self.assertTrue(filter_bits.status51(value78=1, value70=2 ** -23))
        self.assertTrue(filter_bits.status51(value78=1, value70=-(2 ** -23)))
        self.assertFalse(filter_bits.status51(value78=1, value70=2 ** -23 + 2 ** -46))
        self.assertTrue(filter_bits.status51(value78=1, value70=float("nan")))
        self.assertFalse(filter_bits.status51(value78=1, value70=float("inf")))
        with self.assertRaises(ValueError):
            filter_bits.status51(value78=True, value70=0)

    def test_presence_masks_do_not_assume_unavailable_words_or_payloads_are_absent(self):
        masks = (1 << 25, 1 << (97 - 64))
        self.assertTrue(filter_bits.has_component(masks, 25))
        self.assertTrue(filter_bits.has_component(masks, 97))
        self.assertFalse(filter_bits.has_component(masks, 57))
        self.assertFalse(filter_bits.status10_from_masks(masks))
        with self.assertRaisesRegex(NotImplementedError, "word is unavailable"):
            filter_bits.has_component(masks[:1], 97)
        with self.assertRaisesRegex(NotImplementedError, "component51"):
            filter_bits.status10_from_masks((1 << 51,))
        with self.assertRaisesRegex(NotImplementedError, "component28"):
            filter_bits.status10_from_masks((1 << 28,))
        with self.assertRaisesRegex(NotImplementedError, "component57"):
            filter_bits.status10_from_masks((1 << 57,))
        for masks, identifier in (((), 0), ((True,), 0), ((0,), 256), ((0,), -1)):
            with self.subTest(masks=masks, identifier=identifier), self.assertRaises(ValueError):
                filter_bits.has_component(masks, identifier)

    def test_presence_status_branch_requires_only_payloads_that_native_reads(self):
        unused = Mock(side_effect=AssertionError("unreached status payload"))
        self.assertFalse(filter_bits.status10_from_masks(
            ((1 << 57) | (1 << 51) | (1 << 28),), primary=lambda: False, health=unused, flags=unused))
        self.assertTrue(filter_bits.status10_from_masks(
            ((1 << 51) | (1 << 28),), health=lambda: True, flags=unused))
        self.assertTrue(filter_bits.status10_from_masks(
            ((1 << 51) | (1 << 28),), health=lambda: False, flags=lambda: 0x2001))
        with self.assertRaises(ValueError):
            filter_bits.status10_from_masks((1 << 57,), primary=lambda: NotImplemented)


if __name__ == "__main__":
    unittest.main()
