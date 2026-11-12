"""Native mask-stage cases; full filter admission remains a separate stage."""

import unittest

from ow174.game.script.filter_masks import ALL, Masks, matches


class FilterMaskTests(unittest.TestCase):
    def test_any_all_and_excluded_bits_keep_independent_roles(self):
        self.assertTrue(matches(3, Masks(1, 3, 4), owner_present=False))
        self.assertFalse(matches(3, Masks(4, 3, 0), owner_present=False))
        self.assertFalse(matches(3, Masks(1, 7, 0), owner_present=False))
        self.assertFalse(matches(3, Masks(1, 3, 2), owner_present=False))

    def test_all_marker_accepts_missing_component_but_required_bits_still_apply(self):
        self.assertTrue(matches(None, Masks(ALL), owner_present=False))
        self.assertFalse(matches(None, Masks(ALL, 1), owner_present=False))
        self.assertFalse(matches(None, Masks(), owner_present=False))

    def test_any_relation_can_supply_allowance_but_cannot_override_requirement_or_exclusion(self):
        def relation(spectator):
            return True
        self.assertTrue(matches(0, Masks(any_relation=2), owner_present=True, relation_enemy=relation))
        self.assertFalse(matches(0, Masks(all_bits=1, any_relation=2),
                                 owner_present=True, relation_enemy=relation))
        self.assertFalse(matches(1, Masks(no_bits=1, any_relation=2),
                                 owner_present=True, relation_enemy=relation))

    def test_required_relationships_are_conjunctive_and_self_is_friendly(self):
        for hostile in (False, True):
            def relation(spectator, value=hostile):
                return value
            self.assertFalse(matches(1, Masks(1, all_relation=3),
                                     owner_present=True, relation_enemy=relation))
            self.assertTrue(matches(1, Masks(1, all_relation=5), owner_present=True,
                                    same_entity=True, relation_enemy=relation))
            self.assertFalse(matches(1, Masks(1, all_relation=2), owner_present=True,
                                     same_entity=True, relation_enemy=relation))

    def test_relation_is_eager_even_after_static_failure_and_receives_spectator_branch(self):
        calls = []
        self.assertFalse(matches(1 << 28, Masks(1, any_relation=2), owner_present=True,
                                 relation_enemy=lambda spectator: calls.append(spectator) or False))
        self.assertEqual(calls, [True])
        self.assertFalse(matches(0, Masks(1, any_relation=1 << 63), owner_present=True,
                                 relation_enemy=lambda spectator: calls.append(spectator) or True))
        self.assertEqual(calls, [True, False])

    def test_missing_owner_or_zero_relation_masks_do_not_call_relationship_service(self):
        def unexpected(spectator):
            self.fail('unneeded relationship query')
        self.assertFalse(matches(0, Masks(any_relation=7), owner_present=False, relation_enemy=unexpected))
        self.assertTrue(matches(1, Masks(1), owner_present=True, relation_enemy=unexpected))

    def test_unknown_relation_and_invalid_mask_data_are_rejected(self):
        with self.assertRaises(ValueError):
            matches(0, Masks(any_relation=2), owner_present=True)
        with self.assertRaises(ValueError):
            matches(0, Masks(any_relation=2), owner_present=True, relation_enemy=lambda spectator: None)
        for value in (-1, 1 << 64, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Masks(value)


if __name__ == '__main__':
    unittest.main()
