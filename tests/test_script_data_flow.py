"""Data-flow registration, typed providers, dependency changes and owner lifetimes."""

import unittest
from unittest import mock

from ow174.game.script import data_flow, expr, nodes, runtime, scene
from tests.test_script_runtime_budget import graph, node

OWNER = 0x800005DC


def literal(value):
    return {'$': 'STUConfigVarLiteral', 'value': value}


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'STUConfigVarLiteral':
            return cfg['value']
        return super().native(cfg, instance)


def setup_state(*, strict=True, **fields):
    world = ValueWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=strict)
    instance = component.create(graph([node(0, data_flow.CLASS, state=0, **fields)]), init=False)
    instance.initialized = True
    state = instance.state(0)
    events = []
    context = data_flow.Context(OWNER, True, True,
        register=lambda state: events.append(
            ('register', state.mapping, state.mapping_self, state.mapping_target)),
        unregister=lambda state: events.append(
            ('unregister', state.mapping, state.mapping_self, state.mapping_target)),
        special_changed=lambda delta: events.append(('special', delta)),
        position=lambda entity: events.append(('position', entity)) or expr.Vec3(4, 5, 6))
    world.set_data_flow_context(OWNER, context)
    return world, component, instance, state, context, events


class DataFlowTests(unittest.TestCase):
    def test_unknown_context_faults_instead_of_accepting_a_passive_mapping(self):
        world, component, _, state, _, _ = setup_state()
        world.data_flow_contexts.clear()
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            state.begin()
        self.assertEqual(error.exception.diagnostic['feature'], 'data-flow context')
        self.assertIs(component.fault, error.exception)
        self.assertFalse(state.active)

    def test_unknown_consumers_remain_strict(self):
        _, component, _, state, context, _ = setup_state(m_5A6D4FBD=literal(expr.Asset(17)))
        context.register = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            state.begin()
        self.assertEqual(error.exception.diagnostic['feature'], 'data-flow register')
        self.assertIs(component.fault, error.exception)
        self.assertEqual(state.mapping, 17)

    def test_begin_and_cleanup_register_then_remove_the_same_provider(self):
        _, _, instance, state, _, events = setup_state(
            m_5A6D4FBD={'$': 'STUConfigVarDataFlow', 'm_5A6D4FBD': '0x11'})
        state.begin()
        self.assertIsInstance(state, nodes.DataFlowMapping)
        self.assertEqual(events, [('register', 17, False, None)])
        self.assertEqual((state.mapping, state.mapping_self, state.mapping_target), (17, True, None))
        self.assertNotIn(state, instance.ticking)
        state.end(False)
        self.assertEqual(events[-1], ('unregister', 17, True, None))
        self.assertEqual(state.mapping, 0)

    def test_dependency_change_removes_old_before_publishing_new_guid(self):
        _, _, instance, state, _, events = setup_state(
            m_5A6D4FBD={'$': 'STUConfigVarDynamic', 'm_identifier': 99})
        variable = instance.find_var(0, 99)
        variable.base = expr.Asset(17)
        state.begin()
        self.assertIn(state, variable.watchers)
        events.clear()
        instance.write((0, 99), expr.Asset(23))
        instance.component.run_now()
        self.assertEqual([row[:2] for row in events], [('unregister', 17), ('register', 23)])
        state.end(False)
        self.assertNotIn(state, variable.watchers)

    def test_unchanged_guid_still_rebuilds_target_policy_without_reregistering(self):
        _, _, instance, state, _, events = setup_state(m_5A6D4FBD=literal(expr.Asset(17)),
            m_entity={'$': 'STU_328656A2', 'm_609E2FBF': literal(expr.Handle(1, 2, 3))})
        state.begin()
        events.clear()
        def evaluate(cfg, *args, **kwargs):
            return expr.Asset(17) if cfg is state.node.field('m_5A6D4FBD') else expr.Handle(1, 4, 5)

        instance.evaluate = evaluate
        state.dependency_changed()
        self.assertEqual(events, [])
        self.assertEqual((state.mapping_self, state.mapping_target), (False, expr.Handle(1, 4, 5)))

    def test_default_owner_global_and_explicit_target_selectors_remain_distinct(self):
        for selector, wanted in [(None, (True, None)), ({'$': 'STU_02C0109E'}, (True, None)),
                                 ({'$': 'STU_469A9B36'}, (False, None)),
                                 ({'$': 'STU_328656A2', 'm_609E2FBF': literal(expr.Handle(1, 2, 3))},
                                  (False, expr.Handle(1, 2, 3)))]:
            with self.subTest(selector=selector):
                _, _, _, state, _, _ = setup_state(m_entity=selector)
                state.begin()
                self.assertEqual((state.mapping_self, state.mapping_target), wanted)

    def test_target_needs_handle_tag_and_unknown_selector_is_diagnostic(self):
        _, _, _, state, _, _ = setup_state(m_entity={'$': 'STU_328656A2'})
        state.begin()
        self.assertEqual((state.mapping_self, state.mapping_target), (False, None))
        _, _, _, state, _, _ = setup_state(
            m_entity={'$': 'STU_328656A2', 'm_609E2FBF': literal(expr.Entity(27))})
        state.begin()
        self.assertIsNone(state.mapping_target)
        _, _, _, state, _, _ = setup_state(m_entity={'$': 'STU_Unknown'})
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()

    def test_only_assets_and_first_array_elements_supply_mapping_guids(self):
        for value, wanted in [(None, 0), (17, 0), (expr.Entity(17), 0),
                              (expr.Asset(17), 17), ((expr.Asset(23), expr.Asset(17)), 23),
                              ((None, expr.Asset(17)), 0), ((), 0)]:
            with self.subTest(value=value):
                _, _, _, state, _, _ = setup_state(m_5A6D4FBD=literal(value))
                state.begin()
                self.assertEqual(state.mapping, wanted)

    def test_begin_clears_cached_guid_before_refresh(self):
        _, _, _, state, _, events = setup_state(m_5A6D4FBD=literal(expr.Asset(17)))
        state.mapping = 23
        state.begin()
        self.assertEqual([row[:2] for row in events], [('register', 17)])

    def test_cleanup_missing_component_preserves_cached_guid(self):
        _, _, _, state, context, events = setup_state(m_5A6D4FBD=literal(expr.Asset(17)))
        state.begin()
        context.component_present = False
        events.clear()
        state.end(False)
        self.assertEqual(events, [])
        self.assertEqual(state.mapping, 17)

    def test_special_counter_uses_low32_guid_and_balances_after_registration(self):
        _, _, _, state, _, events = setup_state(m_5A6D4FBD=literal(expr.Asset(0x048000010000006B)))
        state.begin()
        state.end(False)
        self.assertEqual([row[0] for row in events], ['register', 'special', 'unregister', 'special'])
        self.assertEqual([row for row in events if row[0] == 'special'], [('special', 1), ('special', -1)])

    def test_provider_namespace_failure_preserves_destination_and_skips_answer(self):
        _, _, instance, state, context, _ = setup_state(m_answer=literal(True))
        context.namespace_present = False
        with mock.patch.object(instance, 'evaluate', side_effect=AssertionError('must not evaluate')):
            for kind in ('entity', 'asset', 'float', 'vector'):
                self.assertEqual(state.answer(kind), (4, None))

    def test_provider_answer_kinds_preserve_their_native_casts(self):
        for value, expected in [(True, (0, 0, 1.0, expr.Vec3(0, 0, 0))),
                                (expr.Asset(19), (0, 19, 0.0, expr.Vec3(0, 0, 0))),
                                (expr.Entity(23), (23, 0, 0.0, expr.Vec3(0, 0, 0))),
                                (expr.Vec3(1, 2, 3), (0, 0, 0.0, expr.Vec3(1, 2, 3)))]:
            _, _, _, state, _, _ = setup_state(m_answer=literal((value,)))
            actual = tuple(state.answer(kind)[1] for kind in ('entity', 'asset', 'float', 'vector'))
            self.assertEqual(actual, expected)

    def test_provider_queries_do_not_subscribe_to_answer_changes(self):
        _, _, instance, state, _, _ = setup_state(m_answer={'$': 'STUConfigVarDynamic', 'm_identifier': 99})
        instance.find_var(0, 99).base = True
        state.begin()
        self.assertEqual(state.answer('float'), (1, 1.0))
        self.assertEqual(instance.find_var(0, 99).watchers, {})

    def test_vector_provider_resolves_direct_entity_position_but_not_array_entity(self):
        _, _, _, state, context, events = setup_state(m_answer=literal(expr.Entity(23)))
        self.assertEqual(state.answer('vector'), (1, expr.Vec3(4, 5, 6)))
        self.assertEqual(events, [('position', 23)])
        context.position = lambda entity: None
        self.assertEqual(state.answer('vector'), (1, expr.Vec3(0, 0, 0)))
        context.position = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.answer('vector')

    def test_retirement_during_registration_prevents_later_cache_writes(self):
        world, component, _, state, context, _ = setup_state(m_5A6D4FBD=literal(expr.Asset(17)))
        context.register = lambda state: world.unbind(OWNER)
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertTrue(context.retired)
        self.assertIsNotNone(component.fault)
        self.assertEqual((state.mapping_self, state.mapping_target), (False, None))

    def test_scene_rejects_replacement_and_unbind_retires_context_before_id_reuse(self):
        world, _, _, state, context, _ = setup_state()
        with self.assertRaises(ValueError):
            world.set_data_flow_context(OWNER, data_flow.Context(OWNER, True, True))
        world.unbind(OWNER)
        world.bind(OWNER)
        self.assertTrue(context.retired)
        self.assertIs(world.data_flow_context(OWNER), NotImplemented)
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.answer('asset')

    def test_typed_data_flow_literal_including_zero_and_unknown_key_availability(self):
        for value in (0, 0x048000000000061D):
            _, _, instance, _, _, _ = setup_state()
            cfg = {'$': 'STUConfigVarDataFlow', 'm_5A6D4FBD': f'0x{value:016X}'}
            self.assertEqual(instance.evaluate(cfg), expr.Asset(value))
        _, _, instance, _, _, _ = setup_state()
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate({'$': 'STUConfigVarDataFlow', 'm_resourceKey': '0x0D80000000000019',
                               'm_5A6D4FBD': '0x048000000000061D'})

    def test_passive_policy_does_not_activate_native_mapping_services(self):
        world, _, _, state, _, events = setup_state(strict=False)
        world.data_flow_contexts.clear()
        state.begin()
        state.dependency_changed()
        state.end(False)
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
