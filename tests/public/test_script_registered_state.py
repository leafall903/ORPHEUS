"""Native type-list order, cleanup and successor links across graph instances."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import nodes, runtime
from tests.script_fixtures import export_synthetic_component_vm, graph, node, plug

CLASS = 'STU_9028E035'


def model(*, count=1, server_only=(), remote=True, state_class=CLASS):
    records = [node(index, state_class, state=index, m_type=index % 2) for index in range(count)]
    for index, item in enumerate(records):
        item['server_only'] = index in server_only
        # Remote-sync indexes deliberately differ from m_states indexes.
        item['remote'] = count - index - 1 if remote else -1
    result = graph(records)
    result.header.update(m_statesBitCount=max(0, (count - 1).bit_length()),
                         m_remoteSyncNodesBitCount=max(0, (count - 1).bit_length()))
    result.remote_nodes = list(reversed(result.states)) if remote else []
    result._remote = [item for item in result.remote_nodes if item.is_networked]
    return result


def instance(component, definition=None):
    result = component.create(definition or model(), init=False)
    result.initialized = True
    return result


def begin(component, active, index=0):
    active.state(index).request(runtime.BEGIN)
    component.run_now()
    return active.state(index)


class RegisteredStateTests(unittest.TestCase):
    def test_mission_registered_state_links_only_its_own_class(self):
        component = runtime.Component(123, strict=True)
        other = begin(component, instance(component))
        for mission_class in ('STU_25CBEFB0', 'STU_43994F1B', 'STU_93B8758C'):
            with self.subTest(mission_class=mission_class):
                definition = model(state_class=mission_class)
                first = begin(component, instance(component, definition))
                last = begin(component, instance(component, definition))
                self.assertEqual(component.state_registries[mission_class], [first, last])
                self.assertEqual(first.payload(), {'link': (last.instance.id, 0)})
                self.assertEqual(other.payload(), {})
                last.end(False)
                self.assertEqual(first.payload(), {})

    def test_registration_precedes_authored_begin_plug_and_m_type_does_not_split_lists(self):
        component = runtime.Component(123, strict=True)
        definition = model(count=2)
        seen = []
        definition.nodes[0].fields['m_onBeginPlug'] = plug((1, 'm_inPlug'))
        active = instance(component, definition)
        first = begin(component, active)
        second = active.state(1)
        self.assertEqual(component.state_registries[CLASS], [first, second])
        self.assertEqual(first.payload(), {'link': (active.id, 1)})
        self.assertEqual(second.payload(), {})
        # An action reached by a begin plug must see the just-registered state.
        action = node(2, 'ObserveRegistry')
        definition._by_pos[2] = type(definition.nodes[0])(action)
        definition.nodes[1].fields['m_onBeginPlug'] = plug((2, 'm_inPlug'))
        second.end(False)
        with mock.patch.dict(runtime.HANDLERS, ObserveRegistry=lambda *_: seen.append(
                tuple(component.state_registries[CLASS])) or True):
            begin(component, active, 1)
        self.assertEqual(seen, [(first, second)])

    def test_cross_instance_order_and_reentry_do_not_register_twice(self):
        component = runtime.Component(123, strict=True)
        active = [instance(component) for _ in range(3)]
        states = [begin(component, item) for item in active]
        begin(component, active[0])
        self.assertEqual(component.state_registries[CLASS], states)
        self.assertEqual(states[0].payload(), {'link': (active[1].id, 0)})
        self.assertEqual(states[1].payload(), {'link': (active[2].id, 0)})
        states[1].end(True)
        self.assertEqual(component.state_registries[CLASS], [states[0], states[2]])
        self.assertEqual(states[0].payload(), {'link': (active[2].id, 0)})
        begin(component, active[1])
        self.assertEqual(component.state_registries[CLASS], [states[0], states[2], states[1]])

    def test_wire_link_skips_server_only_successors_but_the_registry_keeps_them(self):
        component = runtime.Component(123, strict=True)
        active = instance(component, model(count=4, server_only=(1, 3)))
        states = [begin(component, active, index) for index in range(4)]
        self.assertEqual(component.state_registries[CLASS], states)
        self.assertEqual(states[0].payload(), {'link': (active.id, 2)})
        self.assertEqual(states[2].payload(), {})
        states[2].end(False)
        self.assertEqual(states[0].payload(), {})

    def test_cleanup_follows_class_end_hook_and_precedes_authored_end_plug(self):
        for finished in (False, True):
            with self.subTest(finished=finished):
                seen = []

                class Probe(nodes.RegisteredState):
                    def on_end(self, finished, seen=seen):
                        seen.append(('class', tuple(self.instance.component.state_registries[CLASS])))

                component = runtime.Component(123, strict=True)
                definition = graph([node(0, CLASS, state=0, m_onEndPlug=plug((1, 'm_inPlug'))),
                                    node(1, 'ObserveEnd')])
                with mock.patch.dict(runtime.STATE_CLASSES, {CLASS: Probe}), mock.patch.dict(
                    runtime.HANDLERS, ObserveEnd=lambda *_, seen=seen, component=component: seen.append(
                        ('plug', tuple(component.state_registries[CLASS]))) or True,
                ):
                    state = begin(component, instance(component, definition))
                    state.end(finished)
                self.assertEqual(seen, [('class', (state,)), ('plug', ())])

    def test_teardown_id_reuse_and_fault_clear_live_registry_membership(self):
        component = runtime.Component(123, strict=True)
        first = instance(component)
        old = begin(component, first)
        component.destroy(first)
        self.assertEqual(component.state_registries[CLASS], [])
        fresh = component.create(model(), instance_id=first.id, init=False)
        fresh.initialized = True
        current = begin(component, fresh)
        self.assertIsNot(old, current)
        self.assertEqual(component.state_registries[CLASS], [current])
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(fresh, 'test fault', 'UnknownNative')
        self.assertFalse(component.state_registries)
        self.assertFalse(current.active)

    def test_storage_observes_registry_order_without_resolving_payload(self):
        component = runtime.Component(123, strict=True)
        active = instance(component, model(count=2))
        first, second = [begin(component, active, index) for index in range(2)]
        with mock.patch.object(nodes.RegisteredState, 'payload', side_effect=AssertionError('payload')):
            before = snapshot_digest(export_synthetic_component_vm(component))
            self.assertEqual(snapshot_digest(export_synthetic_component_vm(component)), before)
            component.state_registries[CLASS][:] = [second, first]
            self.assertNotEqual(snapshot_digest(export_synthetic_component_vm(component)), before)



if __name__ == '__main__':
    unittest.main()
