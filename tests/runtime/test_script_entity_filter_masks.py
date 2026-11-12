"""BCAAE0 mask-stage semantics, distinct from full target admission."""

import unittest

from ow174.game.script.entity_filter import RelationResult, matches_masks


class EntityFilterMaskTests(unittest.TestCase):
    def test_authored_sleep_mask64_excludes_bits20_and_requires_an_any_match(self):
        cfg = {'m_84065FD1': 64, 'm_4EA686DB': 20}
        for bits, expected in ((None, False), (0, False), (64, True), (65, True),
                               (64 | 4, False), (64 | 16, False), (20, False)):
            with self.subTest(bits=bits):
                self.assertIs(matches_masks(bits, cfg), expected)

    def test_any_sentinel_all_required_and_high_uint64_bits(self):
        self.assertTrue(matches_masks(None, {'m_84065FD1': 0x3FFFFFFF}))
        self.assertFalse(matches_masks(None, {'m_84065FD1': 0xFFFFFFFFFFFFFFFF}))
        self.assertFalse(matches_masks(0xFFFFFFFFFFFFFFFF, {}))
        cfg = {'m_84065FD1': 1 << 63, 'm_1E14CCA8': 3}
        self.assertTrue(matches_masks((1 << 63) | 3, cfg))
        self.assertFalse(matches_masks((1 << 63) | 1, cfg))
        self.assertFalse(matches_masks(3, cfg))

    def test_relation_any_rescues_all_requires_each_and_exclusion_vetoes(self):
        for same_entity, predicate, matched in ((False, False, 1), (False, True, 2),
                                               (True, False, 5), (True, True, 5)):
            relation = RelationResult(same_entity, predicate)
            with self.subTest(relation=relation):
                for wanted in range(8):
                    self.assertEqual(matches_masks(0, {'m_27CF71D5': wanted}, relation=relation),
                                     bool(wanted & matched))
                    cfg = {'m_84065FD1': 64, 'm_66747E7D': wanted}
                    self.assertEqual(matches_masks(64, cfg, relation=relation), wanted & matched == wanted)
                    cfg = {'m_84065FD1': 64, 'm_89831602': wanted}
                    self.assertEqual(matches_masks(64, cfg, relation=relation), not bool(wanted & matched))

    def test_absent_source_ignores_relations_and_high_relation_bits_do_not_match(self):
        cfg = {'m_84065FD1': 64, 'm_66747E7D': 7, 'm_89831602': 7}
        self.assertTrue(matches_masks(64, cfg))
        self.assertFalse(matches_masks(0, {'m_27CF71D5': 7}))
        cfg = {'m_84065FD1': 64, 'm_66747E7D': 1 << 63, 'm_89831602': 1 << 63}
        self.assertTrue(matches_masks(64, cfg, relation=RelationResult(False, True)))

    def test_unknown_or_non_native_inputs_are_not_silently_coerced(self):
        for value in (True, -1, 1 << 64, 1.0, (), NotImplemented):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    matches_masks(value, {})
                with self.assertRaises(ValueError):
                    matches_masks(0, {'m_84065FD1': value})
        with self.assertRaises(ValueError):
            matches_masks(0, {}, relation=False)
        with self.assertRaises(ValueError):
            RelationResult(1, False)
