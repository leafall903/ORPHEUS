"""Original D06/160 and166 queue boundaries; no target/filter/helper success adapter."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import graph, nodes, runtime


class DriverDeferTests(unittest.TestCase):
    def scene(self, position=166, *, start=True):
        component = runtime.Component(0xA0000201, strict=True)
        source = component.create(graph.graph(0xD06), init=False)
        source.init(run_entries=False)
        other = component.create(graph.graph(0xD06), init=False)
        other.init(run_entries=False)
        state = source.node_state(source.graph.node(position))
        self.assertIsInstance(state, nodes.Defer)
        fixture = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                              / 'doomfist-driver-defer-v1.json').read_text())
        self.assertEqual(state.node.field('m_4BA4C058'), fixture['nodes'][str(position)]['mode'])
        if start:
            state.begin()
        return component, source, other, state

    def phase(self, position):
        component, source, other, state = self.scene(position)
        order = []
        update = other.update
        def ordinary():
            order.append('ordinary-other')
            return update()
        # Timer callback boundary only. The original three166 links are proven
        # separately by native434; this never supplies targets or health/force.
        def timer(active, param):
            self.assertIs(active, state)
            self.assertEqual(param, 1)
            order.append('timer')
        with patch.object(other, 'update', side_effect=ordinary), patch.object(nodes.Defer, 'timer', timer):
            component.run_now()
        self.assertEqual(order.count('timer'), 1)
        self.assertFalse(source.deferred)
        return order

    def test_original166_waits_for_the_driver_transfer_after_other_instances(self):
        order = self.phase(166)
        self.assertLess(order.index('ordinary-other'), order.index('timer'))

    def test_original160_keeps_its_namespace_defer_phase(self):
        order = self.phase(160)
        self.assertLess(order.index('timer'), order.index('ordinary-other'))

    def test_pending166_is_cancelled_on_stop_and_body_fault(self):
        for operation in ('end', 'stop', 'fault'):
            with self.subTest(operation=operation):
                component, source, _other, state = self.scene()
                self.assertEqual(len(component.driver_deferred), 1)
                self.assertFalse(source.deferred)
                if operation == 'end':
                    state.end(False)
                elif operation == 'stop':
                    source.stop()
                else:
                    with self.assertRaises(runtime.ScriptBudgetError):
                        component.budget_exceeded('test terminal fault')
                self.assertFalse(component.driver_deferred)
                if operation != 'fault':
                    with patch.object(nodes.Defer, 'timer', side_effect=AssertionError('cancelled timer')):
                        component.run_now()

    def test_pending166_snapshot_preserves_queue_and_state_identity(self):
        component, _source, _other, state = self.scene()
        queue = component.driver_deferred
        before = snapshot_digest(export_component_vm(component))
        self.assertIs(queue[0][2], state)
        saved = queue[:]
        queue.clear()
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        queue[:] = saved
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        self.assertIs(component.driver_deferred, queue)

    def test_driver_rows_started_after_ordinary_phase_wait_for_another_update(self):
        component, _source, _other, state = self.scene(start=False)
        calls = []
        def timer(active, param):
            calls.append((component.frame, component.now))
        with patch.object(nodes.Defer, 'timer', timer):
            component.run_frame(1, end_phase=state.begin)
            self.assertEqual(calls, [])
            self.assertEqual(len(component.driver_deferred), 1)
            cursor = component.frame, component.now
            component.run_now()
        self.assertEqual(calls, [cursor])
        self.assertEqual((component.frame, component.now), cursor)

    def test_equal_priority_driver_rows_use_native_namespace_keys(self):
        component, _source, other, _state = self.scene()
        other.node_state(other.graph.node(166)).begin()
        fixture = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                              / 'doomfist-driver-defer-v1.json').read_text())
        self.assertEqual([row[2].instance.id for row in component.driver_deferred],
                         fixture['native_equal_priority_namespace_order'])

    def test_full_server_causal_snapshot_captures_pending_driver_queue(self):
        import test_server_snapshot as snapshots

        from ow174.game.server_snapshot import snapshot_digest as server_digest

        owner = snapshots.ServerSnapshotTests()
        session, _peers, players = owner.scene()
        component = players[0].body_script.component
        instance = component.create(graph.graph(0xD06), init=False)
        instance.init(run_entries=False)
        state = instance.node_state(instance.graph.node(166))
        state.begin()
        queue = component.driver_deferred
        self.assertIs(queue[0][2], state)
        saved = queue[:]
        # Stored full-server capture must neither execute the graph nor send.
        with patch.object(runtime.Var, 'value', side_effect=AssertionError('snapshot getter')), \
             patch.object(session.server.sock, 'sendto', side_effect=AssertionError('snapshot send')):
            document = owner.capture(session)
            before = server_digest(document)
            self.assertFalse(document['restorable'])
            queue.clear()
            self.assertNotEqual(server_digest(owner.capture(session)), before)
            queue[:] = saved
            self.assertEqual(server_digest(owner.capture(session)), before)
        self.assertIs(queue[0][2], state)
        self.assertIs(component.driver_deferred, queue)
