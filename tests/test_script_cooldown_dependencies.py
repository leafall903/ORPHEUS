"""Clock-dependent gates and canceled notifications must survive state reuse."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node, output

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import runtime


def dynamic(identifier):
    return {"$": "STUConfigVarDynamic", "m_identifier": identifier}


class CooldownDependencyTests(unittest.TestCase):
    def scene(self):
        definition = make_graph(1, [
            node(0, "STUStatescriptStateAbility", state=0, fields={"m_out_CooldownVar": dynamic(61)}),
            node(1, "STUStatescriptStateBooleanSwitch", state=1, fields={
                "m_condition": dynamic(61), "m_onFalsePlug": output([2, "m_inPlug"]),
            }),
            node(2, "STUStatescriptActionSetVar", fields={
                "m_out_Var": dynamic(77), "m_value": {"$": "STUConfigVarInt", "m_value": 9},
            }),
        ], states=[0, 1])
        component = runtime.Component(123, strict=True)
        instance = component.create(definition, init=False)
        instance.init()
        instance.write((0, 61), 1.0)
        for index in (0, 1):
            instance.state(index).begin()
        component.run_now()
        return component, instance, instance.state(0), instance.state(1)

    def test_clock_expiry_notifies_watched_gate_without_another_write(self):
        component, instance, ability, switch = self.scene()
        self.assertTrue(switch.current)
        for time in (400, 800, 999):
            component.now = time
            component.update()
            self.assertTrue(switch.current)
        component.now = 1000
        component.update()
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(switch.current)
        self.assertEqual(instance.vars[77].value(), 9)

    def test_canceled_dependency_event_does_not_poison_restarted_switch(self):
        component, instance, _, switch = self.scene()
        instance.write((0, 61), 0.0)
        self.assertTrue(switch.flags_changed)
        switch.end(False)  # cancel the queued CHANGED before it can dispatch
        self.assertFalse(switch.flags_changed)
        instance.write((0, 61), 1.0)
        switch.begin()
        self.assertTrue(switch.current)
        instance.write((0, 61), 0.0)
        component.run_now()
        self.assertFalse(switch.current)
        self.assertEqual(instance.vars[77].value(), 9)

    def test_cancel_clears_completion_coalescer_and_all_queued_work(self):
        _, instance, _, switch = self.scene()
        switch.flags_check = True
        instance.post(runtime.Event(0, 0, runtime.CHECK, state=switch))
        instance.cancel(switch)
        self.assertFalse(switch.flags_check)
        self.assertFalse(any(event.state is switch for event in instance.queue))

    def test_clock_sample_is_causal_storage_and_export_does_not_read_getters(self):
        component, _, ability, _ = self.scene()
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(export_component_vm(component))
            self.assertEqual(snapshot_digest(export_component_vm(component)), before)
            ability.last_sample = .5
            self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)


if __name__ == "__main__":
    unittest.main()
