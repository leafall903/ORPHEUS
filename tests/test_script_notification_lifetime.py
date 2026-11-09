"""Canceled notification markers must not suppress a later state activation."""

import unittest
from unittest.mock import patch

import test_script_join as join_tests

from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node


class NotificationLifetimeTests(unittest.TestCase):
    def scene(self):
        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, 'STUStatescriptStateBooleanSwitch', state=0,
            m_condition={'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0},
        )]), init=False)
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
