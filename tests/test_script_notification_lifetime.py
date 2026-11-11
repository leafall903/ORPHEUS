"""Canceled notification markers must not suppress a later state activation."""

import unittest
from unittest.mock import patch

from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node


class NotificationLifetimeTests(unittest.TestCase):
    def scene(self, *, joins=False):
        component = runtime.Component(1)
        records = [node(
            0, 'STUStatescriptStateBooleanSwitch', state=0,
            m_condition={'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0},
        )]
        if joins:
            records.extend(node(index, 'STU_1113EBD5', m_122DF0CA=[0], m_B24D1794=2)
                           for index in (1, 2))
        instance = component.create(graph(records), init=False)
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

    def test_canceled_join_check_does_not_suppress_a_later_join_after_reentry(self):
        component, instance, state = self.scene(joins=True)
        for index in (1, 2):
            instance.plug_source = (10+index, 'm_outPlug')
            self.assertFalse(instance.join(instance.graph.node(index), None))
            self.assertTrue(state.flags_check)
            self.assertTrue(any(event.kind == runtime.CHECK and event.state is state
                                for event in instance.queue))
            if index == 1:
                state.end(False)
                self.assertFalse(state.flags_check)
                self.assertFalse(any(event.state is state for event in instance.queue))
                state.request(runtime.BEGIN)
                component.run_now()
            else:
                component.run_now()
                self.assertFalse(state.flags_check)
