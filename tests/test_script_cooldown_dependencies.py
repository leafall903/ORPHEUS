"""Linked cooldown conditions recover after time elapses and State re-entry."""

import unittest

from test_script_runtime_budget import graph, node

from ow174.game.script import runtime


def dynamic(identifier):
    return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": 0}


class CooldownDependencyTests(unittest.TestCase):
    def make(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(graph([
            node(0, "STUStatescriptStateAbility", state=0, m_out_CooldownVar=dynamic(61)),
            node(1, "STUStatescriptStateBooleanSwitch", state=1, m_condition=dynamic(61)),
        ]), init=False)
        instance.initialized = True
        instance.write((0, 61), 0.1)
        ability, switch = instance.state(0), instance.state(1)
        ability.begin()
        switch.begin()
        component.run_now()
        return component, instance, ability, switch

    def test_time_updates_notify_a_condition_without_another_variable_write(self):
        component, instance, ability, switch = self.make()
        self.assertTrue(switch.current)
        component.now = 50
        component.run_now()
        self.assertTrue(switch.current)
        component.now = 101
        component.run_now()
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(switch.current)
        instance.write((0, 61), 0.2)
        component.run_now()
        self.assertTrue(switch.current)
        component.now = 302
        component.run_now()
        self.assertFalse(switch.current)

    def test_cancelled_notification_does_not_poison_a_reentered_switch(self):
        component, instance, _ability, switch = self.make()
        instance.write((0, 61), 0)
        self.assertTrue(switch.flags_changed)
        switch.end(False)
        switch.begin()
        self.assertFalse(switch.current)
        instance.write((0, 61), 0.2)
        component.run_now()
        self.assertTrue(switch.current)
        component.now = 201
        component.run_now()
        self.assertFalse(switch.current)


if __name__ == "__main__":
    unittest.main()
