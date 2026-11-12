"""54FEB21C native empty-output timer contract; no automatic authority response."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node, output

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import runtime


def scalar(value):
    return {"$": "STUConfigVarFloat", "m_value": value}


class ConditionalTimerTests(unittest.TestCase):
    def fixture(self, duration=0, condition=None, **extra):
        definition = make_graph(1, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STU_54FEB21C", state=0, fields={
                "m_ED97A97C": scalar(duration), "m_7C8AD485": condition,
                "m_onFinishedPlug": output([2, "m_inPlug"]), **extra,
            }),
            node(2, "STUStatescriptActionSetVar", fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 7},
                "m_value": {"$": "STUConfigVarInt", "m_value": 9},
            }),
        ], entries=[0], states=[1])
        component = runtime.Component(123, strict=True)
        instance = component.create(definition)
        component.run_now()
        return component, instance

    def test_native_timer_cases_keep_missing_false_and_other_parameters_active(self):
        for condition, expected in ((None, True), (scalar(0), True), (scalar(1), False)):
            with self.subTest(condition=condition):
                _component, instance = self.fixture(condition=condition)
                self.assertEqual(instance.states[0].active, expected)
                value = instance.vars.get(7)
                self.assertEqual(value.value() if value else None, None if expected else 9)
        component, instance = self.fixture(duration=1, condition=scalar(1))
        instance.states[0].timer(2)
        component.run_now()
        self.assertTrue(instance.states[0].active)

    def test_positive_delay_ends_in_the_frame_containing_its_authored_time(self):
        component, instance = self.fixture(duration=0.5, condition=scalar(1))
        component.run_frame(30)
        self.assertTrue(instance.states[0].active)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            self.assertTrue(export_component_vm(component))
        component.run_frame(31)
        self.assertFalse(instance.states[0].active)
        self.assertEqual(instance.vars[7].value(), 9)

    def test_false_condition_is_checked_once_not_watched_or_rearmed(self):
        condition = {"$": "STUConfigVarDynamic", "m_identifier": 8}
        component, instance = self.fixture(condition=condition)
        instance.write((0, 8), True)
        component.run_frame(100)
        self.assertTrue(instance.states[0].active)
        self.assertFalse(instance.states[0].watched)
        self.assertFalse(instance.queue)

    def test_unhosted_engine_output_is_refused_in_strict_execution(self):
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.fixture(m_C7A42E2A={"$": "STU_076E0DBA"})


if __name__ == "__main__":
    unittest.main()
