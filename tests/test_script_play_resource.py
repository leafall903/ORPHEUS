"""Native resource selection and schema/start ordering for dynamic scripts."""

import unittest
from unittest import mock

from ow174.game.script import expr, play, runtime
from tests.test_script_play_loaded import KEY, definition, dynamic, model_record
from tests.test_script_runtime_budget import graph, node, plug, setter

GUID = 0x0580000000000002
IDENT = play.IDENTIFIER_TYPE
EMPTY = {'$': 'STU_7BD2CA19', 'm_resourceKey': None}


def integer(value):
    return {'$': 'STUConfigVarInt', 'm_value': value}


def resource(guid=GUID):
    return {'$': 'STU_105E1BCC', 'm_resourceKey': None, 'm_graph': f'0x{guid:016X}'}


def row(key, value):
    return {'m_0D09D2D9': f'0x{key:016X}', 'm_value': value}


def inline(rows=(), guid=GUID):
    return {'$': 'STU_9DB46B98', 'm_6525E98B': {
        'm_graph': f'0x{guid:016X}' if guid else None, 'm_1EB5A024': list(rows),
    }}


def array(*configs):
    return {'$': 'STU_06610EBC', 'm_configVars': list(configs)}


def requesting(primary=None, fallback=None, world=None):
    model = graph([node(0, 'STUStatescriptActionPlayScript', m_69F4B52C=primary, m_script=fallback)])
    component = runtime.Component(123, world=world, strict=True)
    return component.create(model, init=False), model.node(0)


def with_schema(model=None, rows=(), kind='STUStatescriptSchema'):
    model = model or definition()
    model.index = 2
    model.fields['m_publicSchema'] = {'$': kind, 'm_entries': list(rows)}
    return model


class TraceWorld(runtime.World):
    def __init__(self, values):
        super().__init__()
        self.values = iter(values)
        self.seen = []

    def native(self, cfg, instance):
        self.seen.append((cfg['$'], instance.id))
        return next(self.values)


class PlayResourceTests(unittest.TestCase):
    def test_inline_primary_evaluates_overrides_once_and_does_not_touch_fallback(self):
        world = TraceWorld([17])
        caller, action = requesting(inline([row(IDENT | 7, {'$': 'Trace'})]), {'$': 'Unused'}, world)
        selected = play.resolve_resource(caller, action)
        self.assertEqual(selected, play.Resource(GUID, ((IDENT | 7, 17),)))
        self.assertEqual(world.seen, [('Trace', caller.id)])

    def test_empty_inline_skips_values_and_selects_fallback(self):
        caller, action = requesting(inline([row(IDENT | 7, {'$': 'Never'})], guid=0), resource())
        self.assertEqual(play.resolve_resource(caller, action), play.Resource(GUID))
        self.assertIsNone(caller.component.fault)

    def test_ordinary_resource_uses_second_evaluation_and_checks_graph_type(self):
        world = TraceWorld([expr.Asset(123), ((expr.Asset(GUID),),)])
        caller, action = requesting({'$': 'Trace'}, world=world)
        self.assertEqual(play.resolve_resource(caller, action), play.Resource(GUID))
        self.assertEqual(len(world.seen), 2)
        caller, action = requesting(resource(0x06B0000000000002), resource(0))
        self.assertIsNone(play.resolve_resource(caller, action))

    def test_second_array_evaluation_uses_only_first_config(self):
        world = TraceWorld([99])
        caller, action = requesting(array(array(resource()), {'$': 'Trace'}), world=world)
        self.assertEqual(play.resolve_resource(caller, action), play.Resource(GUID))
        self.assertEqual(len(world.seen), 1)

    def test_first_config_failure_does_not_select_a_later_array_item(self):
        caller, action = requesting(array(None, resource()), resource(GUID + 1))
        self.assertEqual(play.resolve_resource(caller, action), play.Resource(GUID + 1))

    def test_low32_collisions_replace_in_place_and_zero_key_is_not_enumerated(self):
        world = TraceWorld([99])
        caller, action = requesting(inline([
            row(IDENT | 14, integer(1)), row(IDENT | 6, integer(2)),
            row(IDENT | 22, integer(3)), row(IDENT | 30, integer(4)),
            row(0x10000000E, integer(5)), row(0, {'$': 'Trace'}),
        ]), world=world)
        selected = play.resolve_resource(caller, action)
        self.assertEqual(selected.overrides, tuple((IDENT | key, value) for key, value in
                                                 ((22, 3), (30, 4), (14, 5), (6, 2))))
        self.assertEqual(len(world.seen), 1)

    def test_full_map_growth_reinserts_old_bucket_order(self):
        caller, action = requesting(inline([row(IDENT | key, integer(key))
                                           for key in (14, 6, 22, 30, 38, 46, 54, 62)]))
        selected = play.resolve_resource(caller, action)
        self.assertEqual([key & 0xFFFFFFFF for key, _ in selected.overrides],
                         [14, 62, 22, 38, 54, 6, 30, 46])

    def test_missing_or_failed_override_is_int_zero_but_successful_null_is_null(self):
        caller, action = requesting(inline([
            row(IDENT | 1, None), row(IDENT | 2, EMPTY), row(IDENT | 3, dynamic(88)),
        ]))
        self.assertEqual(dict(play.resolve_resource(caller, action).overrides),
                         {IDENT | 1: 0, IDENT | 2: 0, IDENT | 3: None})

    def test_invalid_map_graph_repeats_inline_evaluation_before_fallback(self):
        world = TraceWorld([0, 0])
        caller, action = requesting(inline([row(IDENT | play.GRAPH_KEY, {'$': 'Trace'})]),
                                    resource(), world)
        self.assertEqual(play.resolve_resource(caller, action), play.Resource(GUID))
        self.assertEqual(len(world.seen), 2)

    def test_map_graph_accepts_non_graph_asset_and_keeps_existing_map_order(self):
        value = expr.Map(((IDENT | 8, False), (play.GRAPH_KEY, (expr.Asset(123),)),
                          (IDENT | 1, 9)))
        caller, action = requesting(dynamic(7), {'$': 'Never'})
        caller.write((0, 7), value)
        self.assertEqual(play.resolve_resource(caller, action),
                         play.Resource(123, ((IDENT | 8, False), (IDENT | 1, 9))))

    def test_unimplemented_configs_and_invalid_map_storage_fail_closed(self):
        for cfg in ({'$': 'Unknown'}, inline([row(IDENT | 0x80000001, integer(1))]),
                    {**resource(), 'm_resourceKey': '0x0D80000000000001'}):
            with self.subTest(cfg=cfg):
                caller, action = requesting(cfg)
                with self.assertRaises(runtime.UnsupportedScriptError):
                    play.resolve_resource(caller, action)
                self.assertIsNotNone(caller.component.fault)
        caller, action = requesting(dynamic(7))
        caller.write((0, 7), expr.Map(((7, 1), (IDENT | 7, 2))))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'duplicate map key'):
            play.resolve_resource(caller, action)

    def test_map_probes_obey_shared_component_work_budget(self):
        caller, action = requesting(inline([row(IDENT | key, integer(key)) for key in range(8, 200, 8)]))
        with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 25),
              self.assertRaises(runtime.ScriptBudgetError)):
            play.resolve_resource(caller, action)
        self.assertIsNotNone(caller.component.fault)

    def test_world_passes_selected_resource_to_backend_before_parent_output(self):
        class Backend(runtime.World):
            def play_resolved_script(self, caller, action, selected):
                self.selected = selected
                self.assert_before = caller.find_var(0, 7, create=False)

        world = Backend()
        model = graph([node(0, 'STUStatescriptActionPlayScript', m_69F4B52C=inline(),
                            m_outPlug=plug((1, 'm_inPlug'))), setter(1, 99)])
        caller = runtime.Component(123, world=world, strict=True).create(model, init=False)
        caller.run_node(model.node(0), None)
        self.assertEqual(world.selected, play.Resource(GUID))
        self.assertIsNone(world.assert_before)
        self.assertEqual(caller.find_var(0, 7).value(), 99)

    def test_no_resource_follows_output_without_requesting_world_service(self):
        model = graph([node(0, 'STUStatescriptActionPlayScript', m_69F4B52C=inline(guid=0),
                            m_outPlug=plug((1, 'm_inPlug'))), setter(1, 99)])
        caller = runtime.Component(123, strict=True).create(model, init=False)
        caller.run_node(model.node(0), None)
        self.assertEqual(caller.find_var(0, 7).value(), 99)


class PlaySchemaTests(unittest.TestCase):
    def test_defaults_and_overrides_precede_init_then_start_parameters(self):
        base = definition()
        model = graph([*model_record(base),
                       node(2, 'STUStatescriptEntry', m_outPlug=plug((3, 'm_inPlug'))),
                       node(3, 'AuditInit')], entries=[2, 0])
        model = with_schema(model, [row(IDENT | 41, integer(7)),
                                    row(IDENT | 42, {'$': 'STU_B5A0CAF0'})])
        observed = []

        def observe(instance, current, caller):
            observed.append((current.cls, instance.find_var(0, 41).value(),
                             instance.find_var(0, 42).value()))
            return True

        component = runtime.Component(123, strict=True)
        instance = component.play_loaded_graph(model, source=456, overrides=[(IDENT | 41, 9)],
                                               params=[(KEY, 17)])
        self.assertEqual([event.kind for event in instance.queue],
                         [runtime.ASSIGN] * 3 + [runtime.ENTRY, runtime.ASSIGN, runtime.ENTRY])
        with mock.patch.dict(runtime.HANDLERS, AuditStart=observe, AuditInit=observe):
            component.run_now()
        self.assertEqual(observed, [('AuditInit', 9, expr.Entity(123)), ('AuditStart', 17, expr.Entity(123))])

    def test_defaults_read_pre_assignment_values_and_missing_configs_are_skipped(self):
        model = with_schema(rows=[row(IDENT | 7, integer(9)), row(IDENT | 8, dynamic(7)),
                                  row(IDENT | 9, None), row(IDENT | 10, EMPTY)])
        component = runtime.Component(123, strict=True)
        instance = component.play_loaded_graph(model, source=123)
        self.assertEqual([event.assignment for event in instance.queue if event.kind == runtime.ASSIGN],
                         [(0, 7, 9), (0, 8, None)])

    def test_array_defaults_and_overrides_use_collecting_value_evaluator(self):
        caller, action = requesting(inline([row(IDENT | 7, array(integer(3), integer(4)))]))
        selected = play.resolve_resource(caller, action)
        self.assertEqual(selected.overrides, ((IDENT | 7, (3, 4)),))
        component = runtime.Component(456, strict=True)
        model = with_schema(rows=[row(IDENT | 8, array(integer(5), integer(6)))])
        instance = component.play_loaded_graph(model, source=123, overrides=selected.overrides)
        self.assertEqual([event.assignment for event in instance.queue if event.kind == runtime.ASSIGN],
                         [(0, 8, (5, 6)), (0, 7, (3, 4))])

    def test_reuse_reapplies_defaults_and_overrides_without_moving_existing_entries(self):
        model = with_schema(rows=[row(IDENT | 41, integer(9))], kind='STU_77990ED7')
        component = runtime.Component(123, strict=True)
        instance = component.create(model)
        instance.write((0, 41), 77)
        instance.post(runtime.Event(0, 0, runtime.ENTRY, node=model.node(0)))
        reused = component.play_loaded_graph(model, source=123, policy=2, overrides=[(IDENT | 41, 11)])
        self.assertIs(reused, instance)
        self.assertEqual([event.kind for event in instance.queue],
                         [runtime.ENTRY, runtime.ASSIGN, runtime.ASSIGN, runtime.ENTRY])

    def test_override_identifiers_use_low16_and_preserve_duplicate_write_order(self):
        component = runtime.Component(123, strict=True)
        instance = component.play_loaded_graph(definition(), source=123,
                                               overrides=[(IDENT | 41, 3), (IDENT | 0x10029, 5)])
        self.assertEqual([event.assignment for event in instance.queue[:2]], [(0, 41, 3), (0, 41, 5)])

    def test_skip_policy_does_not_evaluate_schema_but_unknown_new_schema_stops_component(self):
        model = with_schema(kind='UnknownSchema')
        component = runtime.Component(123, strict=True)
        component.create(model)
        self.assertIsNone(component.play_loaded_graph(model, source=123))
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.play_loaded_graph(model, source=123, policy=1)
        self.assertTrue(all(not instance.queue for instance in component.instances.values()))

    def test_linked_schema_and_unknown_default_config_are_not_silently_accepted(self):
        for model in (with_schema(), with_schema(rows=[row(IDENT | 7, {'$': 'Unknown'})])):
            if not model.fields['m_publicSchema']['m_entries']:
                model.fields['m_publicSchema']['m_89EAAC40'] = '0x0580000000000099'
            component = runtime.Component(123, strict=True)
            with self.assertRaises(runtime.UnsupportedScriptError):
                component.play_loaded_graph(model, source=123)
            self.assertIsNotNone(component.fault)


class ConfigCollectionTests(unittest.TestCase):
    def test_nested_groups_and_dynamic_arrays_flatten_in_order(self):
        caller, _ = requesting()
        caller.write((0, 7), (1, (None, 2), (), 3))
        self.assertEqual(play.evaluate_value(caller, array(integer(0), array(dynamic(7)), integer(4))),
                         (0, 1, None, 2, 3, 4))

    def test_missing_failed_and_scalar_null_configs_produce_no_item(self):
        caller, _ = requesting()
        self.assertEqual(play.evaluate_value(caller, array(None, EMPTY, dynamic(7), integer(0))), (0,))
        self.assertEqual(play.evaluate_value(caller, array()), ())

    def test_scalar_value_path_preserves_successful_null_and_caller_failure_destination(self):
        caller, _ = requesting()
        destination = object()
        self.assertIs(play.evaluate_value(caller, EMPTY, initial=destination), destination)
        self.assertIsNone(play.evaluate_value(caller, dynamic(7), initial=destination))

    def test_collection_does_not_change_general_first_item_evaluator(self):
        caller, _ = requesting()
        self.assertEqual(play.evaluate_value(caller, array(integer(1), integer(2))), (1, 2))
        # The native +20 array path is still unimplemented, so a strict caller
        # cannot accidentally receive this +28 array result through expr.evaluate.
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller.evaluate(array(integer(1), integer(2)))

    def test_deep_arrays_stop_with_component_fault_before_python_recursion(self):
        caller, _ = requesting()
        config = integer(1)
        for _ in range(play.MAX_CONFIG_DEPTH + 1):
            config = array(config)
        with self.assertRaises(runtime.ScriptBudgetError):
            play.evaluate_value(caller, config)
        self.assertIsNotNone(caller.component.fault)


if __name__ == '__main__':
    unittest.main()
