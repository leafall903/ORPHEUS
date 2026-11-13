"""Compiled graph structure does not freeze live selectors, flags or VM state."""

import unittest

from ow174.game.body_vm_snapshot import BodyVMSnapshotError, snapshot_digest
from ow174.game.script import runtime
from ow174.game.script.graph import Node
from tests.runtime.test_game_graph_analysis import make_graph, node
from tests.script_fixtures import export_synthetic_component_vm


class VMMetadataAccessTests(unittest.TestCase):
    def test_flat_field_identity_nested_arrays_and_invalid_paths_remain_distinct(self):
        value = {'$': 'STUConfigVarInt', 'm_value': 7}
        item = Node(node(0, fields={'m_value': value, '7': 11, 'a.b': 99,
                                  'a\u0301': 12, 'outer': {'items': [None, {'value': 13}]}}))
        self.assertIs(item.field('m_value'), value)
        self.assertEqual(item.field('7'), 11)
        self.assertEqual(item.field('outer.items[1].value'), 13)
        self.assertIsNone(item.field('a.b'))
        self.assertIsNone(item.field('a\u0301'))
        self.assertIsNone(item.field('outer.items[8]'))
        self.assertIsNone(item.field('outer.items[-1]'))
        self.assertIsNone(item.field(''))
        value['m_value'] = 19
        self.assertEqual(item.field('m_value.m_value'), 19)

    def fixture(self):
        message = 0x02400000000006FF
        other = message + 1
        def cfg(value):
            return {'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{value:016X}'}

        definition = make_graph(0xFEE5, [
            node(0, 'STUStatescriptEntryGameMessage', fields={'m_gameMessage': cfg(message)}),
            node(1, state=0, fields={'m_gameMessage': cfg(message)}),
            node(2, 'STUStatescriptEntryGameMessage', client=True, fields={'m_gameMessage': cfg(message)}),
            node(3, 'STUStatescriptEntryGameMessage', fields={'m_gameMessage': None}),
            *[node(pos, 'STUConfigVarInt', fields={'m_value': pos}) for pos in range(4, 40)],
        ], states=(1,))
        component = runtime.Component(0xA0000101)
        instance = component.create(definition)
        component.run_now()
        return component, instance, message, other, cfg

    def test_message_values_flags_and_active_state_are_checked_at_delivery(self):
        component, instance, message, other, cfg = self.fixture()
        def positions(key):
            return [entry.pos for entry in runtime.message_entries(instance, key)]

        self.assertEqual(positions(message), [0])
        instance.state(0).request(runtime.BEGIN)
        component.run_now()
        self.assertEqual(positions(message), [0, 1])
        instance.graph.node(0).client_only = True
        instance.graph.node(2).client_only = False
        instance.graph.node(3).fields['m_gameMessage'] = cfg(other)
        self.assertEqual(positions(message), [1, 2])
        self.assertEqual(positions(other), [3])
        self.assertEqual(runtime.send_instance_message(instance, message, {17: 23}, 0xA0000201), 2)
        queued = [event.node.pos for event in instance.queue if event.kind == runtime.MESSAGE]
        self.assertEqual(queued, [1, 2])

    def test_metadata_reads_preserve_existing_lookup_allocation_and_causal_digest(self):
        component, instance, message, _other, _cfg = self.fixture()
        self.assertEqual(instance.states, {})
        self.assertEqual([entry.pos for entry in runtime.message_entries(instance, message)], [0])
        self.assertEqual(tuple(instance.states), (0,))  # retain the existing inactive-state lookup
        self.assertFalse(instance.states[0].active)
        before = snapshot_digest(export_synthetic_component_vm(component))
        for _ in range(3):
            self.assertEqual(len(instance.graph.message_nodes()), 4)
            self.assertEqual(instance.graph.logical_button_conditions(), ())
            self.assertEqual([entry.pos for entry in runtime.message_entries(instance, message)], [0])
        self.assertEqual(snapshot_digest(export_synthetic_component_vm(component)), before)
        original = instance.graph._message_nodes
        instance.graph._message_nodes = tuple(reversed(original))
        with self.assertRaisesRegex(BodyVMSnapshotError, 'derived cache'):
            export_synthetic_component_vm(component)
        instance.graph._message_nodes = original
        self.assertEqual(snapshot_digest(export_synthetic_component_vm(component)), before)
