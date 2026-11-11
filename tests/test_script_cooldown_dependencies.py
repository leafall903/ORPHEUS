"""Reviewed barrier-recovery prerequisites: cancelled callbacks and cooldown zero."""

import unittest

from test_script_runtime_budget import graph, node

from ow174.game.script import runtime


def variable(identifier):
    return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": 0}


class CooldownDependencyTests(unittest.TestCase):
    def test_restarted_switch_receives_change_after_queued_callback_was_cancelled(self):
        component = runtime.Component(0xA0000101)
        instance = component.create(graph([
            node(0, "STUStatescriptStateBooleanSwitch", state=0, m_condition=variable(77)),
        ]))
        switch = instance.state(0)
        switch.begin()
        instance.write((0, 77), True)
        self.assertTrue(switch.flags_changed)
        switch.end(False)
        switch.begin()
        self.assertTrue(switch.current)
        instance.write((0, 77), False)
        component.run_now()
        self.assertFalse(switch.current)

    def test_computed_cooldown_zero_notifies_waiting_ready_condition(self):
        condition = {"$": "STU_919FD47C", "m_expression": {
            "$": "STUConfigVarExpressionData", "m_opcodes": [8, 0, 6, 0, 37, 0],
            "m_dynamicVars": [variable(61)], "m_D99EF254": [0],
        }}
        component = runtime.Component(0xA0000101)
        instance = component.create(graph([
            node(0, "STUStatescriptStateAbility", state=0, m_out_CooldownVar=variable(61)),
            node(1, "STUStatescriptStateBooleanSwitch", state=1, m_condition=condition),
        ]))
        instance.write((0, 61), .032)
        instance.state(0).begin()
        ready = instance.state(1)
        ready.begin()
        component.run_frame(0)
        self.assertFalse(ready.current)
        component.run_frame(2)
        self.assertEqual(instance.find_var(0, 61).value(), 0)
        self.assertTrue(ready.current)
