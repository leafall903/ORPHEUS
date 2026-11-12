"""A6B80C63 contracts established by unchanged104319 loop/provider probes."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node
from test_script_expr import dynamic, expression

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, fold, graph, play, runtime


def integer(value):
    return {"$": "STUConfigVarInt", "m_value": value}


def parameter(key):
    return expression([13, 0, 0], [(key, 0)])


def reduce_config(values=(1, 2, 3), *, initial=None, reducer=None, final=None):
    return {"$": fold.CLASS, "m_814F8EC3": values, "m_418D9D9D": initial,
            "m_C239F989": reducer, "m_8054375D": final}


SUM = expression([13, 0, 13, 1, 23, 0], [(0x50D, 0), (0x50E, 0)])
FAILED = {"$": "STU_7BD2CA19", "m_resourceKey": None}


class FoldTests(unittest.TestCase):
    def instance(self):
        component = runtime.Component(123, strict=True)
        return component.create(make_graph(99), init=False)

    def test_native_loop_contract_cases(self):
        cases = [
            ("empty", reduce_config((), initial=integer(10), reducer=SUM), None),
            ("sum", reduce_config(initial=integer(0), reducer=SUM), 6),
            ("no initial", reduce_config(reducer=SUM), 6),
            ("no reducer", reduce_config(initial=integer(10)), 10),
            ("failed initial", reduce_config(initial=FAILED, reducer=SUM), 6),
            ("explicit final", reduce_config(initial=integer(0), reducer=SUM, final=parameter(0x50D)), 6),
        ]
        for name, config, expected in cases:
            with self.subTest(case=name):
                instance = self.instance()
                self.assertEqual(instance.evaluate(config), expected)
                self.assertEqual(instance.component.parameter_scopes, [])

    def test_empty_skips_initial_and_reducer_but_runs_final_with_scope_and_zero_count(self):
        instance = self.instance()
        calls = []

        def evaluate(world, config, active):
            calls.append((config["$"], active.context().param(0xC), active.context().param(0x41A),
                          active.context().param(0x50D)))
            return 17

        cfg = reduce_config((), initial={"$": "AuditInitial"}, reducer={"$": "AuditReducer"},
                            final={"$": "AuditFinal"})
        with patch.object(runtime.World, "native", evaluate):
            self.assertEqual(instance.evaluate(cfg), 17)
        self.assertEqual(calls, [("AuditFinal", 0, 0, None)])

    def test_failed_reducer_keeps_previous_accumulator_and_later_items_still_run(self):
        instance = self.instance()
        seen = []

        def evaluate(world, config, active):
            ctx = active.context()
            index, total, item = ctx.param(0xC), ctx.param(0x50D), ctx.param(0x50E)
            seen.append((index, total, item))
            return expr.EVALUATION_FAILED if index == 1 else expr.f32(total + item)

        with patch.object(runtime.World, "native", evaluate):
            result = instance.evaluate(reduce_config(initial=integer(0), reducer={"$": "AuditReducer"}))
        self.assertEqual(result, 4)
        self.assertEqual(seen, [(0, 0, 1), (1, 1, 2), (2, 1, 3)])

    def test_failed_final_preserves_destination_but_fold_scalar_still_succeeds(self):
        instance = self.instance()
        cfg = reduce_config(initial=integer(0), reducer=SUM, final=FAILED)
        self.assertEqual(instance.evaluate(cfg, initial=42), 42)
        self.assertEqual(play.evaluate_scalar_result(instance, cfg, initial=42), (42, True))
        group = {"$": "STU_06610EBC", "m_configVars": [cfg, integer(99)]}
        self.assertEqual(play.evaluate_scalar_result(instance, group, initial=42), (42, True))
        self.assertEqual(play.evaluate_value(instance, cfg, initial=0), 0)

    def test_provider_matches_native_lookup_and_preserves_failed_destination(self):
        scope = fold.Scope((3, 4), 7, 1)
        for key, expected in ((0xC, 1), (0x41A, 2), (0x50D, 7), (0x50E, 4)):
            self.assertEqual(scope.lookup_parameter(key, initial=42), (True, expected))
        for index in (-1, 2):
            scope.index = index
            self.assertEqual(scope.lookup_parameter(0x50E, initial=42), (False, 42))
        for key in (0x7777, True, None):
            self.assertEqual(scope.lookup_parameter(key, initial=42), (False, 42))
        scope.accumulator = None
        self.assertEqual(scope.lookup_parameter(0x50D, initial=42), (True, None))

    def test_final_index_is_count_and_invalid_item_falls_back_to_outer_scope(self):
        instance = self.instance()
        with instance.component.parameter_scope({0x50E: 81, 0x7777: 7}):
            self.assertEqual(instance.evaluate(reduce_config(final=parameter(0x50E))), 81)
            self.assertEqual(instance.evaluate(reduce_config(final=parameter(0x7777))), 7)
            self.assertEqual(instance.evaluate(reduce_config(final=parameter(0xC))), 3)
            self.assertEqual(instance.evaluate(reduce_config(final=parameter(0x41A))), 3)
            self.assertIsNone(instance.evaluate(reduce_config(final=parameter(0x50D))))

    def test_nested_fold_restores_outer_accumulator_and_item(self):
        instance = self.instance()
        inner = reduce_config((10, 20), initial=integer(0), reducer=SUM)
        outer_reducer = expression([14, 0, 13, 0, 23, 13, 1, 23, 0],
                                   [(0x50D, 0), (0x50E, 0)], configs=[inner])
        self.assertEqual(instance.evaluate(reduce_config((1, 2), initial=integer(0),
                                                         reducer=outer_reducer)), 63)
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_collection_is_frozen_before_callbacks_and_scope_is_pushed_after_collection(self):
        instance = self.instance()
        seen = []

        def evaluate(world, config, active):
            seen.append((config["$"], active.context().param(0x50E)))
            if config["$"] == "AuditContainer":
                return (1, 2, 3)
            active.component.set(55, (99,))
            return 0

        cfg = reduce_config({"$": "AuditContainer"}, initial={"$": "AuditInitial"}, reducer=SUM)
        with instance.component.parameter_scope({0x50E: 81}), patch.object(runtime.World, "native", evaluate):
            self.assertEqual(instance.evaluate(cfg), 6)
        self.assertEqual(seen, [("AuditContainer", 81), ("AuditInitial", 1)])
        instance.component.set(55, (1, 2, 3))
        cfg["m_814F8EC3"] = dynamic(55, 1)
        with patch.object(runtime.World, "native", evaluate):
            self.assertEqual(instance.evaluate(cfg), 6)
        self.assertEqual(instance.component.entity_var(55).value(), (99,))

    def test_collected_config_groups_and_nested_arrays_keep_null_members(self):
        instance = self.instance()
        values = {"$": "STU_06610EBC", "m_configVars": [FAILED, integer(1), (None, (2, 3))]}
        self.assertEqual(instance.evaluate(reduce_config(values, final=parameter(0x41A))), 4)
        self.assertEqual(instance.evaluate(reduce_config(values, initial=integer(0), reducer=SUM)), 6)

    def test_scope_is_retired_on_exception_and_nested_scope_is_restored(self):
        instance = self.instance()

        def fail(*args):
            raise ValueError("child evaluation failed")

        outer = {0x50D: 77}
        with instance.component.parameter_scope(outer), patch.object(runtime.World, "native", fail):
            with self.assertRaisesRegex(ValueError, "child evaluation failed"):
                instance.evaluate(reduce_config(initial={"$": "AuditFailure"}))
            self.assertEqual(instance.component.parameter_scopes, [outer])
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_collection_uses_the_shared_work_budget(self):
        instance = self.instance()
        with patch.object(runtime, "MAX_SCRIPT_WORK", 20), self.assertRaises(runtime.ScriptBudgetError):
            instance.evaluate(reduce_config(tuple(range(100)), reducer=SUM))
        self.assertEqual(instance.component.parameter_scopes, [])
        self.assertTrue(instance.stopped)

    def test_reducer_budget_failure_unwinds_an_installed_provider(self):
        instance = self.instance()
        seen = []

        def failed(world, component):
            seen.append(any(isinstance(scope, fold.Scope) for scope in component.parameter_scopes))

        with (
            patch.object(runtime, "MAX_SCRIPT_WORK", 8),
            patch.object(runtime.World, "script_failed", failed),
            self.assertRaises(runtime.ScriptBudgetError),
        ):
            instance.evaluate(reduce_config((1, 2, 3), reducer=SUM))
        self.assertEqual(seen, [True])
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_watcher_retains_child_variable_dependencies_without_polling_the_fold(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(make_graph(99, [node(0, state=0)], states=[0]), init=False)
        watcher = instance.state(0)
        watcher.begin()
        for key, value in ((55, (1, 2)), (56, 0), (57, 4)):
            component.set(key, value)
        config = reduce_config(dynamic(55, 1), initial=dynamic(56, 1), reducer=SUM, final=dynamic(57, 1))
        self.assertEqual(instance.evaluate(config, watcher), 4)
        self.assertTrue({component.entity_var(key) for key in (55, 56, 57)} <= set(watcher.watched))
        self.assertFalse(watcher.polled)
        self.assertFalse(watcher.natives)
        component.set(55, (3,))
        self.assertTrue(any(event.kind == runtime.CHANGED and event.state is watcher
                            for event in instance.queue))

    def test_cyclic_container_is_bounded_before_python_recursion(self):
        instance = self.instance()
        cfg = reduce_config()
        cfg["m_814F8EC3"] = cfg
        with self.assertRaisesRegex(runtime.ScriptBudgetError, "fold depth budget"):
            instance.evaluate(cfg)
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_live_provider_storage_is_passive_and_retains_accumulator_index_and_input(self):
        instance = self.instance()
        captured = []

        def evaluate(world, config, active):
            scope = active.component.parameter_scopes[-1]
            before = snapshot_digest(export_component_vm(active.component))
            scope.index += 1
            changed = snapshot_digest(export_component_vm(active.component))
            scope.index -= 1
            restored = snapshot_digest(export_component_vm(active.component))
            captured.append((before, changed, restored))
            return active.context().param(0x50E)

        with patch.object(runtime.World, "native", evaluate):
            self.assertEqual(instance.evaluate(reduce_config((3,), reducer={"$": "AuditCapture"})), 3)
        self.assertNotEqual(captured[0][0], captured[0][1])
        self.assertEqual(captured[0][0], captured[0][2])

    def test_original_0b8b_damage_metadata_subtraction_uses_actual_fold_expression(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(graph.graph(0x0B8B), init=False)
        records = tuple(expr.Map(((16056, expr.Asset(0x0D80000000000000 | key)), (499, amount)))
                        for key, amount in ((0x3EBB, 2), (0x123, 7), (0x3EBB, 3)))
        instance.write((0, 16100), records)
        value = instance.evaluate(instance.graph.node(83).field("m_value"))
        self.assertEqual(value, 5)
        instance.write((0, 16102), value)
        for amount, expected in ((20, 15), (3, 0)):
            instance.write((0, 336), amount)
            self.assertEqual(instance.evaluate(instance.graph.node(84).field("m_value")), expected)
        self.assertEqual(component.parameter_scopes, [])


if __name__ == "__main__":
    unittest.main()
