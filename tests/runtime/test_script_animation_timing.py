"""Native104319 CBD58F/CC1E70 field independence and loop/state completion."""

import unittest

from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node


class NativeAnimFlagsTests(unittest.TestCase):
    def scene(self, *, loop=False, suppress=False, client=False):
        def boolean(value):
            return {"$": "STUConfigVarBool", "m_value": value}

        component = runtime.Component(1)
        instance = component.create(graph([node(
            0, "STUStatescriptStateAnim", state=0,
            m_86701C3E={"$": "STUConfigVarFloat", "m_value": .1},
            m_37EE887D=boolean(loop), m_2DEAE6A9=boolean(suppress),
            m_154DBA91=boolean(client),
        )]), init=False)
        instance.initialized = True
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_now()
        return component, instance, state

    def test_client_playback_flag_does_not_suppress_state_finish(self):
        component, _instance, state = self.scene(client=True)
        component.now = 200
        component.run_now()
        self.assertFalse(state.active)

    def test_native_suppression_flag_keeps_state_after_finished_timer(self):
        component, instance, state = self.scene(suppress=True)
        component.now = 200
        component.run_now()
        self.assertTrue(state.active)
        self.assertFalse(instance.queue)

    def test_loop_does_not_schedule_duration_timer_or_state_finish(self):
        _component, instance, state = self.scene(loop=True)
        self.assertTrue(state.active)
        self.assertFalse(instance.queue)
