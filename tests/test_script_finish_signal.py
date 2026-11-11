"""Verified server callback; client scheduling/admission is a separate service."""

import unittest

from test_game_graph_analysis import make_graph, node, output
from test_symmetra_combat import arena, fire

from ow174.game.script import expr, graph, nodes, runtime


class FinishSignalTests(unittest.TestCase):
    def fixture(self, *, strict=False):
        definition = make_graph(0xFA00, (
            node(0, 'STU_54FEB21C', state=0, fields={
                'm_ED97A97C': {'$': 'STUConfigVarFloat', 'm_value': .2},
                'm_onFinishedPlug': output((1, 'm_inPlug'))}),
            node(1, 'STUStatescriptActionSetVar', fields={
                'm_out_Var': {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0},
                'm_value': {'$': 'STUConfigVarInt', 'm_value': 123}}),
        ), states=(0,))
        component = runtime.Component(0xA0000101, strict=strict)
        instance = component.create(definition)
        state = instance.state(0)
        state.begin()
        component.run_now()
        return component, instance, state

    def test_entry_does_not_turn_client_duration_into_a_server_timer(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                component, instance, state = self.fixture(strict=strict)
                self.assertIsInstance(state, nodes.FinishOnSignal)
                self.assertEqual(instance.queue, [])
                component.run_frame(10000)
                self.assertTrue(state.active)
                self.assertIsNone(instance.find_var(0, 7).value())

    def test_only_parameter_one_requests_finish_and_follows_original_plug(self):
        for param in (0, 1, 2, 3, 127, 255, 0x7FFFFFFF, 0xFFFFFFFF):
            with self.subTest(param=param):
                component, instance, state = self.fixture()
                state.timer(param)
                component.run_now()
                self.assertEqual(state.active, param != 1)
                self.assertEqual(instance.find_var(0, 7).value(), 123 if param == 1 else None)

    def test_duplicate_and_retired_events_do_not_repeat_finished_flow(self):
        component, instance, state = self.fixture()
        for _ in range(3):
            state.timer_at(0, 1)
        component.run_now()
        self.assertFalse(state.active)
        self.assertEqual(instance.find_var(0, 7).value(), 123)
        self.assertEqual(state.counter, 1)
        component.destroy(instance)
        component.run_now()
        self.assertNotIn(instance.id, component.instances)

    def test_actual_use_input_waits_for_signal_then_reaches_unimplemented_next_service(self):
        run, owner, other = arena()
        fire(run, owner, buttons=0)
        component = owner.body_script.component
        use = component.play_loaded_graph(
            graph.graph(0x15F5), source=other.body,
            params=((0x0A40000000000008, expr.Entity(other.body)),), policy=1)
        component.run_now()
        fire(run, owner, 12, buttons=0)
        fire(run, owner, buttons=4)
        wait = use.state(1)
        self.assertIsInstance(wait, nodes.FinishOnSignal)
        self.assertTrue(wait.active)
        fire(run, owner, 30, buttons=4)
        self.assertTrue(wait.active)
        # Explicitly admitted server callback; no claim of client transport.
        wait.timer_at(0, 1)
        component.run_now()
        self.assertFalse(wait.active)
        self.assertIn(8, use.states)  # original node28/550D0C5C was entered
        self.assertFalse(any(i.graph.index == 0x1647 for i in component.instances.values()))
        self.assertEqual(owner.mover.position, [0, 1, 0])


if __name__ == '__main__':
    unittest.main()
