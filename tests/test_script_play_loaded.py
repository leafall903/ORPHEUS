"""Loaded-graph invocation policies and native start-entry parameter ordering."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, runtime
from tests.test_script_runtime_budget import graph, node, plug, setter

KEY = 0x0A4000000000000A


def dynamic(identifier, scope=0):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': scope}


def binding(identifier, variable, scope=0):
    return {'m_B5051BCE': f'0x{identifier:016X}', 'm_out_Var': dynamic(variable, scope)}


def definition(*, entries=None, bindings=None, source=None, once=0):
    return graph([
        node(0, 'STU_BB4AC733', m_params=bindings or [binding(KEY, 41)],
             m_B60ABDA4=source, m_FDBDCB70=once, m_5AEA0A51=0,
             m_outPlug=plug((1, 'm_inPlug'))),
        node(1, 'AuditStart'),
    ], entries=[0] if entries is None else entries)


class PlayLoadedTests(unittest.TestCase):
    def test_parameters_are_queued_before_start_but_after_ordinary_initialization(self):
        model = definition()
        init = node(2, 'STUStatescriptEntry', m_outPlug=plug((3, 'm_inPlug')))
        model = graph([*model_record(model), init, node(3, 'AuditInit')], entries=[2, 0])
        seen = []

        def observe(instance, current, caller):
            seen.append((current.cls, instance.find_var(0, 41).value()))
            return True

        component = runtime.Component(123, strict=True)
        with mock.patch.dict(runtime.HANDLERS, AuditStart=observe, AuditInit=observe):
            created = component.play_loaded_graph(model, source=456, params=[(KEY, 17)])
            self.assertIsNone(created.parent)
            self.assertIsNone(created.find_var(0, 41, create=False))
            component.run_now()
        self.assertEqual(seen, [('AuditInit', None), ('AuditStart', 17)])

    def test_policy_zero_skips_existing_and_policy_one_creates_another(self):
        component = runtime.Component(123, strict=True)
        model = definition()
        first = component.play_loaded_graph(model, source=123)
        queued = len(first.queue)
        self.assertIsNone(component.play_loaded_graph(model, source=123, params=[(KEY, 99)]))
        self.assertEqual(len(first.queue), queued)
        second = component.play_loaded_graph(model, source=123, policy=1)
        self.assertEqual([first.id, second.id], [1, 2])

    def test_policy_two_reuses_first_and_keeps_unsupplied_values(self):
        component = runtime.Component(123, strict=True)
        model = definition()
        first = component.play_loaded_graph(model, source=123)
        component.play_loaded_graph(model, source=123, policy=1)
        first.write((0, 41), 88)
        first.queue.clear()
        reused = component.play_loaded_graph(model, source=123, policy=2)
        self.assertIs(reused, first)
        self.assertEqual([event.kind for event in first.queue], [runtime.ENTRY])
        self.assertEqual(first.find_var(0, 41).value(), 88)

    def test_policy_three_retires_every_matching_instance_and_its_children(self):
        component = runtime.Component(123, strict=True)
        model = definition()
        first = component.play_loaded_graph(model, source=123)
        second = component.play_loaded_graph(model, source=123, policy=1)
        child = component.create(graph(), parent=first)
        replacement = component.play_loaded_graph(model, source=123, policy=3)
        self.assertEqual(list(component.instances), [replacement.id])
        self.assertTrue(all(instance.stopped for instance in (first, second, child)))
        self.assertEqual(replacement.id, 4)

    def test_named_positional_duplicate_and_missing_parameter_bindings(self):
        model = definition(bindings=[binding(KEY, 41), binding(KEY, 99), binding(KEY + 1, 42, 1)])
        component = runtime.Component(123, strict=True)
        instance = component.play_loaded_graph(model, source=123, params=[
            (KEY, 5), (KEY, None), (0, 7), (KEY + 99, 8),
        ])
        with mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True):
            component.run_now()
        self.assertIsNone(instance.find_var(0, 41).value())
        self.assertIsNone(instance.find_var(0, 99, create=False))
        self.assertEqual(component.entity_var(42).value(), 7)

    def test_source_binding_runs_after_parameters_and_only_for_valid_source(self):
        model = definition(bindings=[binding(KEY, 41)], source=dynamic(41))
        for source, expected in ((456, expr.Entity(456)), (0, 5), (0x1FFFFFFF, 5)):
            with self.subTest(source=source):
                component = runtime.Component(123, strict=True)
                instance = component.play_loaded_graph(model, source=source, params=[(KEY, 5)])
                with mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True):
                    component.run_now()
                self.assertEqual(instance.find_var(0, 41).value(), expected)

    def test_one_shot_start_entry_is_disabled_before_a_second_queued_invocation(self):
        component = runtime.Component(123, strict=True)
        model = definition(once=1)
        first = component.play_loaded_graph(model, source=123, params=[(KEY, 5)])
        component.play_loaded_graph(model, source=123, params=[(KEY, 8)], policy=2)
        self.assertEqual(first.disabled_start_entries, {0})
        self.assertEqual(sum(event.kind == runtime.ENTRY for event in first.queue), 1)

    def test_init_does_not_fire_start_entries_and_other_context_kinds_do_not_match(self):
        component = runtime.Component(123, strict=True)
        model = definition()
        instance = component.create(model)
        self.assertFalse(instance.queue)
        model.node(0).fields['m_5AEA0A51'] = 1
        self.assertEqual(instance.start_entries(((KEY, 5),), 123), 0)
        self.assertFalse(instance.queue)

    def test_client_only_start_entry_and_unknown_targets_do_not_manufacture_work(self):
        model = definition()
        model.node(0).client_only = True
        component = runtime.Component(123, strict=True)
        instance = component.play_loaded_graph(model, source=123, params=[(KEY, 5)])
        self.assertFalse(instance.queue)
        model.node(0).client_only = False
        model.node(0).fields['m_params'][0]['m_out_Var'] = {'$': 'UnknownOutput'}
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.start_entries(((KEY, 5),), 123)
        self.assertIsNotNone(component.fault)
        self.assertFalse(instance.queue)

    def test_storage_export_retains_pending_assignments_and_disabled_entries_without_execution(self):
        component = runtime.Component(123, strict=True)
        instance = component.play_loaded_graph(definition(once=1), source=123, params=[(KEY, 5)])
        queued = list(instance.queue)
        with (
            mock.patch.object(runtime.Instance, 'start_entries', side_effect=AssertionError('executed')),
            mock.patch.object(runtime.Instance, 'dispatch', side_effect=AssertionError('executed')),
        ):
            before = snapshot_digest(export_component_vm(component))
        self.assertEqual(instance.queue, queued)
        instance.queue[0].assignment = (0, 41, 6)
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)

    def test_strict_play_action_requires_world_backend_and_legacy_action_keeps_compatibility(self):
        for strict in (True, False):
            component = runtime.Component(123, strict=strict)
            instance = component.create(graph([
                node(0, 'STUStatescriptActionPlayScript',
                     m_script={'$': 'STU_105E1BCC', 'm_resourceKey': None,
                               'm_graph': '0x0580000000000002'},
                     m_targets={'$': 'STU_B5A0CAF0'},
                     m_outPlug=plug((1, 'm_inPlug'))),
                setter(1, 99),
            ]), init=False)
            if strict:
                with self.assertRaises(runtime.UnsupportedScriptError) as raised:
                    instance.run_node(instance.graph.node(0), None)
                self.assertEqual(raised.exception.diagnostic['feature'], 'native PlayScript request context')
                self.assertNotIn(7, instance.vars)
            else:
                instance.run_node(instance.graph.node(0), None)
                self.assertEqual(instance.find_var(0, 7).value(), 99)

    def test_resolved_world_request_creates_child_before_following_parent_output(self):
        world = runtime.World()
        component = runtime.Component(123, world=world, strict=True)
        model = definition()
        model.index = 2
        parent = component.create(graph([
            node(0, 'STUStatescriptActionPlayScript', m_outPlug=plug((1, 'm_inPlug'))),
            setter(1, 99),
        ]), init=False)
        parent.initialized = True

        def resolved(requesting, request):
            self.assertIs(requesting, parent)
            self.assertEqual(request.pos, 0)
            self.assertNotIn(7, parent.vars)
            component.play_loaded_graph(model, source=456, params=[(KEY, 77)], policy=1)

        with (
            mock.patch.object(world, 'play_script', side_effect=resolved) as backend,
            mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True),
        ):
            parent.run_node(parent.graph.node(0), None)
            component.run_now()
        backend.assert_called_once()
        self.assertEqual(component.instances[2].find_var(0, 41).value(), 77)
        self.assertEqual(parent.find_var(0, 7).value(), 99)

    def test_registered_stopped_instance_is_not_replaced_by_skip_or_reuse(self):
        component = runtime.Component(123, strict=True)
        model = definition()
        instance = component.create(model)
        instance.stopped = True
        self.assertIsNone(component.play_loaded_graph(model, source=123, policy=0))
        self.assertIs(component.play_loaded_graph(model, source=123, policy=2), instance)
        self.assertFalse(instance.queue)


def model_record(model):
    return [dict(pos=item.pos, **{'class': item.cls}, uid=item.uid, rid=item.rid, remote=item.remote,
                 state=item.state, client_only=item.client_only, server_only=item.server_only,
                 fields=item.fields) for item in model.nodes]


if __name__ == '__main__':
    unittest.main()
