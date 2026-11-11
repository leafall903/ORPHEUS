"""Native filter candidate parameter scoping, conversions and authored reads."""

import math
import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph

from ow174.game.script import expr, filter_condition, runtime

PARAMETER = {"$": "STUConfigVarExpression", "m_expression": {
    "$": "STUConfigVarExpressionData", "m_opcodes": [13, 0, 0],
    "m_dynamicVars": [{"$": "STUConfigVarDynamic", "m_identifier": 8}]}}


class FilterConditionTests(unittest.TestCase):
    def instance(self, *, strict=False):
        component = runtime.Component(100, world=runtime.World(1, 1), strict=strict)
        return component.create(make_graph(1), init=False)

    def test_missing_condition_precedes_instance_and_missing_instance_fails(self):
        self.assertTrue(filter_condition.evaluate(None, None, 102))
        self.assertFalse(filter_condition.evaluate(None, PARAMETER, 102))

    def test_candidate_is_parameter_only_and_outer_scope_is_restored(self):
        instance = self.instance()
        component = instance.component
        outer = {8: expr.Entity(0)}
        with component.parameter_scope(outer):
            self.assertTrue(filter_condition.evaluate(instance, PARAMETER, 102))
            self.assertEqual(component.parameter_scopes, [outer])
            self.assertFalse(filter_condition.evaluate(instance, PARAMETER, 0))
        self.assertEqual(component.parameter_scopes, [])
        self.assertNotIn(8, instance.vars)
        self.assertNotIn(8, component.vars)

    def test_actual_assassin_condition_reads_candidate_flag_not_callers_flag(self):
        instance = self.instance()
        component = instance.component
        # Actual13C2 condition: !parameter0008.member14695.
        config = {"$": "STU_919FD47C", "m_expression": {
            "$": "STUConfigVarExpressionData", "m_opcodes": [13, 0, 7, 1, 15, 30, 0],
            "m_dynamicVars": [{"$": "STUConfigVarDynamic", "m_identifier": 8},
                              {"$": "STUConfigVarDynamic", "m_identifier": 14695}]}}
        slot = runtime.Var((102, 14695))
        slot.base = True
        component.set(14695, False)
        with patch.object(component.world, "entity_var_slot", return_value=slot) as lookup:
            self.assertFalse(filter_condition.evaluate(instance, config, 102))
            lookup.assert_called_with(102, 14695, create=False)
            slot.base = False
            self.assertTrue(filter_condition.evaluate(instance, config, 102))

    def test_nested_candidate_scope_does_not_leak_or_overwrite_outer(self):
        instance = self.instance()
        seen = []

        def nested(cfg, current):
            seen.append(current.context().param(8))
            result = filter_condition.evaluate(current, PARAMETER, 103)
            seen.append(current.context().param(8))
            return result

        with patch.object(instance.component.world, "native", side_effect=nested):
            self.assertTrue(filter_condition.evaluate(instance, {"$": "declared_nested_condition"}, 102))
        self.assertEqual(seen, [expr.Entity(102), expr.Entity(102)])
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_scope_cleanup_on_exception_and_strict_unknown_queries(self):
        instance = self.instance()
        with (patch.object(instance, "evaluate", side_effect=RuntimeError("condition callback")),
              self.assertRaisesRegex(RuntimeError, "condition callback")):
            filter_condition.evaluate(instance, PARAMETER, 102)
        self.assertEqual(instance.component.parameter_scopes, [])
        strict = self.instance(strict=True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            filter_condition.evaluate(strict, {"$": "unknown_filter_condition"}, 102)
        self.assertEqual(strict.component.parameter_scopes, [])

    def test_known_failure_and_successful_null_are_different(self):
        instance = self.instance()
        with patch.object(instance.component.world, "native", return_value=expr.EVALUATION_FAILED):
            self.assertTrue(filter_condition.evaluate(instance, {"$": "known_failure"}, 102))
        with patch.object(instance.component.world, "native", return_value=None):
            self.assertFalse(filter_condition.evaluate(instance, {"$": "known_null"}, 102))

    def test_native_boolean_defaults_and_nonfinite_values(self):
        for value, expected in ((None, False), ((), True), (((),), True), ((None,), False),
                                (expr.Entity(0), False), (expr.Entity(102), True),
                                (1 << 32, False), (expr.Entity(1 << 32), False),
                                (expr.Asset(1 << 64), False),
                                (float("nan"), False), (math.inf, True), (1e-100, False),
                                (expr.Vec3(float("nan"), 0, 0), False),
                                (expr.Vec3(float("nan"), 1, 0), True),
                                (expr.Handle(1, 0, 0), True)):
            with self.subTest(value=value):
                self.assertEqual(filter_condition.boolean(value, True), expected)
        self.assertFalse(filter_condition.boolean((), False))
        with self.assertRaises(ValueError):
            filter_condition.boolean(object(), True)


if __name__ == "__main__":
    unittest.main()
