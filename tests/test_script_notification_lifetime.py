"""Canceled notifications must not suppress a state's subsequent activation."""

import unittest
from unittest.mock import patch

from test_script_runtime_budget import graph, node, plug

from ow174.game.script import runtime


class NotificationLifetimeTests(unittest.TestCase):
    def scene(self, extra=()):
        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, "STUStatescriptStateBooleanSwitch", state=0,
            m_condition={"$": "STUConfigVarDynamic", "m_identifier": 41, "m_60DB8F99": 0},
        ), *extra]), init=False)
        instance.initialized = True
        instance.write((runtime.INSTANCE, 41), False)
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_now()
        return component, instance, state

    def test_canceled_change_does_not_block_notifications_after_reentry(self):
        component, instance, state = self.scene()
        instance.write((runtime.INSTANCE, 41), True)
        self.assertTrue(any(event.kind == runtime.CHANGED for event in instance.queue))
        state.end(False)
        self.assertFalse(any(event.state is state for event in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.current)
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)

    def test_cleanup_writes_do_not_queue_notifications_for_the_ending_state(self):
        component, instance, state = self.scene()
        with patch.object(state, "on_end",
                          side_effect=lambda _: instance.write((runtime.INSTANCE, 41), True)):
            state.end(False)
        self.assertFalse(any(event.state is state for event in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.current)
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)

    def test_canceled_join_check_does_not_suppress_a_later_join_activation(self):
        component, instance, state = self.scene((
            node(1, "STU_1113EBD5", m_B24D1794=2, m_122DF0CA=[0],
                 m_outPlug=plug((4, "m_inPlug"))),
            node(2, "STUStatescriptActionSetVar", m_outPlug=plug((1, "m_inPlug"))),
            node(3, "STUStatescriptActionSetVar", m_outPlug=plug((1, "m_inPlug"))),
            node(4, "STUStatescriptActionSetVar",
                 m_out_Var={"$": "STUConfigVarDynamic", "m_identifier": 42, "m_60DB8F99": 0},
                 m_value={"$": "STUConfigVarBool", "m_value": True}),
        ))
        instance.follow(instance.graph.node(2), "m_outPlug")
        self.assertTrue(any(event.kind == runtime.CHECK for event in instance.queue))
        state.end(False)
        state.request(runtime.BEGIN)
        component.run_now()
        instance.follow(instance.graph.node(3), "m_outPlug")
        component.run_now()
        self.assertTrue(instance.vars[42].value())
        self.assertFalse(state.active)
        state.request(runtime.BEGIN)
        component.run_now()
        instance.follow(instance.graph.node(2), "m_outPlug")
        self.assertTrue(any(event.kind == runtime.CHECK for event in instance.queue))


if __name__ == "__main__":
    unittest.main()
