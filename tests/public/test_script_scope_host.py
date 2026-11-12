"""A selected normal-forward host defers native scope edges to an explicit phase."""

import unittest
from unittest import mock

from ow174.game.script import nodes, runtime, scene, scopes
from tests.public.test_script_scopes import CLASS, OWNER, definition
from tests.runtime.test_script_runtime_budget import graph, node, plug


def setup(model=None, *, frame=20):
    world = scene.SceneWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    host = scopes.ForwardHost(component)
    if frame is not None:
        host.set_frame(frame)
    world.set_subgraph_scopes(OWNER, host)
    instance = component.create(model or definition(), init=False)
    instance.initialized = True
    return component, instance, host


class ScopeHostTests(unittest.TestCase):
    def test_cold_registration_and_vm_updates_do_not_drain_the_host(self):
        component, instance, host = setup()
        state = instance.state(0)
        self.assertFalse(host.history[state].records)
        state.request(runtime.BEGIN)
        component.run_frame(0)
        self.assertTrue(state.active)
        self.assertNotIn(7, instance.vars)
        self.assertEqual(host.history[state].records, [scopes.Edge(20, True)])
        host.drain()
        self.assertTrue(host.history[state].dispatched)
        self.assertNotIn(7, instance.vars)  # Child begin remains queued.
        component.run_now()
        self.assertTrue(instance.state(1).active)
        self.assertEqual(instance.find_var(0, 7).value(), 7)

    def test_unknown_frame_stops_before_the_first_edge_and_clears_host(self):
        component, instance, host = setup(frame=None)
        state = instance.state(0)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            state.begin()
        self.assertEqual(error.exception.diagnostic['feature'], 'subgraph scope frame')
        self.assertIs(component.fault, error.exception)
        self.assertTrue(instance.stopped)
        self.assertFalse(host.history)
        self.assertFalse(host.states)
        self.assertFalse(host.dirty)

    def test_frame_validation_does_not_derive_or_rewind_the_native_clock(self):
        _, instance, host = setup()
        for value in (True, 2.5, None, 0x80000000, -0x80000001, 19):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.set_frame(value)
        state = instance.state(0)
        state.begin()
        host.set_frame(21)
        state.end(False)
        self.assertEqual(host.history[state].records, [scopes.Edge(20, True), scopes.Edge(21, False)])
        self.assertFalse(host.history[state].initial)

    def test_begin_reentry_and_inactive_end_do_not_create_extra_edges(self):
        _, instance, host = setup()
        state = instance.state(0)
        state.end(False)
        state.begin()
        state.begin()
        self.assertEqual(state.counter, 1)
        self.assertEqual(len(host.history[state].records), 1)
        state.end(True)
        state.end(False)
        self.assertEqual(len(host.history[state].records), 2)

    def test_begin_end_before_drain_cancels_both_interface_callbacks(self):
        component, instance, host = setup()
        state = instance.state(0)
        state.begin()
        state.end(False)
        host.drain()
        component.run_now()
        self.assertFalse(host.history[state].records)
        self.assertFalse(host.history[state].dispatched)
        self.assertFalse(instance.state(1).active)
        self.assertNotIn(7, instance.vars)

    def test_three_edges_use_first_snapshot_and_dispatch_one_enter(self):
        component, instance, host = setup()
        state = instance.state(0)
        state.begin()
        state.end(True)
        state.begin()
        host.drain()
        component.run_now()
        self.assertEqual(state.counter, 2)
        self.assertTrue(instance.state(1).active)
        self.assertTrue(host.history[state].dispatched)

    def test_finish_and_abort_each_queue_exit_until_explicit_drain(self):
        for finished in (False, True):
            with self.subTest(finished=finished):
                component, instance, host = setup()
                state = instance.state(0)
                state.begin()
                host.drain()
                component.run_now()
                state.end(finished)
                self.assertTrue(instance.state(1).active)
                self.assertTrue(host.history[state].initial)
                host.drain()
                self.assertFalse(instance.state(1).active)
                self.assertFalse(host.history[state].dispatched)

    def test_payload_is_retained_but_normal_dispatch_uses_snapshot_and_parity(self):
        for active in (False, True):
            for payload in (False, True):
                with self.subTest(active=active, payload=payload):
                    _, instance, host = setup()
                    state = instance.state(0)
                    state.active = active
                    host.record(state, payload)
                    self.assertEqual(host.history[state].records[0].active, payload)
                    with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter') as enter, \
                            mock.patch.object(nodes.ScopedSubgraph, 'scope_exit') as exit:
                        host.drain()
                    self.assertEqual(enter.call_count, int(not active))
                    self.assertEqual(exit.call_count, int(active))

    def test_hook_order_records_begin_before_exclusives_and_end_before_children(self):
        model = graph([node(0, CLASS, state=0, m_exclusiveStateIndices=[1]),
                       node(1, CLASS, state=1)])
        _, instance, host = setup(model)
        first, second = instance.state(0), instance.state(1)
        second.begin()
        observations = []
        record = host.record

        def observe(state, active):
            observations.append((state.index, state.counter, state.active, state.ending, second.active))
            record(state, active)

        with mock.patch.object(host, 'record', observe):
            first.begin()
        self.assertEqual(observations, [(0, 1, False, False, True), (1, 1, True, True, True)])

    def test_live_unregister_processes_pending_then_swap_removes_registration(self):
        model = graph([node(i, CLASS, state=i) for i in range(3)])
        _, instance, host = setup(model)
        states = [instance.state(i) for i in range(3)]
        states[0].begin()
        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter') as enter:
            host.unregister(states[0])
        self.assertEqual(enter.call_count, 1)
        self.assertEqual(host.states, [states[2], states[1]])
        self.assertNotIn(states[0], host.history)
        for state in states[1:]:
            state.begin()
        observed = []
        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter',
                               lambda state: observed.append(state.index)):
            host.drain()
        self.assertEqual(observed, [2, 1])

    def test_callback_can_append_a_new_registration_in_the_current_scan(self):
        model = graph([node(i, CLASS, state=i) for i in range(2)])
        _, instance, host = setup(model)
        first = instance.state(0)
        first.begin()
        observed = []

        def enter(state):
            observed.append(state.index)
            if state is first:
                instance.state(1).begin()

        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter', enter):
            host.drain()
        self.assertEqual(observed, [0, 1])
        self.assertFalse(host.dirty)
        self.assertTrue(all(not h.records and h.dispatched for h in host.history.values()))

    def test_edges_appended_during_current_callback_are_consumed_by_that_cleanup(self):
        _, instance, host = setup()
        state = instance.state(0)
        state.begin()
        observed = []

        def enter(current):
            observed.append(current)
            current.end(False)

        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter', enter):
            host.drain()
        self.assertEqual(observed, [state])
        self.assertFalse(state.active)
        self.assertFalse(host.history[state].records)
        self.assertTrue(host.history[state].dispatched)
        self.assertFalse(host.dirty)

    def test_callback_can_dirty_an_already_scanned_state_for_the_next_pass(self):
        model = graph([node(i, CLASS, state=i) for i in range(2)])
        _, instance, host = setup(model)
        first, second = instance.state(0), instance.state(1)
        second.begin()
        observed = []

        def enter(state):
            observed.append(state.index)
            if state is second:
                first.begin()

        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter', enter):
            host.drain()
        self.assertEqual(observed, [1, 0])

    def test_callback_retirement_discards_history_without_following_more_plugs(self):
        component, instance, host = setup()
        state = instance.state(0)
        state.begin()
        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter', lambda _: component.destroy(instance)):
            host.drain()
        self.assertTrue(instance.stopped)
        self.assertFalse(host.states)
        self.assertFalse(host.history)
        self.assertIsNone(state.scope_registry)

    def test_pending_teardown_never_creates_a_late_effect_or_enters_custom_plugs(self):
        component, instance, host = setup()
        state = instance.state(0)
        state.begin()
        component.destroy(instance)
        host.drain()
        self.assertNotIn(7, instance.vars)
        self.assertFalse(host.states)
        self.assertFalse(host.history)

    def test_entity_retirement_discards_pending_edges_and_rejects_old_callbacks(self):
        component, instance, host = setup()
        state = instance.state(0)
        state.begin()
        component.world.unbind(OWNER)
        host.drain()
        self.assertFalse(host.states)
        self.assertFalse(host.history)
        with self.assertRaises(ValueError):
            host.enter(state)
        component.destroy(instance)

    def test_strict_callback_failure_clears_pending_and_stops_later_callbacks(self):
        model = graph([node(0, CLASS, state=0, m_5B10D01F=plug((2, 'm_inPlug'))),
                       node(1, CLASS, state=1), node(2, 'MissingScopedAction')])
        component, instance, host = setup(model)
        for index in range(2):
            instance.state(index).begin()
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            host.drain()
        self.assertEqual(error.exception.diagnostic['class'], 'MissingScopedAction')
        self.assertFalse(host.states)
        self.assertFalse(host.history)
        self.assertFalse(host.dirty)
        self.assertIs(component.fault, error.exception)

    def test_undecoded_self_unregister_during_callback_is_a_strict_gate(self):
        component, instance, host = setup()
        state = instance.state(0)
        state.begin()
        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter',
                               lambda current: host.unregister(current)), \
                self.assertRaises(runtime.UnsupportedScriptError) as error:
            host.drain()
        self.assertEqual(error.exception.diagnostic['feature'], 'subgraph scope retirement during callback')
        self.assertIs(component.fault, error.exception)
        self.assertFalse(host.history)

    def test_callback_cycles_share_the_component_work_budget(self):
        model = graph([node(i, CLASS, state=i) for i in range(2)])
        component, instance, host = setup(model)
        first, second = instance.state(0), instance.state(1)
        first.begin()

        def enter(state):
            other = second if state is first else first
            host.record(other, True)

        with mock.patch.object(nodes.ScopedSubgraph, 'scope_enter', enter), \
                mock.patch.object(nodes.ScopedSubgraph, 'scope_exit', enter), \
                self.assertRaises(runtime.ScriptBudgetError):
            host.drain()
        self.assertIsNotNone(component.fault)
        self.assertFalse(host.history)

    def test_owner_identity_cannot_be_inferred_from_a_reused_entity_id(self):
        component, instance, host = setup()
        with self.assertRaises(ValueError):
            scopes.ForwardHost(object())
        other_world = scene.SceneWorld()
        other_world.bind(OWNER)
        with self.assertRaises(ValueError):
            other_world.set_subgraph_scopes(OWNER, host)
        other = runtime.Component(OWNER, world=component.world, strict=True)
        foreign = other.create(definition(), init=False)
        with self.assertRaises(ValueError):
            foreign.state(0)
        self.assertFalse(host.states)
        self.assertIs(instance.component, component)

    def test_retired_instance_ids_do_not_inherit_pending_history(self):
        component, instance, host = setup()
        old = instance.state(0)
        old.begin()
        component.destroy(instance)
        fresh = component.create(definition(), instance_id=instance.id, init=False)
        fresh.initialized = True
        state = fresh.state(0)
        self.assertNotIn(old, host.history)
        self.assertFalse(host.history[state].records)
        self.assertFalse(host.history[state].dispatched)
        with self.assertRaises(ValueError):
            host.record(old, True)


if __name__ == '__main__':
    unittest.main()
