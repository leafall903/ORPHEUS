"""Scoped invocation preserves the receiving component and sibling instances."""

import unittest
from unittest.mock import patch

from ow174.game.script import runtime
from tests.script_fixtures import graph, node, plug


class ObservedInstanceClockTests(unittest.TestCase):
    def test_recorded_seven_ms_cursor_and_due_timer_do_not_update_sibling(self):
        component = runtime.Component(123, frame_ms=7, strict=True)
        component.frame, component.now = 1040, 7286
        sibling = component.create(graph([node(0, 'STUStatescriptEntry',
            m_outPlug=plug((1, 'm_inPlug'))), node(1, 'Sibling')], entries=[0]))
        sibling_queue = list(sibling.queue)
        definition = graph([
            node(0, 'STU_BB4AC733', m_outPlug=plug((1, 'm_beginPlug'))),
            node(1, 'STUStatescriptStateWait', state=0,
                 m_timeout={'$': 'STUConfigVarFloat', 'm_value': .010},
                 m_onFinishedPlug=plug((2, 'm_inPlug'))),
            node(2, 'Done'),
        ], entries=[0])
        seen = []

        def observe(instance, current, caller):
            seen.append((current.cls, component.frame, component.now))
            return True

        with patch.dict(runtime.HANDLERS, Done=observe, Sibling=observe):
            with component.observed_clock(1000, 7006):
                incoming = component.play_loaded_graph(definition, source=456, policy=1, instigator=456)
            component.advance_instance_until(incoming, 7006, 7020)
        self.assertEqual(incoming.start, 7006)
        self.assertEqual(seen, [('Done', 1002, 7016)])
        self.assertEqual(sibling.queue, sibling_queue)
        self.assertEqual((component.frame, component.now), (1040, 7286))
        self.assertEqual(incoming.source_entity(), 456)

    def test_invalid_or_failed_scope_restores_component_and_rejects_stale_instance(self):
        component = runtime.Component(123)
        component.frame, component.now = 100, 1615
        for clock in ((99, 1615), (101, 1616), (-1, -1), (True, 16)):
            with self.assertRaises(ValueError), component.observed_clock(*clock):
                self.fail('invalid cursor entered')
        with self.assertRaisesRegex(RuntimeError, 'probe'), component.observed_clock(99, 1599):
            raise RuntimeError('probe')
        self.assertEqual((component.frame, component.now), (100, 1615))
        incoming = component.create(graph())
        component.destroy(incoming)
        with self.assertRaises(ValueError):
            component.advance_instance_until(incoming, 1615, 1615)
