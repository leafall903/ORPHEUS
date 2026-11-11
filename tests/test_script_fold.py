"""Native fold-loop semantics used by the authored ultimate-charge graph."""

import unittest

from test_game_graph_analysis import make_graph
from test_script_array_item import dynamic, group, integer

from ow174.game.script import runtime


def parameter(key):
    return {"$": "STU_919FD47C", "m_expression": {
        "$": "STUConfigVarExpressionData", "m_opcodes": [13, 0, 0],
        "m_dynamicVars": [{"$": "STUConfigVarDynamic", "m_identifier": key}],
    }}


def add_parameters():
    return {"$": "STU_919FD47C", "m_expression": {
        "$": "STUConfigVarExpressionData", "m_opcodes": [13, 0, 13, 1, 23, 0],
        "m_dynamicVars": [{"$": "STUConfigVarDynamic", "m_identifier": key}
                          for key in (0x50D, 0x50E)],
    }}


def fold(source, seed=None, step=None, final=None):
    return {"$": "STU_A6B80C63", "m_814F8EC3": source, "m_418D9D9D": seed,
            "m_C239F989": step, "m_8054375D": final}


class FoldTests(unittest.TestCase):
    def instance(self):
        return runtime.Component(123, strict=True).create(make_graph(1), init=False)

    def test_collects_nested_groups_and_arrays_before_folding(self):
        instance = self.instance()
        instance.component.set(600, (2, (None, 3)))
        config = fold(group(integer(1), dynamic(600)), integer(7), add_parameters())
        self.assertEqual(instance.evaluate(config), 13)
        self.assertFalse(instance.component.parameter_scopes)

    def test_empty_input_skips_seed_and_step_but_runs_projection(self):
        instance = self.instance()
        unknown = {"$": "STU_MISSING_FOLD_SEED"}
        self.assertIsNone(instance.evaluate(fold(group(), unknown, unknown)))
        self.assertEqual(instance.evaluate(fold(None, unknown, unknown, parameter(0x41A))), 0)
        self.assertIsNone(instance.component.fault)

    def test_scalar_source_is_one_item_and_null_scalar_has_no_items(self):
        instance = self.instance()
        self.assertEqual(instance.evaluate(fold(integer(3), integer(2), add_parameters())), 5)
        self.assertIsNone(instance.evaluate(fold(dynamic(999), integer(2), add_parameters())))

    def test_source_collection_sees_outer_provider_before_fold_binding(self):
        instance = self.instance()
        with instance.component.parameter_scope({0x50E: (1, 2)}):
            config = fold(parameter(0x50E), parameter(0x41A), add_parameters())
            self.assertEqual(instance.evaluate(config), 5)
            self.assertEqual(instance.context().param(0x50E), (1, 2))

    def test_final_index_is_count_and_exhausted_item_falls_through(self):
        instance = self.instance()
        config = fold(group(integer(3), integer(4)), integer(2), add_parameters())
        with instance.component.parameter_scope({0x50E: 91, 0x50D: 81}):
            for key, expected in ((0x0C, 2), (0x41A, 2), (0x50D, 9), (0x50E, 91)):
                config['m_8054375D'] = parameter(key)
                self.assertEqual(instance.evaluate(config), expected)
            self.assertEqual(instance.context().param(0x50D), 81)
        self.assertFalse(instance.component.parameter_scopes)

    def test_failed_seed_and_steps_preserve_accumulator_and_projection_destination(self):
        instance = self.instance()
        failed = {"$": "STU_7BD2CA19", "m_resourceKey": None}
        source = group(integer(2), integer(3))
        self.assertIsNone(instance.evaluate(fold(source, failed, failed)))
        self.assertEqual(instance.evaluate(fold(source, integer(8), failed)), 8)
        self.assertEqual(instance.evaluate(fold(source, integer(8), add_parameters(), failed),
                                           initial=42), 42)

    def test_seed_sees_first_item_before_steps(self):
        instance = self.instance()
        self.assertEqual(instance.evaluate(fold(group(integer(2), integer(3)),
                                                parameter(0x50E), add_parameters())), 7)

    def test_nested_fold_restores_outer_accumulator(self):
        instance = self.instance()
        nested = fold(integer(5), parameter(0x50D), add_parameters())
        outer = fold(group(integer(1), integer(2)), integer(10), nested)
        # Each inner provider starts with null, hiding the outer accumulator.
        self.assertEqual(instance.evaluate(outer), 5)
        self.assertFalse(instance.component.parameter_scopes)

    def test_strict_failure_unwinds_provider_and_preserves_outer_scope(self):
        instance = self.instance()
        outer = {0x50D: 83}
        with instance.component.parameter_scope(outer):
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(fold(integer(1), integer(0), {"$": "STU_UNKNOWN_FOLD_STEP"}))
            self.assertEqual(instance.component.parameter_scopes, [outer])
        self.assertFalse(instance.component.parameter_scopes)


if __name__ == "__main__":
    unittest.main()
