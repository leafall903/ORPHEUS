"""Explicit auxiliary-effect routing and callbacks preserve strict VM boundaries."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import effect_allocation, effect_routing, expr, runtime, scopes
from tests.test_script_effect_parameters import ValuesWorld, dynamic
from tests.test_script_play_resource import EMPTY, array, integer
from tests.test_script_runtime_budget import graph, node, plug

OWNER, OTHER, THIRD = 0xA0000101, 0xA0000102, 0xA0000103
CLASS = 'STU_582E0D5F'


def setup(*, scope='registered', context='present', target='present', owner=OWNER,
          enabled=False, fallback=True, fields=None, values=None, strict=True):
    world = ValuesWorld(values or {})
    for entity in (OWNER, OTHER, THIRD):
        world.bind(entity)
    registry = scopes.Registry()
    if scope != 'unknown':
        world.set_subgraph_scopes(OWNER, registry if scope == 'registered' else None)
    local, remote, global_list = (effect_allocation.EffectList() for _ in range(3))
    engine, target_context = object(), object()
    ctx = effect_routing.Context(owner, engine, enabled, global_list if fallback else NotImplemented)
    if context != 'unknown':
        world.set_effect_context(OWNER, ctx if context == 'present' else None)
    if target != 'unknown':
        world.set_effect_target(OWNER, effect_routing.Target(OWNER, target_context, local)
                                if target == 'present' else None)
    world.set_effect_target(OTHER, effect_routing.Target(OTHER, object(), remote))
    component = runtime.Component(OWNER, world=world, strict=strict)
    instance = component.create(graph([node(0, CLASS, state=0, **(fields or {}))]), init=False)
    instance.initialized = True
    state = instance.state(0)
    return component, instance, state, registry, ctx, local, remote, global_list


class EffectStateTests(unittest.TestCase):
    def test_cold_registration_and_ordinary_begin_end_do_not_invent_interface_calls(self):
        component, _, state, registry, _, local, _, _ = setup(context='unknown')
        self.assertEqual(registry.states, [state])
        self.assertFalse(state.active)
        state.request(runtime.BEGIN)
        component.run_now()
        state.end(True)
        self.assertFalse(local.entries)
        self.assertIsNone(component.fault)
        self.assertIsNone(state.effect)

    def test_unknown_host_and_context_are_distinct_strict_gates(self):
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            setup(scope='unknown')
        self.assertEqual(error.exception.diagnostic['feature'], 'subgraph scope registration')
        component, _, state, registry, _, _, _, _ = setup(context='unknown')
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.enter(state)
        self.assertEqual(error.exception.diagnostic['feature'], 'auxiliary effect context')
        self.assertIsNotNone(component.fault)
        self.assertFalse(registry.states)

    def test_known_absent_context_skips_unknown_target_config_before_evaluation(self):
        component, _, state, registry, _, local, _, _ = setup(
            context='absent', fields={'m_481B15D1': {'$': 'MustNotEvaluate'}},
        )
        registry.enter(state)
        self.assertIsNone(state.effect)
        self.assertFalse(local.entries)
        self.assertIsNone(component.fault)

    def test_valid_authored_target_precedes_native_owner_and_supplies_its_context(self):
        _, _, state, registry, ctx, local, remote, _ = setup(
            fields={'m_481B15D1': {'$': 'Target'}},
            values={'Target': (expr.Entity(OTHER), expr.Entity(OWNER))},
        )
        registry.enter(state)
        self.assertEqual(state.effect.target, OTHER)
        self.assertIs(state.effect_list, remote)
        self.assertIsNot(state.effect.context, ctx.engine)
        self.assertFalse(local.entries)
        self.assertFalse(state.active)

    def test_missing_failed_null_or_invalid_target_falls_back_to_explicit_native_owner(self):
        for config, values in ((None, {}), (EMPTY, {}), (array(EMPTY, integer(2)), {}),
                               ({'$': 'Target'}, {'Target': None}),
                               ({'$': 'Target'}, {'Target': expr.Entity(0)}),
                               ({'$': 'Target'}, {'Target': expr.Entity(0xBFFFFFFF)}),
                               ({'$': 'Target'}, {'Target': 123})):
            with self.subTest(config=config, values=values):
                _, _, state, registry, _, local, remote, _ = setup(
                    owner=OTHER, fields={'m_481B15D1': config}, values=values,
                )
                registry.enter(state)
                self.assertEqual(state.effect.target, OTHER)
                self.assertIs(state.effect_list, remote)
                self.assertFalse(local.entries)

    def test_known_component_absence_uses_instance_bit8_then_global_fallback(self):
        for flags in (8, 0):
            _, instance, state, registry, ctx, local, _, global_list = setup(
                target='absent', enabled=True, fields={'m_6B30E42C': integer(1)},
            )
            ctx.set_flags(instance, flags)
            registry.enter(state)
            self.assertFalse(local.entries)
            if flags:
                self.assertIsNone(state.effect)
                self.assertFalse(global_list.entries)
            else:
                self.assertEqual(state.effect.target, 0)
                self.assertIs(state.effect.context, ctx.engine)
                self.assertIs(state.effect_list, global_list)
                registry.exit(state)
                self.assertFalse(global_list.entries[0].live)

    def test_unknown_targets_flags_fallback_and_capability_do_not_manufacture_effects(self):
        cases = [({'target': 'unknown'}, 'auxiliary effect target'),
                 ({'target': 'absent'}, 'auxiliary effect instance flags'),
                 ({'target': 'absent', 'fallback': False}, 'auxiliary effect fallback'),
                 ({'enabled': NotImplemented}, 'auxiliary effect capability')]
        for options, feature in cases:
            component, instance, state, registry, ctx, local, _, global_list = setup(**options)
            if options.get('fallback') is False:
                ctx.set_flags(instance, 0)
            with self.assertRaises(runtime.UnsupportedScriptError) as error:
                registry.enter(state)
            self.assertEqual(error.exception.diagnostic['feature'], feature)
            self.assertFalse(local.entries)
            self.assertFalse(global_list.entries)
            self.assertIsNotNone(component.fault)

    def test_valid_but_unbound_target_is_not_treated_as_known_component_absence(self):
        _, _, state, registry, _, _, _, _ = setup(
            fields={'m_481B15D1': {'$': 'Target'}}, values={'Target': expr.Entity(0xA0000800)},
        )
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.enter(state)
        self.assertEqual(error.exception.diagnostic['feature'], 'auxiliary effect target')

    def test_refresh_and_matching_observer_release_keep_native_parameter_and_identity_rules(self):
        _, instance, state, registry, _, local, _, _ = setup(fields={'m_F5C94645': dynamic(7)})
        instance.write((0, 7), 0.25)
        registry.enter(state)
        first = state.effect
        instance.write((0, 7), 0.75)
        state.refresh_effect()
        self.assertEqual(first.parameters.values[0xC4], 0.75)
        registry.enter(state)
        current = state.effect
        state.effect_lost(first)
        self.assertIs(state.effect, current)
        self.assertTrue(local.entries[0].live)
        state.effect_lost(current)
        self.assertIsNone(state.effect)
        self.assertIsNone(current.observer)
        self.assertFalse(local.entries[1].live)
        self.assertIs(first.observer, state)
        self.assertEqual(len(state.owned_effects), 1)

    def test_normal_state_end_keeps_effect_until_explicit_exit(self):
        component, _, state, registry, _, local, _, _ = setup()
        state.request(runtime.BEGIN)
        component.run_now()
        registry.enter(state)
        state.end(False)
        self.assertTrue(local.entries[0].live)
        self.assertIsNotNone(state.effect)
        registry.exit(state)
        self.assertFalse(local.entries[0].live)
        self.assertIsNone(local.entries[0].effect.observer)

    def test_retirement_updates_blend_parameters_and_retires_instead_of_erasing(self):
        _, instance, state, registry, _, local, _, _ = setup(fields={'m_63FFE8DF': dynamic(7)})
        instance.write((0, 7), 0.25)
        registry.enter(state)
        effect = state.effect
        instance.write((0, 7), 0.875)
        registry.exit(state)
        self.assertEqual(effect.parameters.values[0xC8], 0.875)
        self.assertEqual(len(local.entries), 1)
        self.assertFalse(local.entries[0].live)
        self.assertFalse(state.owned_effects)

    def test_orientation_cleanup_remains_strict_without_native_consumer_evidence(self):
        component, _, state, registry, _, local, _, _ = setup(
            enabled=True, fields={'m_6B30E42C': integer(1)},
        )
        registry.enter(state)
        effect = state.effect
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.exit(state)
        self.assertEqual(error.exception.diagnostic['feature'], 'auxiliary effect orientation cleanup')
        self.assertIsNotNone(component.fault)
        self.assertFalse(local.entries[0].live)
        self.assertIsNone(effect.observer)
        self.assertIsNone(state.effect)

    def test_failed_initialization_discards_effects_bound_before_config_evaluation(self):
        component, _, state, registry, _, local, _, _ = setup(fields={'m_1A76C130': {'$': 'UnknownInitial'}})
        with self.assertRaises(runtime.UnsupportedScriptError):
            registry.enter(state)
        self.assertIsNotNone(component.fault)
        self.assertEqual(len(local.entries), 1)
        self.assertFalse(local.entries[0].live)
        self.assertIsNone(local.entries[0].effect.observer)
        self.assertIsNone(state.effect)
        self.assertFalse(state.owned_effects)

    def test_failure_does_not_retire_another_components_effect_in_the_same_target_list(self):
        component, _, first, registry, _, local, _, _ = setup()
        definition = graph([node(0, CLASS, state=0)])
        other_component = runtime.Component(THIRD, world=component.world, strict=True)
        other_registry = scopes.Registry()
        component.world.set_subgraph_scopes(THIRD, other_registry)
        component.world.set_effect_context(THIRD, effect_routing.Context(OWNER, object(), False))
        other_instance = other_component.create(definition, init=False)
        second = other_instance.state(0)
        registry.enter(first)
        other_registry.enter(second)
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(first.instance, 'missing later service', 'Missing')
        self.assertFalse(local.entries[0].live)
        self.assertTrue(local.entries[1].live)
        self.assertIs(local.entries[1].effect.observer, second)

    def test_instance_destruction_retires_current_and_superseded_effects_without_authored_plugs(self):
        component, instance, state, registry, _, local, _, _ = setup()
        registry.enter(state)
        registry.enter(state)
        effects = [entry.effect for entry in local.entries]
        with mock.patch.object(instance, 'follow', side_effect=AssertionError('authored teardown callback')):
            component.destroy(instance)
        self.assertFalse(registry.states)
        self.assertFalse(state.owned_effects)
        self.assertTrue(all(not entry.live for entry in local.entries))
        self.assertTrue(all(effect.observer is None for effect in effects))
        with self.assertRaises(ValueError):
            state.scope_enter()

    def test_entity_retirement_discards_only_affected_effects_and_reuse_needs_fresh_declarations(self):
        component, _, state, registry, _, _, remote, _ = setup(
            fields={'m_481B15D1': {'$': 'Target'}}, values={'Target': expr.Entity(OTHER)},
        )
        registry.enter(state)
        effect = state.effect
        component.world.unbind(OTHER)
        self.assertFalse(remote.entries[0].live)
        self.assertIsNone(effect.observer)
        self.assertIsNone(state.effect)
        component.world.bind(OTHER)
        self.assertIs(component.world.auxiliary_effect_target(OTHER), NotImplemented)
        with self.assertRaises(runtime.UnsupportedScriptError):
            registry.enter(state)

    def test_native_instance_flags_do_not_transfer_to_a_reused_numeric_instance_id(self):
        component, first, state, registry, ctx, _, _, _ = setup(target='absent')
        ctx.set_flags(first, 8)
        registry.enter(state)
        component.destroy(first)
        replacement = component.create(graph([node(0, CLASS, state=0)]), instance_id=first.id, init=False)
        fresh = replacement.state(0)
        self.assertIs(ctx.flags(replacement), NotImplemented)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.enter(fresh)
        self.assertEqual(error.exception.diagnostic['feature'], 'auxiliary effect instance flags')

    def test_unknown_callback_evaluation_shares_budgets_and_cleans_bound_effects(self):
        fields = {'m_F5C94645': dynamic(7), 'm_onBeginPlug': plug((1, 'm_inPlug'))}
        _component, instance, state, registry, _, local, _, _ = setup(fields=fields)
        instance.write((0, 7), 0.25)
        registry.enter(state)
        with mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 0), self.assertRaises(runtime.ScriptBudgetError):
            state.refresh_effect()
        self.assertIsNone(state.effect)
        self.assertFalse(local.entries[0].live)

    def test_passive_body_storage_includes_empty_effect_fields_without_running_services(self):
        component, instance, state, _, _, _, _, _ = setup(strict=False, scope='unknown', context='unknown')
        # SceneWorld itself stays outside the storage whitelist; passive state
        # fields can still be observed with the supported legacy World.
        component.world = runtime.World()
        with mock.patch.object(type(state), 'scope_enter', side_effect=AssertionError('callback')):
            export_component_vm(component)
        self.assertIsNone(state.effect)
        self.assertEqual(list(instance.states), [0])

    def test_service_lifetimes_reject_replacement_and_keep_absence_distinct(self):
        component, _, _, _, ctx, local, _, _ = setup()
        world = component.world
        world.set_effect_context(OWNER, ctx)
        with self.assertRaises(ValueError):
            world.set_effect_context(OWNER, None)
        with self.assertRaises(ValueError):
            world.set_effect_target(OWNER, None)
        for flags in (-1, 0x100000000, True):
            with self.assertRaises(ValueError):
                ctx.set_flags(component.instances[1], flags)
        with self.assertRaises(ValueError):
            effect_routing.Target(0, object(), local)
        with self.assertRaises(ValueError):
            effect_routing.Context(OWNER, NotImplemented, False)


if __name__ == '__main__':
    unittest.main()
