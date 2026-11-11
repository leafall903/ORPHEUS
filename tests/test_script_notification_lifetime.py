"""Canceled notification markers must not suppress a later state activation."""

import unittest
from unittest.mock import patch

from ow174.game.script import runtime
from tests import test_script_join as join_tests
from tests.test_script_runtime_budget import graph, node


class NotificationLifetimeTests(unittest.TestCase):
    def test_animation_timing_bypass_suppresses_both_events(self):
        for bypass, playback_flag, defer_finish, duration, expected_finish, expected_timer in (
            (None, False, False, 0.1, 100, (100, 1)),
            (False, False, False, 2.2, 2200, (2200, 1)),
            (True, False, False, 2.0, None, None),
            (False, False, True, 2.2, None, (2200, 1)),
            (False, True, False, 2.2, 2200, (2200, 1)),
        ):
            with self.subTest(bypass=bypass, playback_flag=playback_flag, defer_finish=defer_finish):
                fields = {
                    'm_86701C3E': {'$': 'STUConfigVarFloat', 'm_value': duration},
                    'm_154DBA91': {'$': 'STUConfigVarBool', 'm_value': playback_flag},
                    'm_2DEAE6A9': {'$': 'STUConfigVarBool', 'm_value': defer_finish},
                }
                if bypass is not None:
                    fields['m_37EE887D'] = {'$': 'STUConfigVarBool', 'm_value': bypass}
                component = runtime.Component(1)
                instance = component.create(
                    graph([node(0, 'STUStatescriptStateAnim', state=0, **fields)]), init=False,
                )
                state = instance.state(0)
                with (
                    patch.object(state, 'request_finish') as finish,
                    patch.object(state, 'timer_at') as timer,
                ):
                    state.on_enter()
                if expected_finish is None:
                    finish.assert_not_called()
                else:
                    finish.assert_called_once_with(expected_finish)
                if expected_timer is None:
                    timer.assert_not_called()
                else:
                    timer.assert_called_once_with(*expected_timer)

    def test_animation_backdate_preserves_native_due_time_and_output_order(self):
        for elapsed, expected_due, timer_runs in (
            (0, 1100, False),
            (47, 1053, False),
            (100, 1000, True),
            (123, 1000, True),
        ):
            with self.subTest(elapsed=elapsed):
                component = runtime.Component(1)
                component.now = 1000
                definition = graph([node(
                    0, 'STUStatescriptStateAnim', state=0,
                    m_86701C3E={'$': 'STUConfigVarFloat', 'm_value': 0.1},
                )])
                instance = component.create(definition, init=False)
                instance.initialized = True
                state = instance.state(0)
                state.begin(backdate=elapsed)

                expected_order = [runtime.STATE_OP, runtime.TIMER]
                if timer_runs:
                    expected_order.reverse()
                self.assertEqual([event.kind for event in instance.timers()], expected_order)
                self.assertEqual([event.time for event in instance.timers()], [expected_due] * 2)

                component.now = expected_due
                with patch.object(instance, 'follow', wraps=instance.follow) as follow:
                    component.run_now()
                outputs = [call.args[1] for call in follow.call_args_list]
                expected_outputs = ['m_onFinishedPlug', 'm_onEndPlug']
                if timer_runs:
                    expected_outputs.insert(0, 'm_A3B86BB0')
                self.assertEqual(outputs, expected_outputs)
                self.assertFalse(state.active)
                self.assertFalse(instance.timers())

    def test_canceled_join_check_clears_its_coalescing_marker(self):
        fixture = join_tests.JoinTests()
        component, instance = fixture.setup_join()
        state = instance.state(0)
        fixture.arrive(instance, 4)
        self.assertTrue(state.flags_check)
        self.assertTrue(any(event.state is state and event.kind == runtime.CHECK
                            for event in instance.queue))
        state.end(False)
        self.assertFalse(state.flags_check)
        self.assertFalse(any(event.state is state for event in instance.queue))
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.active)
        self.assertFalse(state.flags_check)

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
