"""Native entry bindings, ordered packet scopes, filtering and owned delivery."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import entity_queries, expr, runtime, scene
from ow174.game.script import message_receive as receive
from tests.script_fixtures import export_synthetic_component_vm, graph, node, plug

OWNER, CALLER, INSTIGATOR, SUBJECT = 0xA0000100, 0xA0000101, 0xA0000102, 0xA0000103
MESSAGE, KEY = 0x02400000000006FF, 0x0A400000000003BE


def variable(identifier, scope=0):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': scope}


def binding(key, identifier, scope=0):
    return {'m_B5051BCE': f'0x{key:016X}' if key is not None else None,
            'm_out_Var': variable(identifier, scope) if identifier is not None else None}


def literal(value):
    return {'$': 'ReceiveLiteral', 'value': value}


def entry(position=0, **fields):
    config = {'m_gameMessage': {'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{MESSAGE:016X}'},
              'm_params': [], 'm_FDBDCB70': 0}
    config.update(fields)
    return node(position, receive.ENTRY_CLASS, **config)


class ReceiveWorld(scene.SceneWorld):
    def __init__(self):
        super().__init__()
        self.filter_calls = []

    def native(self, cfg, instance):
        if cfg.get('$') == 'ReceiveLiteral':
            value = cfg['value']
            return value(instance) if callable(value) else value
        return super().native(cfg, instance)

    def game_message_filter(self, instance, node, config, caller):
        self.filter_calls.append(caller)
        value = config['value']
        return value(instance) if callable(value) else value


def setup(model=None):
    world = ReceiveWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    model = model or graph([entry()], entries=[0])
    instance = component.create(model, init=False)
    instance.initialized = True
    return world, component, instance


def packet(params=(), caller=CALLER, instigator=INSTIGATOR):
    return receive.Packet(MESSAGE, caller, instigator, tuple(params))


def assignments(instance):
    return [event.assignment for event in instance.queue if event.kind == runtime.ASSIGN]


def query_context(world, result):
    context = entity_queries.Context(OWNER, True, True, True, lookup=lambda identifier: result)
    world.set_entity_query_context(OWNER, context)
    return context


class MessageReceiveTests(unittest.TestCase):
    def test_named_positional_repeated_and_missing_parameters_queue_incoming_order(self):
        model = graph([entry(m_params=[binding(KEY, 41), binding(KEY, 99),
                                      binding(KEY+1, 42, 1), binding(KEY+2, 43)])], entries=[0])
        _, component, instance = setup(model)
        instance.write((0, 43), 88)
        self.assertEqual(receive.deliver(component, packet([(KEY, (1,)), (KEY, (2,)), (0, (3,)),
                                                           (KEY+99, (4,))])), 1)
        self.assertEqual(assignments(instance), [(0, 41, (1,)), (0, 41, (2,)), (1, 42, (3,))])
        self.assertEqual([event.kind for event in instance.queue], [runtime.ASSIGN]*3+[runtime.ENTRY])
        self.assertEqual(instance.find_var(0, 43).value(), 88)
        self.assertIsNone(instance.find_var(0, 41, create=False))
        component.run_now()
        self.assertEqual(instance.find_var(0, 41).value(), (2,))
        self.assertIsNone(instance.find_var(0, 99, create=False))
        self.assertEqual(component.entity_var(42).value(), (3,))
        self.assertEqual(instance.find_var(0, 43).value(), 88)

    def test_full_keys_do_not_alias_low_identifiers_and_null_bindings_keep_searching(self):
        model = graph([entry(m_params=[None, binding(KEY, None), binding(KEY, 41), binding(KEY, 42)])],
                      entries=[0])
        _, component, instance = setup(model)
        receive.deliver(component, packet([(KEY+(1 << 48), (9,)), (KEY, ())]))
        self.assertEqual(assignments(instance), [(0, 41, ())])

    def test_parameter_identifier_zero_skips_write_and_does_not_try_later_duplicates(self):
        model = graph([entry(m_params=[binding(KEY, 0), binding(KEY, 41)])], entries=[0])
        _, component, instance = setup(model)
        receive.deliver(component, packet([(KEY, (1,))]))
        self.assertEqual(assignments(instance), [])
        self.assertEqual(len(instance.queue), 1)

    def test_context_fields_write_after_parameters_and_keep_invalid_entities_typed(self):
        model = graph([entry(m_params=[binding(KEY, 41)], m_B60ABDA4=variable(41),
                              m_FF703F20=variable(42), m_44D0D060=variable(43))], entries=[0])
        world, component, instance = setup(model)
        query_context(world, None)
        receive.deliver(component, packet([(KEY, (17,))], caller=0, instigator=0xFFFFFFFF))
        self.assertEqual(assignments(instance), [(0, 41, (17,)), (0, 41, expr.Entity(0)),
            (0, 42, expr.Entity(0xFFFFFFFF)), (0, 43, expr.Entity(0xFFFFFFFF))])

    def test_context_outputs_require_dynamic_even_when_a_parameter_lvalue_expression_is_known(self):
        expression = {'$': 'STUConfigVarExpression', 'm_expression': {
            'm_opcodes': [8, 0], 'm_dynamicVars': [variable(41)]}}
        model = graph([entry(m_B60ABDA4=expression, m_FF703F20={'$': 'STUConfigVarInt', 'm_value': 9},
                              m_44D0D060=expression, m_params=[{'m_B5051BCE': f'0x{KEY:016X}',
                                                              'm_out_Var': expression}])], entries=[0])
        _, component, instance = setup(model)
        receive.deliver(component, packet([(KEY, (7,))]))
        self.assertEqual(assignments(instance), [(0, 41, (7,))])

    def test_source_current_possession_uses_valid_forward_value_or_returns_the_instigator(self):
        for target, expected in ((None, INSTIGATOR), (entity_queries.Target(INSTIGATOR, None), INSTIGATOR),
            (entity_queries.Target(INSTIGATOR, entity_queries.Possession(0)), INSTIGATOR),
            (entity_queries.Target(INSTIGATOR, entity_queries.Possession(0x1FFFFFFF)), INSTIGATOR),
            (entity_queries.Target(INSTIGATOR, entity_queries.Possession(SUBJECT)), SUBJECT)):
            world, component, instance = setup(graph([entry(m_44D0D060=variable(41))], entries=[0]))
            context = query_context(world, target)
            context.watch_possession = lambda *_: self.fail('unexpected dependency subscription')
            receive.deliver(component, packet())
            self.assertEqual(assignments(instance), [(0, 41, expr.Entity(expected))])

    def test_absent_subject_output_does_not_require_lookup_and_unknown_subject_services_are_strict(self):
        _, component, _ = setup()
        self.assertEqual(receive.deliver(component, packet()), 1)
        _, component, instance = setup(graph([entry(m_44D0D060=variable(41))], entries=[0]))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'subject lookup'):
            receive.deliver(component, packet())
        self.assertFalse(instance.queue)
        self.assertFalse(instance.params)

    def test_subject_unknown_components_bad_values_and_callback_retirement_remain_errors(self):
        for result in (NotImplemented, entity_queries.Target(CALLER, None),
                       entity_queries.Target(INSTIGATOR)):
            world, component, _ = setup(graph([entry(m_44D0D060=variable(41))], entries=[0]))
            query_context(world, result)
            with self.assertRaises(runtime.UnsupportedScriptError):
                receive.deliver(component, packet())
        world, component, instance = setup(graph([entry(m_44D0D060=variable(41))], entries=[0]))
        context = query_context(world, None)

        def retire(identifier):
            context.retire()
            return None

        context.lookup = retire
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'subject lifecycle'):
            receive.deliver(component, packet())
        self.assertFalse(instance.queue)

    def test_parameter_scope_builtins_override_named_values_and_first_duplicate_wins(self):
        world, _, instance = setup()
        query_context(world, entity_queries.Target(INSTIGATOR, entity_queries.Possession(SUBJECT)))
        value = packet([(KEY, (1,)), (KEY, (2,)), (receive.PARAMETER_TYPE+9, (99,))])
        scope = receive.ParamScope(instance, instance.graph.node(0), value)
        self.assertEqual(scope.get(KEY & 0xFFFF), (1,))
        self.assertEqual([scope.get(key) for key in (9, 10, 11)],
                         [expr.Entity(CALLER), expr.Entity(INSTIGATOR), expr.Entity(SUBJECT)])
        self.assertIsNone(scope.get(None))
        self.assertEqual(scope.get(123, 'missing'), 'missing')

    def test_packet_scope_exists_while_selecting_and_filtering_then_is_removed_before_entry(self):
        seen = []

        def selection(instance):
            seen.append(instance.context().param(KEY & 0xFFFF))
            return expr.Asset(MESSAGE)

        def predicate(instance):
            seen.append(instance.context().param(9))
            return True

        model = graph([entry(m_gameMessage=literal(selection), m_filter=literal(predicate),
                             m_outPlug=plug((1, 'm_inPlug'))), node(1, 'ObserveMessageScope')], entries=[0])
        _, component, instance = setup(model)
        with mock.patch.dict(runtime.HANDLERS, ObserveMessageScope=lambda inst, *_:
                             seen.append(inst.context().param(KEY & 0xFFFF)) or True):
            receive.deliver(component, packet([(KEY, (7,))]))
            self.assertFalse(instance.params)
            component.run_now()
        self.assertEqual(seen, [(7,), expr.Entity(CALLER), None])

    def test_missing_parameter_scope_value_falls_through_to_an_outer_provider(self):
        _, component, instance = setup(graph([entry(m_gameMessage=literal(
            lambda inst: inst.context().param(123)))], entries=[0]))
        outer = {123: expr.Asset(MESSAGE)}
        instance.params.append(outer)
        self.assertEqual(receive.deliver(component, packet()), 1)
        self.assertEqual(instance.params, [outer])

    def test_entry_array_order_and_one_shot_flags_are_native_entry_indices(self):
        model = graph([entry(0, m_FDBDCB70=1), entry(2, m_FDBDCB70=1)], entries=[2, 0])
        _, component, instance = setup(model)
        self.assertEqual(receive.deliver(component, packet()), 2)
        self.assertEqual([event.node.pos for event in instance.queue], [2, 0])
        self.assertEqual(instance.disabled_message_entries, {0, 1})
        self.assertEqual(receive.deliver(component, packet()), 0)

    def test_non_entry_and_client_only_nodes_do_not_become_native_entry_receivers(self):
        model = graph([entry(0), entry(1), node(2, 'STUStatescriptStateGameMessageEntry', state=0)],
                      entries=[None, 1, 2])
        model.node(1).client_only = True
        _, component, instance = setup(model)
        self.assertEqual(receive.deliver(component, packet()), 0)
        self.assertFalse(instance.queue)

    def test_non_entity_entry_context_is_skipped_before_config_evaluation(self):
        model = graph([entry(m_5AEA0A51=1, m_gameMessage={'$': 'Unknown'})], entries=[0])
        _, component, instance = setup(model)
        self.assertEqual(receive.deliver(component, packet()), 0)
        self.assertFalse(instance.queue)

    def test_zero_message_never_selects_an_entry_even_when_the_selector_returns_zero(self):
        for cfg in (None, literal(expr.EVALUATION_FAILED), literal(None), literal(17)):
            _, component, instance = setup(graph([entry(m_gameMessage=cfg)], entries=[0]))
            self.assertEqual(receive.deliver(component, receive.Packet(0, CALLER, INSTIGATOR)), 0)
            self.assertFalse(instance.queue)

    def test_failed_nonasset_and_mismatched_messages_skip_filter_and_bindings(self):
        for value in (expr.EVALUATION_FAILED, None, 17, expr.Asset(MESSAGE+1)):
            world, component, instance = setup(graph([entry(m_gameMessage=literal(value),
                m_filter=literal(lambda _: self.fail('filter evaluated')), m_params=[True])], entries=[0]))
            self.assertEqual(receive.deliver(component, packet()), 0)
            self.assertFalse(world.filter_calls)
            self.assertFalse(instance.params)

    def test_filter_result_absence_and_zero_caller_bypass_have_distinct_paths(self):
        for answer, caller, count in ((None, CALLER, 1), (True, CALLER, 1), (False, CALLER, 0),
                                     (False, 0, 1), (False, 0x1FFFFFFF, 0)):
            world, component, _ = setup(graph([entry(m_filter=literal(answer))], entries=[0]))
            self.assertEqual(receive.deliver(component, packet(caller=caller)), count)
            self.assertEqual(world.filter_calls, [caller])

    def test_unknown_filter_is_strict_even_for_zero_caller_and_scope_is_always_popped(self):
        for caller in (0, CALLER):
            world, component, instance = setup(graph([entry(m_filter=literal(True))], entries=[0]))
            world.game_message_filter = runtime.World.game_message_filter.__get__(world)
            with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'game message filter'):
                receive.deliver(component, packet(caller=caller))
            self.assertFalse(instance.params)
            self.assertFalse(instance.queue)

    def test_retirement_during_selection_or_filter_cannot_queue_an_entry(self):
        for field in ('m_gameMessage', 'm_filter'):
            world, component, instance = setup()

            def retire(inst, world=world, component=component, field=field):
                world.unbind_script_component(OWNER, component)
                return expr.Asset(MESSAGE) if field == 'm_gameMessage' else True

            instance.graph.node(0).fields[field] = literal(retire)
            with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'source lifecycle'):
                receive.deliver(component, packet())
            self.assertFalse(instance.params)
            self.assertFalse(instance.queue)

    def test_stale_instance_does_not_borrow_replacement_context(self):
        world, component, instance = setup()
        world.unbind_script_component(OWNER, component)
        replacement = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            receive.deliver(component, packet())
        self.assertIsNone(replacement.fault)
        self.assertFalse(instance.params)

    def test_binding_budget_exhaustion_cancels_partial_assignments_and_entry(self):
        _, component, instance = setup(graph([entry(m_params=[binding(KEY, 41)])], entries=[0]))
        with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 12),
              self.assertRaises(runtime.ScriptBudgetError)):
            receive.deliver(component, packet([(KEY, (1,))]*50))
        self.assertFalse(instance.queue)
        self.assertFalse(instance.params)

    def test_scoped_parameter_search_cannot_bypass_the_shared_budget(self):
        model = graph([entry(m_gameMessage=literal(lambda inst: inst.context().param(0xFFFF)))], entries=[0])
        _, component, instance = setup(model)
        values = [(KEY+i, (i,)) for i in range(50)]
        with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 12),
              self.assertRaises(runtime.ScriptBudgetError)):
            receive.deliver(component, packet(values))
        self.assertFalse(instance.params)
        self.assertFalse(instance.queue)

    def test_queued_message_on_dormant_instance_waits_for_initialization(self):
        model = graph([entry(m_params=[binding(KEY, 41)])], entries=[0])
        component = runtime.Component(OWNER, strict=True)
        instance = component.create(model, init=False)
        self.assertEqual(receive.deliver(component, packet([(KEY, (1,))])), 1)

        component.run_frame(1)
        self.assertIsNone(component.fault)
        self.assertIsNone(instance.find_var(expr.INSTANCE, 41, create=False))
        self.assertTrue(instance.queue)

        instance.init()
        component.run_frame(2)
        self.assertEqual(instance.vars[41].value(), (1,))
        self.assertFalse(instance.queue)

    def test_pending_bindings_disabled_entries_and_scope_export_without_execution(self):
        model = graph([entry(m_params=[binding(KEY, 41)], m_FDBDCB70=1)], entries=[0])
        component = runtime.Component(OWNER, strict=True)
        instance = component.create(model, init=False)
        receive.deliver(component, packet([(KEY, (1,))]))
        instance.params.append(receive.ParamScope(instance, model.node(0), packet()))
        queued = list(instance.queue)
        with (mock.patch.object(receive.ParamScope, 'get', side_effect=AssertionError('resolved')),
              mock.patch.object(runtime.Instance, 'dispatch', side_effect=AssertionError('executed'))):
            before = snapshot_digest(export_synthetic_component_vm(component))
            instance.disabled_message_entries.clear()
            self.assertNotEqual(before, snapshot_digest(export_synthetic_component_vm(component)))
        self.assertEqual(instance.queue, queued)

    def test_actual_message_entry_class_does_not_keep_an_idle_transient_script_alive(self):
        _, component, instance = setup()
        instance.auto_retire = True
        instance.initialized_frame = 0
        component.frame = 3
        receive.deliver(component, packet())
        self.assertFalse(instance.stopped)
        component.run_now()
        self.assertTrue(instance.stopped)
        self.assertNotIn(instance.id, component.instances)

    def test_packet_validates_native_identities_and_keeps_ordered_repeated_full_keys(self):
        self.assertEqual(packet([(KEY, 1), (KEY, 2)]).params, ((KEY, 1), (KEY, 2)))
        for args in ((True, 0, 0, ()), (MESSAGE, -1, 0, ()), (MESSAGE, 0, 1 << 32, ()),
                     (MESSAGE, 0, 0, {}), (MESSAGE, 0, 0, ((True, 1),))):
            with self.assertRaises(ValueError):
                receive.Packet(*args)


if __name__ == '__main__':
    unittest.main()
