"""Native entity-rule ordering, service gates and real State lifecycle."""
import unittest
from unittest import mock

from ow174.game.script import entity_control as control
from ow174.game.script import expr, runtime, scene
from tests.script_fixtures import export_synthetic_component_vm, graph, node


def literal(value):
    return {"$": "ControlValue", "value": value}


class World(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get("$") == "ControlValue":
            return cfg['value']
        return super().native(cfg, instance)


class EntityControlTests(unittest.TestCase):
    def setup_state(self, *, strict=True, **fields):
        world = World()
        world.bind(0x800001AE)
        component = runtime.Component(0x800001AE, world=world, strict=strict)
        instance = component.create(graph([node(0, control.CLASS, state=0, **fields)]), init=False)
        instance.initialized = True
        state = instance.state(0)
        return world, component, instance, state

    def services(self, world, owner, **options):
        events = []
        context = control.Context(
            owner, False, permitted=lambda: events.append('permission') or True,
            within_distance=lambda state: events.append(('distance', state.index)) or False,
            write_entity=lambda value: events.append(('entity', value)),
            model_ready=lambda: True,
            write_model=lambda state, value, recursive: events.append(('model', value, recursive)),
            route=lambda state, kind: events.append(('route', kind)),
            extra_test=None,
        )
        for name, value in options.items():
            setattr(context, name, value)
        world.set_entity_control_context(owner.entity, context)
        return context, events

    def test_unknown_engine_context_stops_instead_of_accepting_a_passive_state(self):
        _, component, _, state = self.setup_state()
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertEqual(component.fault.diagnostic['feature'], 'entity control context')
        self.assertFalse(component.state_registries)

    def test_known_absent_context_still_refreshes_caches_before_return(self):
        world, component, _, state = self.setup_state(m_381879C1=literal(True))
        world.set_entity_control_context(component.entity, None)
        state.begin()
        self.assertTrue(state.active)
        self.assertEqual(state.control_radius, -1.0)
        self.assertFalse(state.control_inverse_a)
        self.assertTrue(state.control_inverse_b)
        self.assertEqual(state.control_generation, 1)
        self.assertEqual(component.state_registries[control.CLASS], [state])

    def test_authored120E_flags_and_cleanup_recompute_without_the_ended_rule(self):
        world, component, _, state = self.setup_state(
            m_381879C1=literal(True), m_7E42F868=literal(True), m_D82DB14B=literal(True),
        )
        _, events = self.services(world, component)
        state.begin()
        self.assertEqual(events, [('route', 6), ('distance', 0), ('entity', False), ('model', True, False)])
        events.clear()
        state.end(False)
        self.assertEqual(events, [('route', 6), ('entity', True), ('model', False, False)])
        self.assertFalse(component.state_registries[control.CLASS])
        self.assertEqual(state.control_generation, 2)

    def test_bypass_and_gate_do_not_skip_the_always_applied_100_field(self):
        for bypass, enabled in ((True, True), (False, False)):
            with self.subTest(bypass=bypass, enabled=enabled):
                world, component, _, state = self.setup_state(
                    m_72FD7742=literal(bypass), m_3C443404=literal(enabled),
                    m_7E42F868=literal(True), m_D82DB14B=literal(True),
                )
                _, events = self.services(world, component)
                state.begin()
                self.assertIn(('entity', True), events)
                self.assertIn(('model', True, False), events)

    def test_extra_query_can_restore_second_output_only_under_native_gates(self):
        world, component, _, state = self.setup_state(m_7E42F868=literal(True), m_DFD8E2EA=literal(True))
        context, events = self.services(world, component)
        context.extra_test = lambda: events.append('extra') or True
        state.begin()
        self.assertEqual(events[-2:], ['extra', ('entity', True)])
        events.clear()
        context.native_flag = True
        state.dependency_changed()
        self.assertNotIn('extra', events)
        self.assertIn('permission', events)
        self.assertIn(('entity', False), events)

    def test_missing_target_component_skips_consumers_after_route_and_permission(self):
        world, component, _, state = self.setup_state()
        _, events = self.services(world, component, component=None, native_flag=True)
        state.begin()
        self.assertEqual(events, [('route', 6), 'permission'])

    def test_permission_callback_does_not_replace_sampled_native_flag(self):
        world, component, _, state = self.setup_state(m_7E42F868=literal(True), m_DFD8E2EA=literal(True))
        context, events = self.services(world, component, native_flag=True)

        def permission():
            context.native_flag = False
            return True

        context.permitted = permission
        context.extra_test = lambda: events.append('extra') or True
        state.begin()
        self.assertFalse(context.native_flag)
        self.assertNotIn('extra', events)
        self.assertIn(('entity', False), events)

    def test_rule_callback_does_not_replace_sampled_extra_service(self):
        world, component, _, state = self.setup_state(m_7E42F868=literal(True), m_DFD8E2EA=literal(True))
        context, events = self.services(world, component)

        def distance(current):
            context.extra_test = None
            return False

        context.within_distance = distance
        context.extra_test = lambda: events.append('extra') or True
        state.begin()
        self.assertIsNone(context.extra_test)
        self.assertEqual(events[-2:], ['extra', ('entity', True)])

    def test_model_availability_and_change_gate_preserve_previous_value(self):
        world, component, _, state = self.setup_state(m_D82DB14B=literal(True))
        context, events = self.services(world, component, model_ready=lambda: False)
        state.begin()
        self.assertFalse(state.control_previous)
        context.model_ready = lambda: True
        state.dependency_changed()
        self.assertTrue(state.control_previous)
        self.assertEqual(events.count(('model', True, False)), 1)
        state.dependency_changed()
        self.assertEqual(events.count(('model', True, False)), 1)

    def test_dynamic_change_rechecks_and_radius_requests_ticks(self):
        dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}
        world, component, instance, state = self.setup_state(m_D82DB14B=dynamic, m_069FD7A5=literal(0.5))
        _, events = self.services(world, component)
        state.begin()
        self.assertIn(state, instance.ticking)
        self.assertIn(instance.find_var(0, 7), state.watched)
        instance.write((0, 7), True)
        component.run_now()
        self.assertIn(('model', True, False), events)
        state.end(False)
        self.assertNotIn(state, instance.ticking)
        self.assertFalse(state.watched)

    def test_nonzero_generation_skips255_and_wraps_byte(self):
        world, component, _, state = self.setup_state()
        world.set_entity_control_context(component.entity, None)
        for old, expected in ((253, 254), (254, 0), (255, 0)):
            state.control_generation = old
            state.dependency_changed()
            self.assertEqual(state.control_generation, expected)

    def test_failed_scalar_configs_keep_native_defaults(self):
        world, component, _, state = self.setup_state(
            m_069FD7A5=literal(expr.EVALUATION_FAILED),
            m_381879C1=literal(expr.EVALUATION_FAILED),
        )
        world.set_entity_control_context(component.entity, None)
        state.begin()
        self.assertEqual(state.control_radius, -1.0)
        self.assertTrue(state.control_inverse_a)

    def test_target_iterator_filters_invalid_values_and_keeps_order_duplicates(self):
        values = (expr.Entity(0), expr.Entity(0x800001A8), expr.Asset(12),
                  expr.Entity(0xFFFFFFFF), (expr.Entity(0x800001AE), expr.Entity(0x800001A8)))
        world, component, _, state = self.setup_state(m_C3E68133=literal(values))
        world.set_entity_control_context(component.entity, None)
        state.begin()
        self.assertEqual(state.control_targets, (0x800001A8, 0x800001AE, 0x800001A8))

    def test_unknown_required_consumer_faults_before_model_side_effect(self):
        world, component, _, state = self.setup_state()
        _, events = self.services(world, component, write_entity=NotImplemented)
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertEqual(component.fault.diagnostic['feature'], 'entity control entity consumer')
        self.assertFalse(any(isinstance(item, tuple) and item[0] == 'model' for item in events))
        self.assertNotIn(component.entity, world.control_contexts)

    def test_registry_mutation_during_rule_evaluation_is_explicitly_gated(self):
        world, component, _, state = self.setup_state()
        self.services(world, component, within_distance=lambda current:
                      component.state_registries[control.CLASS].clear() or False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertEqual(component.fault.diagnostic['feature'], 'entity control registry mutation')

    def test_context_binding_retires_with_entity_and_rejects_another_world(self):
        world, component, _, _ = self.setup_state()
        context, _ = self.services(world, component)
        with self.assertRaises(ValueError):
            world.set_entity_control_context(component.entity, control.Context(None, False))
        world.unbind(component.entity)
        self.assertIs(world.entity_control_context(component.entity), NotImplemented)
        world.bind(component.entity)
        with self.assertRaises(ValueError):
            world.set_entity_control_context(component.entity,
                                             control.Context(runtime.Component(12), False))
        world.set_entity_control_context(component.entity, context)

    def test_callback_retiring_owner_stops_before_any_entity_or_model_write(self):
        world, component, instance, state = self.setup_state()
        _, events = self.services(world, component,
                                  route=lambda *_: component.instances.pop(instance.id))
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertEqual(component.fault.diagnostic['feature'],
                         'entity control owner retired during callback')
        self.assertFalse(events)

    def test_unknown_optional_services_are_not_treated_as_known_absence(self):
        for field, feature in (('route', 'entity control route'),
                               ('extra_test', 'entity control extra query')):
            with self.subTest(field=field):
                world, component, _, state = self.setup_state(m_DFD8E2EA=literal(True))
                context, _ = self.services(world, component)
                setattr(context, field, NotImplemented)
                with self.assertRaises(runtime.UnsupportedScriptError):
                    state.begin()
                self.assertEqual(component.fault.diagnostic['feature'], feature)

    def test_context_getter_retiring_owner_is_gated_even_when_it_returns_none(self):
        world, component, instance, state = self.setup_state()

        def context(entity):
            component.instances.pop(instance.id)
            return None

        world.entity_control_context = context
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertEqual(component.fault.diagnostic['feature'],
                         'entity control owner retired during callback')

    def test_target_entity_and_component_retirement_revoke_dependent_contexts(self):
        world, source, _, _ = self.setup_state()
        target_id = 0x800001A8
        target = runtime.Component(target_id, world=world, strict=True)
        context = control.Context(target, False)
        with self.assertRaises(ValueError):
            world.set_entity_control_context(source.entity, context)
        world.bind(target_id)
        world.set_entity_control_context(source.entity, context)
        world.unbind(target_id)
        self.assertIs(world.entity_control_context(source.entity), NotImplemented)
        world.bind(target_id)
        target = runtime.Component(target_id, world=world, strict=True)
        world.set_entity_control_context(source.entity, control.Context(target, False))
        world.script_failed(target)
        self.assertIs(world.entity_control_context(source.entity), NotImplemented)

    def test_permissive_body_policy_stays_passive(self):
        _, component, _, state = self.setup_state(strict=False)
        state.begin()
        self.assertTrue(state.active)
        self.assertFalse(component.state_registries)
        self.assertEqual(state.payload(), {})
        self.assertEqual(state.control_generation, 0)

    def test_snapshot_observes_cached_fields_without_evaluating_or_writing(self):
        component = runtime.Component(0x800001AE, strict=True)
        instance = component.create(graph([node(0, control.CLASS, state=0)]), init=False)
        instance.initialized = True
        state = instance.state(0)
        with mock.patch.object(runtime.World, 'entity_control_context', return_value=None):
            state.begin()
        with mock.patch.object(type(state), 'dependency_changed', side_effect=AssertionError('executed')):
            document = export_synthetic_component_vm(component)
        self.assertIn('control_generation', str(document))


if __name__ == '__main__':
    unittest.main()
