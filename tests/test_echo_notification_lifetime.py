"""Shared notification lifetime regressions, reviewed from parent's minimal cases."""
import unittest
from unittest.mock import patch

from test_script_runtime_budget import graph, node

from ow174.game.script import runtime


class EchoNotificationLifetimeTests(unittest.TestCase):
    def scene(self):
        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, 'STUStatescriptStateBooleanSwitch', state=0,
            m_condition={'$':'STUConfigVarDynamic', 'm_identifier':41, 'm_60DB8F99':0},
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
        state.end(False)
        self.assertFalse(state.flags_changed)
        self.assertFalse(any(e.state is state for e in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.current)
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)

    def test_cleanup_write_does_not_schedule_for_ending_state(self):
        component, instance, state = self.scene()
        with patch.object(state, 'on_end',
                          side_effect=lambda _: instance.write((runtime.INSTANCE, 41), True)):
            state.end(False)
        self.assertFalse(state.flags_changed)
        self.assertFalse(any(e.state is state for e in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        instance.write((runtime.INSTANCE, 41), False)
        component.run_now()
        self.assertFalse(state.current)
