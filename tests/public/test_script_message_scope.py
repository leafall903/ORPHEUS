"""Persistent sender scopes, native VM provider ordering and source evaluation."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import controller_heroes, entity_queries, expr, message_scope, message_state, runtime
from tests.test_body_vm_snapshot import raw
from tests.test_script_message_state import OTHER, OWNER, begin, literal, setup
from tests.test_script_runtime_budget import graph, node, plug

KEY = 0x0A4000000000008D


def row(key=KEY, value=None):
    return {'m_B5051BCE': None if key is None else f'0x{key:016X}', 'm_value': value}


def fixture(rows=(), *, shared=True):
    world, component, source, sender, context, _, _ = setup(m_params=list(rows))
    begin(sender)
    owner = component
    if not shared:
        world.bind(OTHER)
        owner = runtime.Component(OTHER, world=world, strict=True)
        world.bind_script_component(OTHER, owner)
        receiving = message_state.Context(owner, context.host,
            policy=lambda state: message_state.Policy(False, False, False),
            frame=lambda state: 10, priority=lambda state, key: None)
        world.set_message_state_context(OTHER, receiving)
    target = owner.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
    target.initialized = True
    receiver = target.state(0)
    begin(receiver)
    return world, source, sender, target, receiver, message_scope.ParamScope(receiver, sender)


class MessageScopeTests(unittest.TestCase):
    def test_missing_parameter_returns_successful_zero_and_shadows_an_outer_provider(self):
        _, _, _, target, _, scope = fixture()
        target.params.append({KEY & 0xFFFF: 99})
        with target.component.parameter_scope(scope):
            self.assertEqual(target.context().parameter_result(KEY & 0xFFFF), (True, 0))
        self.assertEqual(target.context().param(KEY & 0xFFFF), 99)

    def test_first_duplicate_wins_even_if_its_config_pointer_is_absent(self):
        _, _, _, target, _, scope = fixture([None, row(value=None), row(value=literal(7))])
        target.params.append({KEY & 0xFFFF: 99})
        self.assertEqual(scope.lookup_guid(KEY, initial=expr.Asset(12)), (False, expr.Asset(12)))
        with target.component.parameter_scope(scope):
            self.assertEqual(target.context().param(KEY & 0xFFFF), 99)
        self.assertFalse(target.component.parameter_scopes)

    def test_full_keys_do_not_alias_another_resource_type(self):
        _, _, _, _, _, scope = fixture([row(KEY+(1 << 48), literal(5)), row(value=literal(7))])
        self.assertEqual(scope.lookup_guid(KEY), (True, 7))
        self.assertEqual(scope.lookup_guid(KEY+(1 << 48)), (True, 5))

    def test_scalar_destination_preserves_null_arrays_and_evaluation_failure_separately(self):
        for value in (None, (), (1, None), expr.EVALUATION_FAILED):
            _, _, _, _, _, scope = fixture([row(value=literal(value))])
            initial = expr.Asset(12)
            self.assertEqual(scope.lookup_guid(KEY, initial=initial),
                             (False, initial) if value is expr.EVALUATION_FAILED else (True, value))

    def test_source_instance_values_are_read_and_the_receiver_is_the_watcher(self):
        config = {'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0}
        _, source, sender, target, receiver, scope = fixture([row(value=config)], shared=False)
        source.write((0, 41), 7)
        target.write((0, 41), 99)
        self.assertEqual(scope.lookup_guid(KEY), (True, 7))
        variable = source.find_var(0, 41)
        self.assertIn(receiver, variable.watchers)
        self.assertNotIn(sender, variable.watchers)
        source.write((0, 41), 8)
        self.assertTrue(any(event.kind == runtime.CHANGED for event in target.queue))

    def test_native_dependency_hook_belongs_to_the_receiver(self):
        _, _, sender, _, receiver, scope = fixture([row(value=literal(7))])
        sender.natives.clear()
        observed = []
        with mock.patch.object(runtime.State, 'native_dependency',
                               lambda state, cfg: observed.append((state, cfg['$']))):
            self.assertEqual(scope.lookup_guid(KEY), (True, 7))
        self.assertEqual(observed, [(receiver, 'SenderLiteral')])
        self.assertFalse(sender.natives)
        self.assertIn('SenderLiteral', receiver.natives)

    def test_builtins_override_named_rows_and_use_the_senders_namespace(self):
        world, source, sender, target, _, scope = fixture([
            row(message_scope.PARAMETER_TYPE+9, literal(77))], shared=False)
        source.set_source_entity(0xA0000200)
        seen = []
        world.set_entity_query_context(OWNER, entity_queries.Context(OWNER, True, True, True,
            lookup=lambda identifier: seen.append(identifier) or entity_queries.Target(
                identifier, entity_queries.Possession(0xA0000300))))
        self.assertEqual(scope.lookup_parameter(9), (True, expr.Entity(OWNER)))
        self.assertEqual(scope.lookup_parameter(10), (True, expr.Entity(0xA0000200)))
        self.assertEqual(scope.lookup_parameter(11), (True, expr.Entity(0xA0000300)))
        self.assertEqual(seen, [0xA0000200])
        self.assertNotEqual(sender.instance.component.entity, target.component.entity)

    def test_parameter_zero_uses_zero_guid_instead_of_a_typed_zero_identifier(self):
        _, _, _, _, _, scope = fixture([row(0, literal(7)), row(message_scope.PARAMETER_TYPE, literal(9))])
        self.assertEqual(scope.lookup_parameter(0), (True, 7))
        self.assertEqual(scope.lookup_guid(message_scope.PARAMETER_TYPE), (True, 9))

    def test_same_component_instances_share_the_managed_scope(self):
        config = literal(lambda instance: instance.context().param((KEY+1) & 0xFFFF))
        _, source, _, target, _, scope = fixture([row(value=config), row(KEY+1, literal(7))])
        with target.component.parameter_scope(scope):
            self.assertEqual(target.context().param(KEY & 0xFFFF), 7)
            self.assertEqual(source.context().param((KEY+1) & 0xFFFF), 7)
        self.assertFalse(source.params)
        self.assertFalse(target.params)

    def test_other_components_do_not_borrow_the_receivers_provider_stack(self):
        config = literal(lambda instance: instance.context().param((KEY+1) & 0xFFFF))
        _, source, _, target, _, scope = fixture([row(value=config), row(KEY+1, literal(7))], shared=False)
        source.params.append({(KEY+1) & 0xFFFF: 55})
        with target.component.parameter_scope(scope):
            self.assertEqual(target.context().param(KEY & 0xFFFF), 55)
        self.assertFalse(source.component.parameter_scopes)

    def test_nested_providers_preserve_reverse_order_successful_null_and_null_barriers(self):
        component = runtime.Component(OWNER)
        instance = component.create(graph(), init=False)
        instance.params = [{13: 4}, None]
        with component.parameter_scope({13: 7}):
            with component.parameter_scope({13: None}):
                self.assertEqual(instance.context().parameter_result(13), (True, None))
            with component.parameter_scope(None):
                self.assertEqual(instance.context().parameter_result(13, initial=99), (False, 99))
            self.assertEqual(instance.context().param(13), 7)
        self.assertEqual(instance.context().param(13), 4)

    def test_failed_provider_writes_survive_when_all_providers_fail(self):
        world, _, _, target, _, scope = fixture([row(value={
            '$': controller_heroes.CLASS, 'm_controller': {'$': 'STU_B5A0CAF0'}})])
        world.set_controller_hero_context(OWNER, controller_heroes.Context(OWNER, True, True,
            lookup=lambda identifier: controller_heroes.Controller(identifier), catalog=lambda:(),
            available=lambda asset, controller: True, watch_environment=lambda *args: None))
        self.assertEqual(scope.lookup_guid(KEY, initial=expr.Asset(12)), (False, 0))
        with target.component.parameter_scope(scope):
            self.assertEqual(target.context().parameter_result(KEY & 0xFFFF, initial=99), (False, 0))
            self.assertEqual(target.context().param(KEY & 0xFFFF), 0)

    def test_legacy_delivery_uses_the_same_vm_stack_for_another_instance(self):
        _, source, _, target, _, _ = fixture()
        model = graph([node(0, 'LegacyEntry', m_outPlug=plug((1, 'm_inPlug'))), node(1, 'ReadOtherInstance')])
        delivery = target.component.create(model, init=False)
        seen = []
        with mock.patch.dict(runtime.HANDLERS,
                             ReadOtherInstance=lambda *args: seen.append(source.context().param(13)) or True):
            delivery.deliver(runtime.Event(0, 0, runtime.MESSAGE, node=model.node(0),
                                          message=(1, {13: 7}, 9)))
        self.assertEqual(seen, [7])
        self.assertFalse(target.component.parameter_scopes)

    def test_scope_is_removed_when_source_callback_retires_its_context(self):
        world, source, _, target, _, scope = fixture(shared=False)

        def retire(instance):
            world.message_contexts[OWNER].retire()
            return 7

        source.graph.node(0).fields['m_params'] = [row(value=literal(retire))]
        with self.assertRaises(runtime.UnsupportedScriptError), target.component.parameter_scope(scope):
            target.context().param(KEY & 0xFFFF)
        self.assertFalse(target.component.parameter_scopes)
        self.assertIsNotNone(target.component.fault)

    def test_different_manager_cannot_supply_a_selected_sender(self):
        world, source, _, target, _, scope = fixture(shared=False)
        previous = world.message_contexts[OTHER]
        previous.retire()
        replacement = message_state.Context(target.component, message_state.Host(notify=lambda state: None),
            policy=previous.policy, frame=previous.frame, priority=previous.priority)
        world.set_message_state_context(OTHER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            scope.lookup_guid(KEY)
        self.assertIsNone(source.component.fault)

    def test_key_scan_and_foreign_evaluation_share_the_receivers_budget(self):
        _, _, _, target, _, scope = fixture([row(KEY+n+1, literal(1)) for n in range(100)], shared=False)
        with self.assertRaises(runtime.ScriptBudgetError), target.component.operation():
            target.component._work_left = 10
            scope.lookup_guid(KEY)
        world, source, _, target, _, scope = fixture(shared=False)

        def expensive(instance):
            for _ in range(50):
                instance.component.consume_work('fixture foreign work')
            return 7

        source.graph.node(0).fields['m_params'] = [row(value=literal(expensive))]
        with self.assertRaises(runtime.ScriptBudgetError), target.component.operation():
            target.component._work_left = 10
            scope.lookup_guid(KEY)
        self.assertIsNotNone(source.component.fault)
        self.assertIsNotNone(target.component.fault)
        self.assertFalse(world.message_contexts)

    def test_passive_export_records_the_shared_stack_without_calling_its_getter(self):
        component = runtime.Component(OWNER, strict=True)
        instance = component.create(graph([node(0, 'STUStatescriptState', state=0),
                                           node(1, 'STUStatescriptState', state=1)]), init=False)
        scope = message_scope.ParamScope(instance.state(0), instance.state(1))
        component.parameter_scopes.append(scope)
        with mock.patch.object(message_scope, 'check', side_effect=AssertionError('getter called')):
            stored = export_component_vm(component)
        self.assertIn('parameter_scopes', dict(raw(stored)['component']))
        self.assertEqual(component.parameter_scopes, [scope])


if __name__ == '__main__':
    unittest.main()
