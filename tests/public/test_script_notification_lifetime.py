"""Canceled notification markers must not suppress a later state activation."""

import unittest
from unittest.mock import patch

import test_script_join as join_tests

from ow174.game.script import runtime
from test_script_runtime_budget import graph, node, plug


class NotificationLifetimeTests(unittest.TestCase):
    def test_cooldown_zero_notifies_an_authored_gate_at_its_scheduled_boundary(self):
        cooldown = {'$': 'STUConfigVarDynamic', 'm_identifier': 61, 'm_60DB8F99': 0}
        component = runtime.Component(1, frame_ms=1)
        instance = component.create(graph([
            node(0, 'STUStatescriptStateAbility', state=0, m_out_CooldownVar=cooldown),
            node(1, 'STUStatescriptStateBooleanSwitch', state=1, m_condition={
                '$': 'STU_919FD47C', 'm_expression': {
                    '$': 'STUConfigVarExpressionData', 'm_opcodes': [8, 0, 6, 0, 37, 0],
                    'm_dynamicVars': [cooldown], 'm_D99EF254': [0]},
            }),
        ]), init=False)
        instance.initialized = True
        instance.write((runtime.INSTANCE, 61), 1.0)
        for index in (0, 1):
            instance.state(index).request(runtime.BEGIN)
        component.run_now()
        gate = instance.state(1)
        self.assertFalse(gate.current)
        component.run_frame(999)
        self.assertFalse(gate.current)
        component.run_frame(1000)
        self.assertTrue(gate.current)

    def scene(self, extra=()):
        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, 'STUStatescriptStateBooleanSwitch', state=0,
            m_condition={'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0},
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
        self.assertTrue(state.flags_changed)
        self.assertTrue(any(event.kind == runtime.CHANGED for event in instance.queue))
        state.end(False)
        self.assertFalse(state.flags_changed)
        self.assertFalse(any(event.state is state for event in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.current)
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)

    def test_cleanup_writes_do_not_enqueue_notifications_for_the_ending_state(self):
        component, instance, state = self.scene()
        with patch.object(state, 'on_end',
                          side_effect=lambda _: instance.write((runtime.INSTANCE, 41), True)):
            state.end(False)
        self.assertFalse(state.flags_changed)
        self.assertFalse(any(event.state is state for event in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.current)
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)

    def test_canceled_join_check_can_be_queued_after_state_reentry(self):
        case = join_tests.JoinTests()
        component, instance = case.setup_join()
        first, second = instance.states[0], instance.states[1]
        self.assertFalse(case.arrive(instance, 4, caller=first))
        self.assertTrue(second.flags_check)
        self.assertTrue(any(event.kind == runtime.CHECK and event.state is second
                            for event in instance.queue))
        second.end(False)
        self.assertFalse(second.flags_check)
        self.assertFalse(any(event.state is second for event in instance.queue))
        self.assertTrue(case.arrive(instance, 5))
        for state in (first, second):
            state.request(runtime.BEGIN)
        component.run_now()
        self.assertFalse(case.arrive(instance, 4, caller=first))
        self.assertTrue(any(event.kind == runtime.CHECK and event.state is second
                            for event in instance.queue))

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


    def test_canceled_join_check_can_be_queued_again_after_reentry(self):
        import test_script_join as joins

        helper = joins.JoinTests()
        component, instance = helper.setup_join()
        state = instance.states[0]
        helper.arrive(instance, 4)
        self.assertTrue(state.flags_check)
        state.end(False)
        self.assertFalse(state.flags_check)
        helper.arrive(instance, 5)
        component.run_now()
        state.request(runtime.BEGIN)
        component.run_now()
        helper.arrive(instance, 4)
        self.assertTrue(any(event.state is state and event.kind == runtime.CHECK
                            for event in instance.queue))



if __name__ == "__main__":
    unittest.main()
