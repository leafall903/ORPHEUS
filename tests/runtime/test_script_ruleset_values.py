"""Ruleset fallback uses config metadata, not an evaluated value as an identifier."""

import unittest

from test_game_graph_analysis import make_graph
from test_script_expr import dynamic, expression

from ow174.game.script import expr, runtime

OWNER, OTHER = 0xA0000101, 0xA0000102
KEY = 9743


def ruleset(config):
    return {"$": "STU_3832D36C", "m_value": config}


class Values(expr.Context):
    def __init__(self, value=None, default=True):
        super().__init__()
        self.value, self.default = value, default
        self.events = []

    def owner(self):
        return expr.Entity(OWNER)

    def var(self, scope, var):
        return self.value

    def member(self, entity, var):
        return self.value

    def ruleset_default(self, key, target):
        self.events.append((key, target))
        return self.default


class RuleValueTests(unittest.TestCase):
    def test_nonnull_values_are_returned_unchanged_without_default_lookup(self):
        for value in (False, 0, 7, 1.25, expr.Asset(0), expr.Entity(OTHER), (1, 2)):
            ctx = Values(value)
            self.assertEqual(expr.evaluate(ruleset(dynamic(KEY, 1)), ctx), value)
            self.assertEqual(ctx.events, [])

    def test_entity_dynamic_null_uses_its_identifier_and_default_target(self):
        ctx = Values(default=1.5)
        self.assertEqual(expr.evaluate(ruleset(dynamic(KEY, 1)), ctx), 1.5)
        self.assertEqual(ctx.events, [(KEY, 0)])

    def test_instance_dynamic_does_not_use_an_entity_ruleset_default(self):
        ctx = Values()
        self.assertEqual(expr.evaluate(ruleset(dynamic(KEY, 0)), ctx), 0)
        self.assertEqual(ctx.events, [])

    def test_member_expression_falls_back_on_selected_entity_and_first_identifier(self):
        cfg = expression([14, 0, 7, 0, 15, 0], [(KEY, 0)], configs=[
            {"$": "STU_B5A0CAF0"}])
        ctx = Values()
        self.assertIs(expr.evaluate(ruleset(cfg), ctx), True)
        self.assertEqual(ctx.events, [(KEY, OWNER)])

    def test_first_expression_metadata_and_scope_gate_are_preserved(self):
        ctx = Values()
        cfg = expression([8, 0, 0], [(KEY, 0), (999, 1)])
        self.assertEqual(expr.evaluate(ruleset(cfg), ctx), 0)
        self.assertEqual(ctx.events, [])
        cfg = expression([8, 0, 0], [(KEY, 1), (999, 1)])
        self.assertIs(expr.evaluate(ruleset(cfg), ctx), True)
        self.assertEqual(ctx.events, [(KEY, 0)])

    def test_missing_metadata_and_defaults_return_typed_integer_zero(self):
        for cfg in (None, expression([0])):
            self.assertEqual(expr.evaluate(ruleset(cfg), Values(default=None)), 0)
        self.assertEqual(expr.evaluate(ruleset(dynamic(KEY, 1)), Values(default=None)), 0)

    def test_typed_empty_string_is_a_successful_value(self):
        cfg = {"$": "STU_92CA44CC", "m_value": None}
        self.assertEqual(expr.evaluate(ruleset(cfg), Values(default=None)), "")

    def test_failed_selector_keeps_existing_int_but_null_selector_clears_it(self):
        cfg = ruleset(dynamic(KEY, 0))
        self.assertEqual(expr.evaluate(cfg, Values(expr.EVALUATION_FAILED), initial=99), 99)
        self.assertEqual(expr.evaluate(cfg, Values(None), initial=99), 0)
        self.assertEqual(expr.evaluate(cfg, Values(expr.EVALUATION_FAILED), initial=1.25), 0)
        self.assertEqual(expr.evaluate(ruleset(None), Values(), initial=99), 99)

    def test_ruleset_target_is_reset_per_helper_and_retained_in_runtime_instance(self):
        component = runtime.Component(OWNER)
        instance = component.create(make_graph(0xFFFF), init=False)
        cfg = expression([14, 0, 7, 0, 15, 0], [(KEY, 0)], configs=[
            {"$": "STU_B5A0CAF0"}])
        instance.evaluate(ruleset(cfg))
        self.assertEqual(instance.ruleset_target_entity, OWNER)
        instance.evaluate(ruleset(None))
        self.assertEqual(instance.ruleset_target_entity, 0)

    def test_unresolved_member_vm_does_not_replace_last_ruleset_target(self):
        component = runtime.Component(OWNER)
        instance = component.create(make_graph(0xFFFF), init=False)
        cfg = expression([14, 0, 7, 0, 15, 0], [(KEY, 0)], configs=[expr.Entity(OTHER)])
        self.assertEqual(instance.evaluate(ruleset(cfg)), 0)
        self.assertEqual(instance.ruleset_target_entity, 0)


if __name__ == "__main__":
    unittest.main()
