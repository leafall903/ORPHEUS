"""Native team selection, two lookup passes, dependency routing and component lifetimes."""

import unittest
from unittest import mock

from ow174.game.script import entity_team as team
from ow174.game.script import expr, play, runtime, scene
from tests.test_script_runtime_budget import graph, node

OWNER, OTHER = 0xA0000100, 0xA0000101
TEAM0 = 1 << 23


def literal(value):
    return {'$': 'Literal', 'value': value}


def config(selector=None):
    return {'$': team.CLASS, 'm_target': selector or {'$': 'STU_B5A0CAF0'}}


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'Literal':
            return cfg['value']
        return super().native(cfg, instance)


def setup():
    world = ValueWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    instance = component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
    instance.initialized = True
    state = instance.state(0)
    state.begin()
    target = team.Target(OWNER, team.FilterBits(TEAM0))
    events = []
    context = team.Context(OWNER, True, True, True,
        lookup=lambda identifier: events.append(('lookup', identifier)) or (
            target if identifier == OWNER else None),
        watch_filter=lambda watcher, selected: events.append(('filter', watcher, selected)),
        watch_environment=lambda watcher, kind: events.append(('environment', watcher, kind)))
    world.set_entity_team_context(OWNER, context)
    return world, component, instance, state, context, target, events


class EntityTeamTests(unittest.TestCase):
    def test_all_five_team_bits_and_multiteam_priority_ignore_unrelated_bits(self):
        for mask in range(32):
            expected = next((index for index in range(5) if mask & (1 << index)), -1)
            for unrelated in (0, 0x00040001, 1 << 28, 0xFFFFFFFF00000000):
                self.assertEqual(team.index_from_filters((mask << 23) | unrelated), expected)

    def test_unknown_service_is_strict_and_permissive_compatibility_remains_unknown(self):
        for strict in (True, False):
            world = ValueWorld()
            world.bind(OWNER)
            component = runtime.Component(OWNER, world=world, strict=strict)
            instance = component.create(graph(), init=False)
            if strict:
                with self.assertRaises(runtime.UnsupportedScriptError) as error:
                    instance.evaluate(config())
                self.assertEqual(error.exception.diagnostic['feature'], 'world query')
            else:
                self.assertIsNone(instance.evaluate(config()))

    def test_missing_context_preserves_output_and_skips_target_evaluation(self):
        _, _, instance, state, context, _, events = setup()
        context.context_present = False
        previous = expr.Asset(17)
        self.assertIs(instance.evaluate(config({'$': 'Unknown'}), state, initial=previous), previous)
        self.assertEqual(events, [])

    def test_missing_failed_nonentity_and_invalid_target_write_integer_minus_one(self):
        selectors = [literal(value) for value in (
            None, True, OWNER, expr.Asset(OWNER), (), (None, expr.Entity(OWNER)),
            expr.Entity(0), expr.Entity(0x1FFFFFFF), expr.Entity(0xFFFFFFFF))]
        selectors += [{'$': 'STU_7BD2CA19', 'm_resourceKey': None},
                      {'$': 'STU_06610EBC', 'm_configVars': []}]
        for selector in selectors:
            _, _, instance, state, context, _, events = setup()
            context.namespace_present = False  # this path does not obtain a VM
            value = instance.evaluate(config(selector), state, initial=expr.Asset(17))
            self.assertEqual(value, -1)
            self.assertIs(type(value), int)
            self.assertEqual(events, [])
        _, _, instance, _, _, _, events = setup()
        self.assertEqual(instance.evaluate({'$': team.CLASS}), -1)
        self.assertEqual(events, [])

    def test_namespace_absence_for_a_valid_identity_is_not_a_missing_team(self):
        _, _, instance, _, context, _, events = setup()
        context.namespace_present = False
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())
        self.assertEqual(events, [])

    def test_two_lookups_keep_full_wrapped_identity_and_first_array_conversion(self):
        for value in (expr.Entity(OWNER+(1 << 32)), ((expr.Entity(OWNER),),)):
            _, _, instance, state, _, target, events = setup()
            self.assertEqual(instance.evaluate(config(literal(value)), state), 0)
            self.assertEqual(events, [('lookup', OWNER), ('filter', state, target), ('lookup', OWNER)])

    def test_known_component_absence_is_minus_one_without_an_environment_watch(self):
        _, _, instance, state, context, _, events = setup()
        context.lookup = lambda identifier: (
            events.append(('lookup', identifier)) or team.Target(identifier, None))
        self.assertEqual(instance.evaluate(config(), state), -1)
        self.assertEqual(events, [('lookup', OWNER), ('lookup', OWNER)])

    def test_missing_object_uses_active_or_fallback_hub_then_repeats_lookup(self):
        for environment in (True, False):
            _, _, instance, state, context, _, events = setup()
            context.environment_present = environment
            context.lookup = lambda identifier, events=events: events.append(('lookup', identifier))
            self.assertEqual(instance.evaluate(config(), state), -1)
            self.assertEqual(events, [('lookup', OWNER), ('environment', state, 0 if environment else 46),
                                      ('lookup', OWNER)])

    def test_existing_object_can_resolve_through_a_namespace_with_no_active_environment(self):
        _, _, instance, state, context, target, events = setup()
        context.environment_present = False
        self.assertEqual(instance.evaluate(config(), state), 0)
        self.assertEqual(events, [('lookup', OWNER), ('filter', state, target), ('lookup', OWNER)])

    def test_second_lookup_is_not_replaced_with_cached_first_presence_or_component(self):
        for first, second, expected, watched in (
            (None, team.Target(OWNER, team.FilterBits(TEAM0 << 3)), 3, 'environment'),
            (team.Target(OWNER, team.FilterBits(TEAM0)), None, -1, 'filter'),
            (team.Target(OWNER, None), team.Target(OWNER, team.FilterBits(TEAM0 << 4)), 4, None),
        ):
            _, _, instance, state, context, _, events = setup()
            targets = iter((first, second))
            context.lookup = lambda identifier, targets=targets, events=events: (
                events.append(('lookup', identifier)) or next(targets))
            self.assertEqual(instance.evaluate(config(), state), expected)
            self.assertEqual([row[0] for row in events], ['lookup', watched, 'lookup'] if watched else
                             ['lookup', 'lookup'])

    def test_filter_subscription_precedes_the_fresh_value_read(self):
        _, _, instance, state, context, target, events = setup()

        def changed(watcher, selected):
            events.append(('filter', watcher, selected))
            target.filters.bits = TEAM0 << 2

        context.watch_filter = changed
        self.assertEqual(instance.evaluate(config(), state), 2)
        self.assertEqual(events[-1], ('lookup', OWNER))

    def test_selector_is_evaluated_once_and_its_variable_dependency_is_preserved(self):
        _, _, instance, state, context, target, events = setup()
        selector = {'$': 'STUConfigVarDynamic', 'm_identifier': 19, 'm_60DB8F99': 0}
        instance.write((0, 19), expr.Entity(OWNER))

        def lookup(identifier):
            events.append(('lookup', identifier))
            instance.write((0, 19), expr.Entity(OTHER))
            return target

        context.lookup = lookup
        self.assertEqual(instance.evaluate(config({'$': 'STU_06610EBC', 'm_configVars': [
            selector, {'$': 'Unknown'}]}), state), 0)
        self.assertEqual([row[1] for row in events if row[0] == 'lookup'], [OWNER, OWNER])
        self.assertIn(instance.find_var(0, 19), state.watched)
        self.assertIn(team.CLASS, state.natives)

    def test_second_lookup_absence_does_not_add_another_environment_subscription(self):
        _, _, instance, state, context, target, events = setup()
        selected = iter((target, None))
        context.lookup = lambda identifier: next(selected)
        self.assertEqual(instance.evaluate(config(), state), -1)
        self.assertEqual(events, [('filter', state, target)])

    def test_unknown_lookup_component_or_mutated_filter_bits_stay_strict(self):
        for result in (NotImplemented, True, team.Target(OTHER, None), team.Target(OWNER)):
            _, _, instance, _, context, _, _ = setup()
            context.lookup = lambda identifier, result=result: result
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config())
        for bits in (None, True, -1, 1 << 64):
            _, _, instance, _, _, target, _ = setup()
            target.filters.bits = bits
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config())

    def test_unwatched_query_needs_no_observer_but_watched_unknown_observer_fails(self):
        _, _, instance, state, context, _, _ = setup()
        context.watch_filter = NotImplemented
        self.assertEqual(instance.evaluate(config()), 0)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(), state)

    def test_known_absent_environment_hub_does_not_remove_the_second_lookup(self):
        _, _, instance, state, context, _, events = setup()
        context.watch_environment = None
        context.lookup = lambda identifier: events.append(('lookup', identifier))
        self.assertEqual(instance.evaluate(config(), state), -1)
        self.assertEqual(events, [('lookup', OWNER), ('lookup', OWNER)])

    def test_callback_retirement_cannot_return_success_or_touch_a_replacement_context(self):
        for field in ('lookup', 'watch_filter', 'watch_environment'):
            world, component, instance, state, context, target, _ = setup()
            if field == 'watch_environment':
                context.lookup = lambda identifier: None

            def retire(*args, world=world, target=target):
                world.unbind(OWNER)
                return target

            setattr(context, field, retire)
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(), state)
            self.assertTrue(context.retired)
            self.assertIsNotNone(component.fault)
            self.assertEqual(component._operation_depth, 0)

    def test_stale_instance_cannot_borrow_a_replacement_components_context(self):
        world, component, instance, _, context, _, _ = setup()
        world.unbind_script_component(OWNER, component)
        other = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, other)
        active = other.create(graph(), init=False)
        replacement = team.Context(OWNER, True, True, True,
            lookup=lambda identifier: team.Target(identifier, team.FilterBits(TEAM0 << 4)))
        world.set_entity_team_context(OWNER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())
        self.assertTrue(context.retired)
        self.assertFalse(replacement.retired)
        self.assertEqual(active.evaluate(config()), 4)

    def test_both_lookup_passes_share_the_operation_budget(self):
        _, component, instance, _, context, _, events = setup()
        with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 2),
              self.assertRaises(runtime.ScriptBudgetError)):
            instance.evaluate(config())
        self.assertEqual(events, [('lookup', OWNER)])
        self.assertIsNotNone(component.fault)
        self.assertTrue(context.retired)

    def test_constructor_binding_and_filter_input_validation(self):
        world, _, _, _, context, _, _ = setup()
        for bits in (True, None, -1, 1 << 64):
            with self.assertRaises(ValueError):
                team.FilterBits(bits)
        for args in ((0, True, True, True), (OWNER, 1, True, True), (OWNER, True, None, True)):
            with self.assertRaises(ValueError):
                team.Context(*args)
        with self.assertRaises(ValueError):
            world.set_entity_team_context(OWNER, team.Context(OWNER, True, True, True))
        context.retire()
        with self.assertRaises(ValueError):
            world.set_entity_team_context(OWNER, context)

    def test_value_interface_returns_a_signed_integer_and_config_iteration_keeps_minus_one(self):
        _, _, instance, _, _, target, _ = setup()
        target.filters.bits = 1 << 28
        self.assertEqual(play.evaluate_value(instance, config()), -1)
        self.assertEqual(tuple(play._iter_values(instance, config())), (-1,))


if __name__ == '__main__':
    unittest.main()
