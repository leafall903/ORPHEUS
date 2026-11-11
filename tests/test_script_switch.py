"""Native Switch null-case defaults must select and exit actual phase branches."""

import unittest

from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node, plug, setter


def dynamic(number):
    return {"$": "STUConfigVarDynamic", "m_identifier": number, "m_60DB8F99": 0}


def scene(value, mode=1, explicit=None):
    component = runtime.Component(123, strict=True)
    cases = [{"$": "STU_ACACAD83", **plug((2 + index, "m_beginPlug")),
              "m_FF3DAF1E": [1 + index]}
             for index in range(3)]
    if explicit is not None:
        cases[1]["m_value"] = {"$": "STUConfigVarInt", "m_value": explicit}
    items = [
        node(0, "STUStatescriptEntry", m_outPlug=plug((1, "m_beginPlug"))),
        node(1, "STU_DEBD057B", state=0, m_F0F0720B=mode,
             m_262AE982=dynamic(8), m_05706D1C=cases,
             m_A5EF385D=plug((5, "m_inPlug"))),
    ]
    for index in range(3):
        items.append(node(2 + index, "STUStatescriptStateStack", state=1 + index,
                          m_out_Var=dynamic(7),
                          m_value={"$": "STUConfigVarInt", "m_value": 10 + index}))
    items.append(setter(5, 99))
    instance = component.create(graph(items, entries=[0]))
    instance.write((0, 8), value)
    component.run_frame(0)
    return component, instance


class SwitchPhaseTests(unittest.TestCase):
    def test_indexed_phases_enter_then_exit_previous_branch(self):
        component, instance = scene(0)
        self.assertEqual(instance.vars[7].value(), 10)
        instance.write((0, 8), 2)
        component.run_frame(1)
        self.assertEqual(instance.vars[7].value(), 12)
        self.assertFalse(instance.state(1).active)
        self.assertTrue(instance.state(3).active)
        instance.write((0, 8), -1)
        component.run_frame(2)
        self.assertEqual(instance.vars[7].value(), 99)
        self.assertFalse(instance.state(3).active)

    def test_unknown_phase_does_not_match_missing_case_value(self):
        _, instance = scene(8)
        self.assertEqual(instance.vars[7].value(), 99)

    def test_explicit_value_overrides_index(self):
        _, instance = scene(99, explicit=99)
        self.assertEqual(instance.vars[7].value(), 11)

    def test_nonindexed_missing_values_are_zero(self):
        _, instance = scene(0, mode=0)
        self.assertEqual(instance.vars[7].value(), 10)
        _, instance = scene(2, mode=0)
        self.assertEqual(instance.vars[7].value(), 99)
