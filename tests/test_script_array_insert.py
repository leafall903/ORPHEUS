"""Authored array insertion, ordered providers, notifications and strict storage limits."""

import math
import unittest
from unittest import mock

from ow174.game.script import expr, runtime
from tests.test_script_play_loaded import dynamic
from tests.test_script_runtime_budget import graph, node, plug, setter

ABSENT = object()


def setup(value=ABSENT, *, index=ABSENT, key=ABSENT, unique=0, current=None,
          source=ABSENT, output=ABSENT, world=None, action_class='STU_4C3509FB'):
    fields = {'m_26E3B366': dynamic(1) if output is ABSENT else output,
        'm_value': dynamic(2) if source is ABSENT else source,
        'm_index': None if index is ABSENT else dynamic(3),
        'm_key': None if key is ABSENT else dynamic(4), 'm_8AD67B20': unique,
        'm_outPlug': plug((1, 'm_inPlug'))}
    following = setter(1, 19)
    following['fields']['m_out_Var']['m_identifier'] = 90
    model = graph([node(0, action_class, **fields), following,
                   node(2, 'STUStatescriptState', state=0)])
    component = runtime.Component(123, strict=True, world=world)
    active = component.create(model, init=False)
    active.initialized = True
    active.write((0, 1), current)
    for identifier, selected in ((2, value), (3, index), (4, key)):
        if selected is not ABSENT:
            active.write((0, identifier), selected)
    return component, active, model.node(0)


def run(*args, **kwargs):
    component, active, action = setup(*args, **kwargs)
    active.run_node(action, None)
    return component, active


class ArrayInsertTests(unittest.TestCase):
    def test_fold_uses_the_native_accumulator_item_index_and_count_scope(self):
        def expression(keys, code):
            return {'$': 'STU_919FD47C', 'm_expression': {
                'm_dynamicVars': [dynamic(key) for key in keys], 'm_opcodes': code,
            }}

        component, active, _ = setup((4, 7, 1))
        fold = {'$': 'STU_A6B80C63', 'm_814F8EC3': dynamic(2),
                'm_418D9D9D': expression((1294,), [13, 0, 0]),
                'm_C239F989': expression((1293, 1294, 12), [13, 0, 13, 1, 23, 13, 2, 23, 0]),
                'm_8054375D': expression((1293, 1050, 12), [13, 0, 13, 1, 23, 13, 2, 23, 0])}
        with component.parameter_scope({1293: 99, 12: 9}):
            self.assertEqual(active.evaluate(fold), 25)
            self.assertEqual(active.context().param(1293), 99)
        active.write((0, 2), ())
        self.assertIsNone(active.evaluate(fold | {'m_8054375D': None}))
        self.assertEqual(active.evaluate(fold), 0)

    def test_single_value_insertion_keeps_nested_arrays_intact(self):
        _, active = run((2, (3, None), 4), current=(1, 5), index=1, action_class='STU_CBE8BDAA')
        self.assertEqual(active.find_var(0, 1).value(), (1, (2, (3, None), 4), 5))
        self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_append_flattens_nested_source_arrays_in_order_and_follows_output(self):
        _, active = run((2, (3, None), 4), current=(1,))
        self.assertEqual(active.find_var(0, 1).value(), (1, 2, 3, None, 4))
        self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_nonarray_target_becomes_empty_before_append_without_preserving_scalar(self):
        for initial in (None, 99, expr.Entity(123), expr.Map(((2, 9),))):
            _, active = run(7, current=initial)
            self.assertEqual(active.find_var(0, 1).value(), (7,))

    def test_positive_index_inserts_rows_without_overwriting_existing_values(self):
        _, active = run((7, 8), current=(1, 2, 3), index=1)
        self.assertEqual(active.find_var(0, 1).value(), (1, 7, 8, 2, 3))

    def test_index_at_end_appends_and_index_past_end_creates_int_zero_gaps(self):
        for index, expected in [(2, (1, 2, 7)), (4, (1, 2, 0, 0, 7))]:
            _, active = run(7, current=(1, 2), index=index)
            self.assertEqual(active.find_var(0, 1).value(), expected)
            self.assertTrue(all(type(item) is int for item in expected))

    def test_empty_source_still_coerces_target_and_grows_index_gaps(self):
        for source in (None, ()):
            _, active = run(source, current=99, index=3)
            self.assertEqual(active.find_var(0, 1).value(), (0, 0, 0))

    def test_unique_uses_typed_native_equality_and_compares_new_rows_and_gaps(self):
        _, active = run((0, 2, 2, 3), current=(1,), unique=1, index=3)
        self.assertEqual(active.find_var(0, 1).value(), (1, 0, 0, 2, 3))
        _, active = run((1.0+expr.EPSILON/2, True, expr.Entity(1)), current=(1,), unique=255)
        self.assertEqual(active.find_var(0, 1).value(), (1, expr.Entity(1)))

    def test_index_rounds_half_away_and_nonfinite_values_use_native_int_min(self):
        for index, expected in [(1.5, (1, 2, 7, 3)), (-0.5, (1, 2, 3, 7)),
                (math.nan, (1, 2, 3, 7)), (math.inf, (1, 2, 3, 7)), (None, (7, 1, 2, 3)),
                ((), (1, 2, 3, 7)), (expr.Asset(17), (1, 2, 3, 7))]:
            _, active = run(7, current=(1, 2, 3), index=index)
            self.assertEqual(active.find_var(0, 1).value(), expected)

    def test_index_scalar_provider_uses_first_group_config_while_value_uses_all(self):
        component, active, action = setup(source={'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'STUConfigVarInt', 'm_value': 7}, {'$': 'STUConfigVarInt', 'm_value': 8}]},
            current=(1, 2, 3), index=0)
        action.fields['m_index'] = {'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'STUConfigVarInt', 'm_value': 1}, {'$': 'UnknownMustNotEvaluate'}]}
        active.run_node(action, None)
        self.assertEqual(active.find_var(0, 1).value(), (1, 7, 8, 2, 3))
        self.assertIsNone(component.fault)

    def test_absent_value_or_output_skips_index_and_value_providers(self):
        for fields in ({'source': None}, {'output': None}):
            fields = {'source': {'$': 'UnknownSource'}, **fields}
            _, active, action = setup(**fields)
            action.fields['m_index'] = {'$': 'UnknownIndex'}
            active.run_node(action, None)
            self.assertEqual(active.find_var(0, 1).value(), None)
            self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_known_nondynamic_output_skips_and_unknown_output_retains_strict_boundary(self):
        _, active = run(source={'$': 'UnknownSource'}, output={'$': 'STUConfigVarInt', 'm_value': 1})
        self.assertEqual(active.find_var(0, 90).value(), 19)
        _, active, action = setup(1, output={'$': 'UnknownOutput'})
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'array insertion output target'):
            active.run_node(action, None)

    def test_zero_output_identifier_does_not_create_storage_or_evaluate_source(self):
        _, active = run(source={'$': 'UnknownSource'}, output=dynamic(0))
        self.assertIsNone(active.find_var(0, 0, create=False))
        self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_invalid_key_skips_without_coercion_or_source_evaluation(self):
        for key in (0, None, True, 1.0, (), expr.Map()):
            _, active = run(key=key, current=99, source={'$': 'UnknownSource'})
            self.assertEqual(active.find_var(0, 1).value(), 99)
            self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_failed_index_keeps_minus_one_while_failed_key_keeps_int_zero(self):
        for field, expected in [('m_index', (1, 7)), ('m_key', (1,))]:
            _, active, action = setup(7, current=(1,))
            action.fields[field] = {'$': 'STU_7BD2CA19', 'm_resourceKey': None}
            active.run_node(action, None)
            self.assertEqual(active.find_var(0, 1).value(), expected)

    def test_keyed_storage_and_linked_checkpoint_consumers_remain_strict(self):
        _, active, action = setup(7, key=expr.Asset(17), current=(1,))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'array insertion keyed row storage'):
            active.run_node(action, None)
        _, active, action = setup(7, current=(1,))
        active.find_var(0, 1).links.append(runtime.Link(active.state(0)))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'linked variable checkpoint'):
            active.run_node(action, None)

    def test_self_reference_terminates_entire_iterator_instead_of_duplicating_the_array(self):
        for source, expected in [(dynamic(1), (1, 2)), ({'$': 'STU_06610EBC', 'm_configVars': [
                {'$': 'STUConfigVarInt', 'm_value': 7}, dynamic(1),
                {'$': 'UnknownAfterSelf'}]}, (1, 2, 7))]:
            _, active = run(current=(1, 2), source=source)
            self.assertEqual(active.find_var(0, 1).value(), expected)

    def test_entity_scope_output_and_self_iterator_use_the_actual_variable(self):
        component, active, action = setup(7, output=dynamic(9, scope=1))
        active.run_node(action, None)
        self.assertEqual(component.entity_var(9).value(), (7,))
        self.assertIsNone(active.find_var(0, 9, create=False))

    def test_later_providers_see_rows_before_one_final_watcher_notification(self):
        seen = []
        class Audit(runtime.World):
            def native(self, cfg, instance):
                seen.append((instance.find_var(0, 1).base, watcher.flags_changed))
                return 8
        component, active, action = setup(current=99, world=Audit(), source={
            '$': 'STU_06610EBC', 'm_configVars': [
                {'$': 'STUConfigVarInt', 'm_value': 7}, {'$': 'Audit'}]})
        watcher = active.state(0)
        watcher.begin()
        active.find_var(0, 1).watchers[watcher] = None
        active.run_node(action, None)
        self.assertEqual(seen, [((7,), False)])
        self.assertTrue(watcher.flags_changed)
        self.assertEqual(len([event for event in active.queue if event.kind == runtime.CHANGED]), 1)
        self.assertEqual(component.now, 0)

    def test_native_overlimit_diagnostic_stays_explicit_before_or_after_mutation(self):
        for index, changed in [(201, False), (200, True)]:
            _, active, action = setup(7, current=(1,), index=index)
            with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'native limit diagnostic'):
                active.run_node(action, None)
            self.assertEqual(len(active.find_var(0, 1).base), 201 if changed else 1)
            self.assertIsNone(active.find_var(0, 90, create=False))

    def test_retired_owner_during_source_evaluation_cannot_commit_or_follow(self):
        class Retire(runtime.World):
            def native(self, cfg, instance):
                instance.component.destroy(instance)
                return 7
        _, active, action = setup(current=(1,), source={'$': 'Retire'}, world=Retire())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'array action owner retired'):
            active.run_node(action, None)
        self.assertIsNone(active.find_var(0, 90, create=False))


class ClearArrayTests(unittest.TestCase):
    def test_clear_assigns_a_typed_empty_array_for_every_existing_value_kind(self):
        for initial in (None, 0, False, 2.5, expr.Entity(123), expr.Map(((2, 9),)), (1, (2, 3))):
            _, active, action = setup(current=initial)
            action.cls = 'STU_1C2CC6D5'
            active.run_node(action, None)
            self.assertEqual(active.find_var(0, 1).value(), ())
            self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_entity_scope_clear_uses_the_normal_variable_writer(self):
        component, active, action = setup(output=dynamic(9, scope=1))
        action.cls = 'STU_1C2CC6D5'
        active.write((1, 9), (1, 2))
        active.run_node(action, None)
        self.assertEqual(component.entity_var(9).value(), ())
        self.assertIsNone(active.find_var(0, 9, create=False))

    def test_missing_zero_and_known_nondynamic_output_still_follow_without_assignment(self):
        for output in (None, dynamic(0), {'$': 'STUConfigVarInt', 'm_value': 7}):
            _, active, action = setup(current=99, output=output)
            action.cls = 'STU_1C2CC6D5'
            active.run_node(action, None)
            self.assertEqual(active.find_var(0, 1).value(), 99)
            self.assertIsNone(active.find_var(0, 0, create=False))
            self.assertEqual(active.find_var(0, 90).value(), 19)

    def test_unknown_clear_output_is_a_strict_boundary(self):
        _, active, action = setup(current=(1,), output={'$': 'UnknownOutput'})
        action.cls = 'STU_1C2CC6D5'
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'array clear output target'):
            active.run_node(action, None)
        self.assertEqual(active.find_var(0, 1).value(), (1,))

    def test_clear_notifies_links_without_claiming_an_overridden_effective_value_changed(self):
        _, active, action = setup(current=(1,))
        action.cls = 'STU_1C2CC6D5'
        state = active.state(0)
        state.begin()
        var = active.find_var(0, 1)
        var.links.append(runtime.Link(state))
        var.watchers[state] = None
        with mock.patch.object(state, 'var_written', wraps=state.var_written) as written:
            active.run_node(action, None)
        written.assert_called_once_with(var)
        self.assertEqual(var.base, ())
        self.assertFalse(state.flags_changed)

    def test_legacy_permissive_policy_keeps_its_existing_output_only_behavior(self):
        for cls in ('STU_4C3509FB', 'STU_1C2CC6D5'):
            component, active, action = setup(7, current=(1,))
            component.strict = False
            action.cls = cls
            active.run_node(action, None)
            self.assertEqual(active.find_var(0, 1).value(), (1,))
            self.assertEqual(active.find_var(0, 90).value(), 19)


if __name__ == '__main__':
    unittest.main()
