"""Canceled notifications cannot poison a later activation or cleanup writes."""

import unittest
from unittest.mock import patch

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

    def test_cleanup_writes_do_not_notify_the_ending_state(self):
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

    def test_subgraph_exit_only_touches_existing_states(self):
        component = runtime.Component(1, strict=True)
        instance = component.create(graph([
            node(0, 'STUStatescriptState', state=0, m_subgraphPlug={
                '$': 'STU_904BFCEC', 'm_FF3DAF1E': [1, 2, 3, 4], 'links': [],
            }),
            node(1, 'STUStatescriptState', state=1),
            node(2, 'STUStatescriptState', state=2),
            node(3, 'STU_UnvisitedState', state=3),
            node(4, 'STUStatescriptState', state=4),
        ]))
        active = instance.state(1)
        active.begin()
        pending = instance.state(2)
        pending.request(runtime.BEGIN)

        with patch.object(active, 'on_end', side_effect=lambda _: instance.state(4).request(runtime.BEGIN)):
            instance.exit_subgraph(instance.graph.node(0), 'm_subgraphPlug')
        component.run_now()

        self.assertFalse(active.active)
        self.assertFalse(pending.active)
        self.assertNotIn(3, instance.states)
        self.assertEqual(instance.states[4].counter, 0)
