"""External messages retain their world timestamp without advancing an owner's VM."""

import unittest

from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node, plug, setter


class WorldMessageClockTests(unittest.TestCase):
    def setup_message(self):
        component = runtime.Component(123)
        model = graph([
            node(0, 'STUStatescriptEntryGameMessage',
                 m_gameMessage={'m_gameMessage': '0x0240000000000001'},
                 m_outPlug=plug((1, 'm_inPlug'))),
            setter(1, 42),
        ])
        instance = component.create(model, init=False)
        instance.initialized = True
        component.now = 100
        return component, instance

    def test_future_message_does_not_dispatch_during_historical_catchup(self):
        component, instance = self.setup_message()
        self.assertEqual(runtime.send_message(component, 0x0240000000000001, {}, 123,
                                              at_time=1000), 1)
        self.assertEqual(component.now, 100)
        component.now = 999
        instance.drain()
        self.assertNotIn(7, instance.vars)
        component.now = 1000
        instance.drain()
        self.assertEqual(instance.vars[7].value(), 42)

    def test_default_and_past_messages_use_current_component_time(self):
        for when in (None, 0):
            with self.subTest(at_time=when):
                _component, instance = self.setup_message()
                runtime.send_instance_message(instance, 0x0240000000000001, {}, 123, at_time=when)
                self.assertEqual(instance.queue[0].time, 100)
                instance.drain()
                self.assertEqual(instance.vars[7].value(), 42)
