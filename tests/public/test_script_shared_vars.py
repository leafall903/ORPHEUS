"""Declarative sharing retains common State behavior and never evaluates variables."""

import copy
import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import graph as graphs
from ow174.game.script import nodes, runtime
from tests.public.test_script_play_loaded import dynamic
from tests.script_fixtures import export_synthetic_component_vm, graph, node, plug


def metadata(keys):
    rows = [{'$': 'STUStatescriptRemoteSyncVar', 'm_56341592': scope,
             'm_0D09D2D9': f'0x{0x0D80000000000000 | key:016X}'} for scope, key in keys]
    return {'m_6F0C0343': [dynamic(key, scope) for scope, key in keys],
            'm_BF5B22B7': rows, 'm_8BF03679': copy.deepcopy(rows)}


def setup(keys=((0, 940), (1, 41)), *, strict=True, **fields):
    model = graph([node(0, 'STU_9D7BF987', state=0, **(metadata(keys) | fields))])
    model.sync_vars = [graphs.SyncVar(index, key, scope, 0)
                       for index, (scope, key) in enumerate(keys)]
    model._presence = [entry for entry in model.sync_vars if entry.has_presence_bit]
    component = runtime.Component(123, strict=strict)
    instance = component.create(model, init=False)
    instance.initialized = True
    return component, instance


class SharedVariablesTests(unittest.TestCase):
    def test_allocation_does_not_create_read_assign_or_watch_declared_variables(self):
        component, instance = setup()
        with (mock.patch.object(instance, 'evaluate', side_effect=AssertionError('evaluated')),
              mock.patch.object(instance, 'find_var', side_effect=AssertionError('resolved')),
              mock.patch.object(instance, 'write', side_effect=AssertionError('assigned'))):
            state = instance.state(0)
            state.begin()
            state.end(False)
        self.assertIs(type(state), nodes.SharedVariables)
        self.assertEqual((instance.vars, component.vars, state.watched), ({}, {}, {}))
        self.assertFalse(instance.ticking)
        self.assertEqual(state.payload(), {})

    def test_empty_and_absent_declarations_are_valid(self):
        for fields in (metadata(()), dict.fromkeys(metadata(()))):
            with self.subTest(fields=fields):
                _, instance = setup((), **fields)
                self.assertIs(type(instance.state(0)), nodes.SharedVariables)

    def test_metadata_order_and_repeated_declarations_do_not_assign_variables(self):
        fields = metadata(((0, 940), (1, 41), (0, 940)))
        fields['m_BF5B22B7'].reverse()
        _, instance = setup(**fields)
        self.assertIs(type(instance.state(0)), nodes.SharedVariables)
        self.assertEqual(instance.vars, {})

    def test_malformed_declarations_fail_before_activation(self):
        for value in (None, {}, {'$': 'STUConfigVarInt'}, dynamic(0), dynamic(65536),
                      dynamic(True), dynamic(940, 2), dynamic(940, False)):
            with self.subTest(value=value):
                component, instance = setup(m_6F0C0343=[value])
                with self.assertRaises(runtime.UnsupportedScriptError) as error:
                    instance.state(0)
                self.assertEqual(error.exception.diagnostic['feature'], 'shared variable declaration')
                self.assertIs(component.fault, error.exception)

    def test_scalar_lists_and_malformed_compiled_entries_fail_closed(self):
        for field in metadata(()):
            with self.subTest(field=field):
                _, instance = setup(**{field: {}})
                with self.assertRaises(runtime.UnsupportedScriptError) as error:
                    instance.state(0)
                self.assertEqual(error.exception.diagnostic['feature'], 'shared variable list')
        for row in (None, {}, {'$': 'STUConfigVarDynamic'},
                    {'$': 'STUStatescriptRemoteSyncVar', 'm_56341592': 0,
                     'm_0D09D2D9': 'not-a-guid'}):
            with self.subTest(row=row):
                _, instance = setup(m_BF5B22B7=[row])
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.state(0)

    def test_compiled_scope_type_namespace_and_identifier_are_checked(self):
        for scope, key in ((True, 0x0D800000000003AC), (2, 0x0D800000000003AC),
                           (0, 0x09800000000003AC), (0, 0x0D80000000000000),
                           (0, 0x0D80000000010000)):
            with self.subTest(scope=scope, key=key):
                row = {'$': 'STUStatescriptRemoteSyncVar', 'm_56341592': scope,
                       'm_0D09D2D9': f'0x{key:016X}'}
                _, instance = setup(m_BF5B22B7=[row])
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.state(0)

    def test_each_compiled_group_must_match_scope_and_identifier(self):
        for field in ('m_BF5B22B7', 'm_8BF03679'):
            for rows in ([], metadata(((0, 940), (0, 41)))[field]):
                with self.subTest(field=field, rows=rows):
                    _, instance = setup(**{field: rows})
                    with self.assertRaises(runtime.UnsupportedScriptError) as error:
                        instance.state(0)
                    self.assertEqual(error.exception.diagnostic['feature'],
                                     'shared variable compiled metadata')

    def test_graph_metadata_must_cover_every_declaration(self):
        _, instance = setup()
        instance.graph.sync_vars.pop()
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            instance.state(0)
        self.assertEqual(error.exception.diagnostic['feature'], 'shared variable graph metadata')

    def test_common_begin_reentry_counter_wrap_and_both_end_paths(self):
        for finished in (False, True):
            with self.subTest(finished=finished):
                component, instance = setup()
                state = instance.state(0)
                state.counter = 65535
                component.now = 1000
                state.begin(backdate=17)
                self.assertEqual((state.active, state.counter, state.start), (True, 0, 983))
                component.now = 1100
                state.begin()
                self.assertEqual((state.counter, state.start), (0, 983))
                state.end(finished)
                self.assertEqual((state.active, state.ending, state.ended_at), (False, False, 1100))
                component.now = 1200
                state.end(finished)
                self.assertEqual(state.ended_at, 1100)
                state.begin()
                self.assertEqual((state.counter, state.start), (1, 1200))

    def test_common_authored_plugs_run_without_class_specific_work(self):
        _, instance = setup(m_onBeginPlug=plug((1, 'm_inPlug')),
                            m_onFinishedPlug=plug((2, 'm_inPlug')),
                            m_onEndPlug=plug((3, 'm_inPlug')))
        state = instance.state(0)
        with mock.patch.object(instance, 'follow', return_value=False) as follow:
            state.begin()
            state.end(True)
        self.assertEqual([call.args[1] for call in follow.call_args_list],
                         ['m_onBeginPlug', 'm_subgraphPlug', 'm_onFinishedPlug', 'm_onEndPlug'])

    def test_storage_snapshot_is_passive_and_has_no_class_specific_cache(self):
        component, instance = setup()
        instance.state(0).begin()
        before = snapshot_digest(export_synthetic_component_vm(component))
        with mock.patch.object(runtime.Instance, 'evaluate', side_effect=AssertionError('evaluated')):
            self.assertEqual(snapshot_digest(export_synthetic_component_vm(component)), before)
        state = instance.state(0)
        common = runtime.State(instance, state.node, state.index)
        self.assertEqual(set(vars(state)), set(vars(common)))

    def test_permissive_legacy_state_does_not_require_new_metadata(self):
        _, instance = setup(strict=False, m_6F0C0343=[{'$': 'Unknown'}])
        state = instance.state(0)
        state.begin()
        self.assertTrue(state.active)


if __name__ == '__main__':
    unittest.main()
