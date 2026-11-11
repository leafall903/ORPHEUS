"""Canceled state notifications cannot suppress a later activation."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node

from ow174.game.script import runtime


class NotificationLifetimeTests(unittest.TestCase):
    def scene(self):
        definition = make_graph(1, [node(0, 'STUStatescriptStateBooleanSwitch', state=0, fields={
            'm_condition': {'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0},
        })], states=[0])
        component = runtime.Component(123)
        instance = component.create(definition, init=False)
        instance.initialized = True
        instance.write((runtime.INSTANCE, 41), False)
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_now()
        return component, instance, state

    def test_canceled_change_is_not_latched_across_state_reentry(self):
        component, instance, state = self.scene()
        instance.write((runtime.INSTANCE, 41), True)
        self.assertTrue(state.flags_changed)
        state.end(False)
        self.assertFalse(state.flags_changed)
        self.assertFalse(state.flags_check)
        self.assertFalse(any(event.state is state for event in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.current)
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)

    def test_cleanup_write_cannot_queue_a_notification_for_an_ending_state(self):
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
