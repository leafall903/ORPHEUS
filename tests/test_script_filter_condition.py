"""Candidate parameter scope and default policy of the base filter condition."""

import unittest

from test_game_graph_analysis import make_graph
from test_script_array_item import integer
from test_script_fold import parameter

from ow174.game.script import expr, runtime
from ow174.game.script.filter_condition import evaluate


class FilterConditionTests(unittest.TestCase):
    def instance(self):
        return runtime.Component(123, strict=True).create(make_graph(1), init=False)

    def test_absent_condition_is_true_even_without_context(self):
        self.assertTrue(evaluate(self.instance(), {}, 0, context_present=False))

    def test_present_condition_without_context_is_false_without_evaluation(self):
        self.assertFalse(evaluate(self.instance(), {'m_condition': {'$': 'STU_UNAVAILABLE'}},
                                  123, context_present=False))

    def test_valid_candidate_shadows_outer_parameter_only_inside_condition(self):
        instance = self.instance()
        with instance.component.parameter_scope({8: expr.Entity(0)}):
            self.assertTrue(evaluate(instance, {'m_condition': parameter(8)},
                                     0xA0000101, context_present=True))
            self.assertEqual(instance.context().param(8), expr.Entity(0))
        self.assertFalse(instance.component.parameter_scopes)

    def test_invalid_candidate_preserves_outer_parameter_lookup(self):
        instance = self.instance()
        with instance.component.parameter_scope({8: expr.Entity(123)}):
            for invalid in (0, 0x1FFFFFFF, 0xFFFFFFFF):
                self.assertTrue(evaluate(instance, {'m_condition': parameter(8)},
                                         invalid, context_present=True))

    def test_known_no_output_retains_true_but_successful_false_does_not(self):
        instance = self.instance()
        self.assertTrue(evaluate(instance, {'m_condition': {
            '$': 'STU_7BD2CA19', 'm_resourceKey': None}}, 123, context_present=True))
        self.assertFalse(evaluate(instance, {'m_condition': integer(0)}, 123, context_present=True))

    def test_strict_failure_restores_outer_scope(self):
        instance = self.instance()
        outer = {8: expr.Entity(321)}
        with instance.component.parameter_scope(outer):
            with self.assertRaises(runtime.UnsupportedScriptError):
                evaluate(instance, {'m_condition': {'$': 'STU_UNKNOWN_FILTER_CONDITION'}},
                         123, context_present=True)
            self.assertEqual(instance.component.parameter_scopes, [outer])
        self.assertFalse(instance.component.parameter_scopes)


if __name__ == '__main__':
    unittest.main()
