"""Compile requirements must preserve native visited-mask semantics."""

import unittest

from ow174.game.script.component_dependencies import declaration_mask, dependency_mask


class ComponentDependencyTests(unittest.TestCase):
    def test_health_transitively_requires_filter_and97(self):
        value = declaration_mask([0xA658CA1C])
        self.assertEqual([i for i in range(256) if value & (1 << i)],
                         [25, 51, 56, 62, 68, 97, 115])

    def test_seed_is_visited_state_not_merely_union_output(self):
        # Pre-visiting filter25 suppresses its dependency97 during a26 walk.
        self.assertEqual(dependency_mask(26, 1 << 25), (1 << 25) | (1 << 26))
        self.assertEqual(dependency_mask(26, 1 << 26), 1 << 26)
        self.assertTrue(dependency_mask(26) & (1 << 97))
        self.assertEqual(dependency_mask(26, (1 << 256) - 1), (1 << 256) - 1)

    def test_declarations_ignore_unknown_zero_and_union_independent_closures(self):
        self.assertEqual(declaration_mask([0]), 0)
        self.assertEqual(declaration_mask([0x445E7678, 0x445E7678, 0]), 1 << 76)
        self.assertEqual(declaration_mask([0x445E7678, 0xA658CA1C]),
                         dependency_mask(76) | dependency_mask(51))

    def test_out_of_registry_and_non_native_inputs_are_rejected(self):
        for index in (-1, 143, 256, True, 26.0):
            with self.subTest(index=index), self.assertRaises(ValueError):
                dependency_mask(index)
        for initial in (-1, 1 << 256, True, '0'):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                dependency_mask(26, initial)


if __name__ == '__main__':
    unittest.main()
