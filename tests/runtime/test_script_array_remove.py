"""DA54F8F7 unkeyed removal and strict boundaries, grounded in104319 execution."""

import unittest

from ow174.game.script import expr, runtime
from tests.public.test_script_array_insert import setup
from tests.public.test_script_play_loaded import dynamic


def execute(**kwargs):
    component, instance, action = setup(**kwargs)
    action.cls = 'STU_DA54F8F7'
    instance.run_node(action, None)
    return component, instance


class ArrayRemoveTests(unittest.TestCase):
    def test_index_compacts_one_row_and_preserves_order(self):
        for index, expected in ((0, (2, 3)), (1, (1, 3)), (2, (1, 2)), (3, (1, 2, 3))):
            with self.subTest(index=index):
                _, instance = execute(current=(1, 2, 3), index=index, source=None)
                self.assertEqual(instance.find_var(0, 1).value(), expected)
                self.assertEqual(instance.find_var(0, 90).value(), 19)

    def test_nonarray_is_coerced_before_index_even_when_index_is_invalid(self):
        for initial in (None, 99, expr.Entity(1), expr.Map()):
            _, instance = execute(current=initial, index=10, source=None)
            self.assertEqual(instance.find_var(0, 1).value(), ())

    def test_no_selector_skips_coercion_but_missing_index_with_values_removes_all_matches(self):
        _, instance = execute(current=99, source=None)
        self.assertEqual(instance.find_var(0, 1).value(), 99)
        _, instance = execute(current=(1, 2, 1, 3), value=(1, 3))
        self.assertEqual(instance.find_var(0, 1).value(), (2,))

    def test_nonnegative_index_ignores_values_and_negative_index_uses_typed_equality(self):
        _, instance = execute(current=(1, 2, 1), index=100, source={'$': 'UnknownNotEvaluated'})
        self.assertEqual(instance.find_var(0, 1).value(), (1, 2, 1))
        _, instance = execute(current=(1, 1.0, True, 2, expr.Entity(1)), index=-1, value=1)
        self.assertEqual(instance.find_var(0, 1).value(), (2, expr.Entity(1)))

    def test_self_reference_terminates_after_prior_removals(self):
        _, instance = execute(current=(1, 2, 3), source={'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'STUConfigVarInt', 'm_value': 1}, dynamic(1), {'$': 'UnknownNotEvaluated'}]})
        self.assertEqual(instance.find_var(0, 1).value(), (2, 3))

    def test_key_rejection_skips_storage_and_valid_key_retains_strict_boundary(self):
        _, instance = execute(current=99, key=False, source={'$': 'UnknownNotEvaluated'})
        self.assertEqual(instance.find_var(0, 1).value(), 99)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'keyed row storage'):
            execute(current=(1, 2), key=expr.Asset(17))

    def test_linked_output_is_not_approximated(self):
        _, instance, action = setup(current=(1, 2), index=0)
        action.cls = 'STU_DA54F8F7'
        instance.find_var(0, 1).links.append(runtime.Link(instance.state(0)))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'linked variable checkpoint'):
            instance.run_node(action, None)

    def test_compatibility_policy_preserves_previous_output_only_behavior(self):
        component, instance, action = setup(current=(1, 2, 3), index=0)
        action.cls = 'STU_DA54F8F7'
        component.strict = False
        instance.run_node(action, None)
        self.assertEqual(instance.find_var(0, 1).value(), (1, 2, 3))
        self.assertEqual(instance.find_var(0, 90).value(), 19)
