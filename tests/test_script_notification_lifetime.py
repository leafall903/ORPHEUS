"""Canceled notifications must not suppress a later activation of the state.

Reviewed shared regressions from the parent's build104319 runtime fix.
"""

import unittest
from unittest.mock import patch

from test_script_runtime_budget import graph, node

from ow174.game.script import runtime


class NotificationLifetimeTests(unittest.TestCase):
    def scene(self):
        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, "STUStatescriptStateBooleanSwitch", state=0,
            m_condition={"$": "STUConfigVarDynamic", "m_identifier": 41, "m_60DB8F99": 0},
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
        with patch.object(state, "on_end",
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

    def test_stack_predicate_tracks_pop_with_equal_null_values(self):
        class TopologyWorld(runtime.World):
            def stack_topology_queries(self, instance):
                return True

        component = runtime.Component(1, world=TopologyWorld())
        instance = component.create(graph([
            node(0, "STUStatescriptStateBooleanSwitch", state=0,
                 m_condition={"$": "STU_3ACE35FB", "m_E50CF556": {
                     "$": "STUConfigVarDynamic", "m_identifier": 41, "m_60DB8F99": 0}}),
            node(1, "STUStatescriptState", state=1),
        ]), init=False)
        instance.initialized = True
        gate, blocker = instance.state(0), instance.state(1)
        blocker.begin()
        var = blocker.link((runtime.INSTANCE, 41), priority=1, stack=True)
        gate.begin()
        component.run_now()
        self.assertIsNone(var.value())
        self.assertFalse(gate.current)
        blocker.unlink(var)
        self.assertIsNone(var.value())
        component.run_now()
        self.assertTrue(gate.current)
