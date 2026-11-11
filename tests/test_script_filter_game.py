"""Filter-stage service boundaries, vetoes and live candidate namespace reads."""

import unittest
from unittest.mock import Mock, patch

from test_game_graph_analysis import make_graph, node
from test_script_filter_condition import PARAMETER

from ow174.game.script import expr, filter_callbacks, filter_condition, filter_game, runtime


def fixture():
    target, source = object(), object()
    services = Mock(spec=("lookup", "has_component", "bits", "source", "observe", "missing_target",
                          "relationship", "observe_script_filters", "script_filters", "engine_filter",
                          "extension_filter"))
    services.lookup.return_value = target
    services.has_component.return_value = True
    services.bits.return_value = 0x40001
    services.source.return_value = source
    services.engine_filter.return_value = 2
    services.script_filters.return_value = True
    services.extension_filter.return_value = True
    return services


FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x40000, "m_4EA686DB": 0x130,
          "m_C1903771": {"$": "declared_registry_key"}}


class GameFilterTests(unittest.TestCase):
    def instance(self):
        component = runtime.Component(100, world=runtime.World(1, 1), strict=True)
        return component.create(make_graph(1, [node(0, state=0)], states=[0]), init=False)

    def test_null_filter_does_not_touch_unavailable_services_or_validate_unused_candidate(self):
        self.assertTrue(filter_game.evaluate(None, None, -1, services=None))

    def test_missing_target_and_zero_mask_component_guard_preserve_native_branching(self):
        services = fixture()
        services.lookup.return_value = None
        self.assertFalse(filter_game.evaluate(None, FILTER, 102, services=services))
        services.missing_target.assert_called_once_with(None, None, 102)
        services.engine_filter.assert_not_called()
        services = fixture()
        services.has_component.return_value = False
        self.assertFalse(filter_game.evaluate(None, FILTER, 102, services=services))
        services.bits.assert_not_called()
        # D0DA20 skips BCAAE0 when every mask is zero; this differs from calling
        # the primitive with any=0. A missing97 does not reject this branch.
        services.bits.return_value = None
        services.source.return_value = None
        self.assertTrue(filter_game.evaluate(None, {"$": "STUConfigVarFilter"}, 102, services=services))
        services.engine_filter.assert_not_called()

    def test_empty_callback_rules_ignore_populated_registry_but_do_not_accept_unknown_rules(self):
        services = fixture()
        services.engine_filter.side_effect = AssertionError("no nonzero registry key")
        actual_shape = {"$": "STUConfigVarFilter", "m_84065FD1": 0x40000,
                        "m_4EA686DB": 0x130, "m_93FA1642": None, "m_C1903771": None}
        self.assertTrue(filter_game.evaluate(None, actual_shape, 102, services=services))
        for rules in (None, [], ()):
            self.assertEqual(filter_callbacks.empty_result(dict(actual_shape, m_93FA1642=rules)), 2)
        self.assertEqual(filter_callbacks.empty_result(None), 2)
        self.assertIs(filter_callbacks.empty_result(dict(actual_shape, m_93FA1642=[{}])), NotImplemented)
        self.assertIs(filter_callbacks.empty_result(dict(actual_shape, m_C1903771={})), NotImplemented)
        for rules in (False, 0, "", {}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                filter_callbacks.empty_result(dict(actual_shape, m_93FA1642=rules))

    def test_exact_wildcard_skips_presence_guard_but_preserves_other_masks(self):
        services = fixture()
        config = dict(FILTER, m_84065FD1=0x3FFFFFFF, m_1E14CCA8=2)
        self.assertFalse(filter_game.evaluate(None, config, 102, services=services))
        services.has_component.assert_not_called()
        services.engine_filter.assert_not_called()

    def test_callback_one_vetoes_and_unknown_or_missing_services_are_not_defaulted(self):
        for value in (0, 1, 2, 0xFFFFFFFF, True, NotImplemented):
            with self.subTest(callback=value):
                services = fixture()
                services.engine_filter.return_value = value
                if type(value) is not int:
                    with self.assertRaisesRegex(ValueError, "uint32"):
                        filter_game.evaluate(None, FILTER, 102, services=services)
                else:
                    self.assertEqual(filter_game.evaluate(None, FILTER, 102, services=services), value != 1)
        services = fixture()
        services.engine_filter.side_effect = NotImplementedError("registry unavailable")
        with self.assertRaisesRegex(NotImplementedError, "registry unavailable"):
            filter_game.evaluate(None, FILTER, 102, services=services)
        with self.assertRaises(AttributeError):
            filter_game.evaluate(None, FILTER, 102, services=object())

    def test_candidate_member_flag_rejects_before_callback_and_keeps_foreign_watch(self):
        instance = self.instance()
        state = instance.node_state(instance.graph.node(0))
        state.active = True
        slot = runtime.Var((102, 14695))
        slot.base = True
        instance.component.set(14695, False)
        condition = {"$": "STU_919FD47C", "m_expression": {
            "$": "STUConfigVarExpressionData", "m_opcodes": [13, 0, 7, 1, 15, 30, 0],
            "m_dynamicVars": [{"$": "STUConfigVarDynamic", "m_identifier": 8},
                              {"$": "STUConfigVarDynamic", "m_identifier": 14695}]}}
        services = fixture()
        with (instance.component.parameter_scope({8: expr.Entity(103)}),
              patch.object(instance.component.world, "entity_var_slot", return_value=slot)):
            self.assertFalse(filter_game.evaluate(instance, dict(FILTER, m_condition=condition), 102,
                                                  services=services, watcher=state))
            self.assertEqual(instance.context().param(8), expr.Entity(103))
            self.assertIn(state, slot.watchers)
            services.engine_filter.assert_not_called()
            slot.base = False
            self.assertTrue(filter_game.evaluate(instance, dict(FILTER, m_condition=condition), 102,
                                                 services=services, watcher=state))
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_additional_expression_uses_outer_scope_and_false_failure_default(self):
        instance = self.instance()
        services = fixture()
        config = dict(FILTER, m_condition=PARAMETER, m_F61726FE=PARAMETER)
        # Candidate condition sees102. Additional expression sees the outer0,
        # because native candidate binding ends on return from1B8D970.
        with instance.component.parameter_scope({8: expr.Entity(0)}):
            self.assertTrue(filter_game.evaluate(instance, config, 102, services=services))
            services.extension_filter.assert_not_called()
        with instance.component.parameter_scope({8: expr.Entity(103)}):
            services.extension_filter.return_value = False
            self.assertFalse(filter_game.evaluate(instance, config, 102, services=services))
            services.extension_filter.assert_called_once()
        with patch.object(instance.component.world, "native", return_value=expr.EVALUATION_FAILED):
            services.extension_filter.reset_mock()
            config["m_F61726FE"] = {"$": "declared_failure"}
            self.assertTrue(filter_game.evaluate(instance, config, 102, services=services))
            services.extension_filter.assert_not_called()
        self.assertEqual(instance.component.parameter_scopes, [])

    def test_expression_default_distinguishes_failure_null_and_empty_array(self):
        instance = self.instance()
        for default in (False, True):
            self.assertEqual(filter_condition.evaluate_boolean(None, None, default=default), default)
            for value, expected in ((expr.EVALUATION_FAILED, default), (None, False), ((), default)):
                with (self.subTest(default=default, value=value),
                      patch.object(instance.component.world, "native", return_value=value)):
                    self.assertEqual(filter_condition.evaluate_boolean(
                        instance, {"$": "declared_value"}, default=default), expected)


if __name__ == "__main__":
    unittest.main()
