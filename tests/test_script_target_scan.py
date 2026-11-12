"""Native category selection must preserve the general scan fallback."""

import unittest

from ow174.game.script import graph, target_scan


class TargetScanTests(unittest.TestCase):
    def test_required_categories_have_native_priority_over_any_mask(self):
        self.assertEqual(target_scan.category(0x40, 0x500C1), 1)
        self.assertEqual(target_scan.category(1, 0x500C0), 0x40000)
        self.assertEqual(target_scan.category(1, 0x10080), 0x40)
        self.assertEqual(target_scan.category(1, 0x10000), 0x10000)

    def test_single_any_categories_include_alias80_but_not10000(self):
        for bits, expected in ((1, 1), (0x40000, 0x40000), (0x40, 0x40), (0x80, 0x40),
                               (0x10000, 0), (1 << 63, 0), (0, 0)):
            self.assertEqual(target_scan.category(bits, 0), expected)

    def test_mixed_any_masks_keep_general_scan_even_when_aliases_share_a_bucket(self):
        for bits in (0xC0, 0x40001, 0x3FFFFFFF, 1 | (1 << 63)):
            self.assertEqual(target_scan.category(bits, 0), 0)

    def test_actual_flux_filter_selects_category1_without_supplying_body_bits(self):
        config = graph.graph(0x1E41).node(37).field("m_filter")
        self.assertEqual(target_scan.filter_category(config), 1)
        self.assertEqual(target_scan.filter_category(None), 0)
        self.assertEqual(target_scan.filter_category({"$": "STUConfigVarFilter"}), 0)

    def test_unknown_configs_and_non_uint64_masks_fail(self):
        for config in ({}, {"$": "STUConfigVarDynamic"}, object()):
            with self.assertRaises(ValueError):
                target_scan.filter_category(config)
        for value in (None, True, -1, 1 << 64, 1.0):
            for args in ((value, 0), (0, value)):
                with self.assertRaises(ValueError):
                    target_scan.category(*args)

    def test_root_collision_chain_compares_full_key_and_preserves_identity(self):
        first, second = object(), object()
        table = target_scan.RootMap((0,), (target_scan.RootEntry(0x40, 1),
                                          target_scan.RootEntry(1, -1)), (first, second))
        self.assertIs(table.lookup(1), second)
        self.assertIs(table.lookup(0x40), first)
        self.assertIsNone(table.lookup(0x40000))

    def test_first_matching_null_root_does_not_fall_through_to_duplicate(self):
        table = target_scan.RootMap((0,), (target_scan.RootEntry(1, 1),
                                          target_scan.RootEntry(1, -1)), (None, object()))
        self.assertIsNone(table.lookup(1))

    def test_sign_extended_negative_key_and_negative_chain_termination(self):
        root = object()
        table = target_scan.RootMap((0,), (target_scan.RootEntry((1 << 64) - 1, -8),), (root,))
        self.assertIs(table.lookup(-1), root)
        self.assertIsNone(table.lookup(1))

    def test_known_missing_roots_are_distinct_from_unknown_roots(self):
        self.assertIsNone(target_scan.RootMap(None).lookup(1))
        self.assertIsNone(target_scan.RootMap((-1,)).lookup(1))
        with self.assertRaises(ValueError):
            target_scan.RootMap().lookup(1)
        with self.assertRaises(ValueError):
            target_scan.RootMap((0,), (target_scan.RootEntry(1, -1),), (NotImplemented,)).lookup(1)

    def test_malformed_root_storage_fails_without_accepting_an_actor(self):
        for table in (target_scan.RootMap(()), target_scan.RootMap((-1,) * 3),
                      target_scan.RootMap((0,)),
                      target_scan.RootMap((0,), (target_scan.RootEntry(2, 0),)),
                      target_scan.RootMap((0,), (target_scan.RootEntry(1, -1),))):
            with self.subTest(table=table), self.assertRaises(ValueError):
                table.lookup(1)
        for key in (True, None, 1 << 31, -(1 << 31) - 1):
            with self.assertRaises(ValueError):
                target_scan.RootMap(None).lookup(key)


if __name__ == "__main__":
    unittest.main()
