"""Controller hero iteration: scalar/collected values, dependencies and owner lifetime."""

import unittest
from unittest import mock

from ow174.game.script import controller_heroes as heroes
from ow174.game.script import expr, play, runtime, scene
from tests.test_script_runtime_budget import graph, node, plug, setter

OWNER, OTHER = 0xA0000100, 0xA0000101
A, B, C = 0x02E000000000006E, 0x02E0000000000002, 0x02E0000000000042


def literal(value):
    return {'$': 'Literal', 'value': value}


def config(selector=None):
    return {'$': heroes.CLASS, 'm_controller': selector or {'$': 'STU_B5A0CAF0'}}


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'Literal':
            return cfg['value']
        return super().native(cfg, instance)


def setup(catalog=(0, A, B, A, C)):
    world = ValueWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    instance = component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
    instance.initialized = True
    state = instance.state(0)
    state.begin()
    events, allowed = [], {A, C}
    controller = heroes.Controller(OWNER)
    context = heroes.Context(OWNER, True, True,
        lookup=lambda identifier: events.append(('lookup', identifier)) or (
            controller if identifier == OWNER else None),
        catalog=lambda: events.append(('catalog',)) or catalog,
        available=lambda guid, target: events.append(('available', guid, target)) or guid in allowed,
        watch_environment=lambda watcher, kind: events.append(('watch', watcher, kind)))
    world.set_controller_hero_context(OWNER, context)
    return world, component, instance, state, context, events, allowed, controller


class ControllerHeroTests(unittest.TestCase):
    def test_unknown_service_stays_strict_and_legacy_unknown_value_is_preserved(self):
        for strict in (False, True):
            world = ValueWorld()
            world.bind(OWNER)
            component = runtime.Component(OWNER, world=world, strict=strict)
            instance = component.create(graph(), init=False)
            if strict:
                with self.assertRaises(runtime.UnsupportedScriptError) as error:
                    play.evaluate_value(instance, config())
                self.assertEqual(error.exception.diagnostic['feature'], 'world query')
            else:
                self.assertIsNone(instance.evaluate(config()))
                self.assertIsNone(play.evaluate_value(instance, config()))

    def test_scalar_returns_first_accepted_asset_and_stops_before_later_candidates(self):
        _, component, instance, state, _, events, _, target = setup()
        self.assertEqual(instance.evaluate(config(), state), expr.Asset(A))
        self.assertEqual(events, [('watch', state, 3), ('watch', state, 37), ('lookup', OWNER),
                                  ('catalog',), ('available', A, target)])
        self.assertIn(heroes.CLASS, state.natives)
        self.assertEqual(instance.ticking.count(state), 1)
        self.assertEqual(component._operation_depth, 0)

    def test_value_interface_keeps_native_order_duplicates_and_typed_empty_array(self):
        _, _, instance, _, _, events, _, _ = setup()
        self.assertEqual(play.evaluate_value(instance, config()),
                         (expr.Asset(A), expr.Asset(A), expr.Asset(C)))
        self.assertEqual([row[1] for row in events if row[0] == 'available'], [A, B, A, C])
        _, _, instance, _, _, _, _, _ = setup(())
        self.assertEqual(play.evaluate_value(instance, config()), ())

    def test_no_item_scalar_overwrites_the_previous_destination_with_integer_zero(self):
        for catalog in ((), (0, B, 0)):
            _, _, instance, _, _, _, _, _ = setup(catalog)
            value = instance.evaluate(config(), initial=expr.Asset(17))
            self.assertEqual(value, 0)
            self.assertIs(type(value), int)

    def test_missing_controller_still_constructs_catalog_but_never_filters_candidates(self):
        _, _, instance, state, _, events, _, _ = setup()
        self.assertEqual(tuple(play._iter_values(instance, config(literal(expr.Entity(OTHER))),
                                                 watcher=state)), ())
        self.assertEqual(events, [('watch', state, 3), ('watch', state, 37), ('lookup', OTHER), ('catalog',)])

    def test_invalid_and_nonentity_selectors_skip_lookup_but_not_catalog(self):
        for value in (None, True, OWNER, expr.Asset(OWNER), (), (None, expr.Entity(OWNER)),
                      expr.Entity(0), expr.Entity(0xFFFFFFFF)):
            with self.subTest(value=value):
                _, _, instance, _, _, events, _, _ = setup()
                self.assertEqual(instance.evaluate(config(literal(value))), 0)
                self.assertEqual(events, [('catalog',)])

    def test_typed_entity_conversion_wraps_identity_and_reads_first_array_member(self):
        for value in (expr.Entity(OWNER+(1 << 32)), ((expr.Entity(OWNER),),)):
            _, _, instance, _, _, events, _, _ = setup()
            self.assertEqual(instance.evaluate(config(literal(value))), expr.Asset(A))
            self.assertEqual(events[0], ('lookup', OWNER))

    def test_selector_group_takes_one_config_and_preserves_variable_watch(self):
        _, _, instance, state, _, _, _, _ = setup()
        variable = instance.find_var(0, 19)
        variable.base = expr.Entity(OWNER)
        selector = {'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'STUConfigVarDynamic', 'm_identifier': 19, 'm_60DB8F99': 0}, {'$': 'Unknown'}]}
        self.assertEqual(tuple(play._iter_values(instance, config(selector), watcher=state)),
                         (expr.Asset(A), expr.Asset(A), expr.Asset(C)))
        self.assertIn(variable, state.watched)
        self.assertIn(state, variable.watchers)
        self.assertIn(heroes.CLASS, state.natives)

    def test_nested_config_groups_traverse_all_hero_items_instead_of_the_scalar_item(self):
        _, _, instance, _, _, events, _, _ = setup()
        grouped = {'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'STUConfigVarInt', 'm_value': 3}, config(), {'$': 'STUConfigVarInt', 'm_value': 5}]}
        self.assertEqual(play.evaluate_value(instance, grouped),
                         (3, expr.Asset(A), expr.Asset(A), expr.Asset(C), 5))
        self.assertEqual(sum(row[0] == 'catalog' for row in events), 1)

    def test_setvar_stores_the_collected_array_before_following_its_output(self):
        _, _, instance, _, _, _, _, _ = setup()
        action = setter(1, 0)
        action['fields'].update(m_value=config(), m_outPlug=plug((2, 'm_inPlug')))
        tail = setter(2, 17)
        tail['fields']['m_out_Var']['m_identifier'] = 8
        model = graph([node(0, 'STUStatescriptState', state=0), action, tail])
        instance.graph = model
        instance.run_node(model.node(1), None)
        self.assertEqual(instance.find_var(0, 7).value(), (expr.Asset(A), expr.Asset(A), expr.Asset(C)))
        self.assertEqual(instance.find_var(0, 8).value(), 17)

    def test_catalog_is_a_per_evaluation_snapshot_and_not_a_cached_or_sorted_list(self):
        _, _, instance, _, context, _, allowed, _ = setup()
        source = [C, A]
        allowed.add(B)
        context.catalog = lambda: tuple(source)

        def available(guid, target):
            source[:] = [B]
            return True

        context.available = available
        self.assertEqual(play.evaluate_value(instance, config()), (expr.Asset(C), expr.Asset(A)))
        self.assertEqual(play.evaluate_value(instance, config()), (expr.Asset(B),))

    def test_unknown_later_availability_is_lazy_for_scalar_but_strict_when_collected(self):
        _, component, instance, _, context, _, _, _ = setup((A, B))
        context.available = lambda guid, target: True if guid == A else NotImplemented
        self.assertEqual(instance.evaluate(config()), expr.Asset(A))
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            play.evaluate_value(instance, config())
        self.assertEqual(error.exception.diagnostic['feature'], 'controller hero availability result')
        self.assertEqual(component._operation_depth, 0)

    def test_unknown_services_and_bad_lookup_or_catalog_results_are_not_empty_success(self):
        for field, result in (('lookup', NotImplemented), ('lookup', True),
                              ('lookup', heroes.Controller(OTHER)), ('catalog', []),
                              ('catalog', NotImplemented), ('available', 1)):
            _, _, instance, _, context, _, _, _ = setup()
            setattr(context, field, lambda *args, result=result: result)
            with self.assertRaises(runtime.UnsupportedScriptError):
                play.evaluate_value(instance, config())

    def test_invalid_catalog_rows_fail_only_when_iteration_reaches_them(self):
        for bad in (None, True, -1, 1 << 64):
            _, _, instance, _, _, _, _, _ = setup((A, bad))
            self.assertEqual(instance.evaluate(config()), expr.Asset(A))
            with self.assertRaises(runtime.UnsupportedScriptError):
                play.evaluate_value(instance, config())

    def test_known_no_hub_skips_observers_but_unknown_hub_is_not_replaced_by_polling(self):
        for callback in (None, NotImplemented):
            _, _, instance, state, context, events, _, _ = setup()
            context.watch_environment = callback
            if callback is None:
                self.assertEqual(instance.evaluate(config(), state), expr.Asset(A))
                self.assertFalse(any(row[0] == 'watch' for row in events))
            else:
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate(config(), state)

    def test_missing_namespace_and_environment_stay_explicit_unsupported_paths(self):
        for field in ('namespace_present', 'environment_present'):
            _, _, instance, state, context, events, _, _ = setup()
            setattr(context, field, False)
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(), state)
            self.assertEqual(events, [] if field == 'namespace_present' else [('watch', state, 46)])

    def test_callback_retirement_cannot_return_a_value_or_retain_the_context(self):
        for field in ('lookup', 'catalog', 'available', 'watch_environment'):
            world, component, instance, state, context, _, _, target = setup()

            def retire(*args, world=world, field=field, target=target):
                world.unbind(OWNER)
                return {'lookup': target, 'catalog': (A,), 'available': True}.get(field)

            setattr(context, field, retire)
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(), state)
            self.assertTrue(context.retired)
            self.assertIs(world.controller_hero_context(OWNER), NotImplemented)
            self.assertIsNotNone(component.fault)
            self.assertEqual(component._operation_depth, 0)

    def test_paused_iteration_checks_lifetime_before_the_next_candidate_and_closes(self):
        world, component, instance, _, context, _, _, _ = setup()
        iterator = heroes.values(instance.context(), config())
        self.assertEqual(next(iterator), expr.Asset(A))
        world.unbind(OWNER)
        with self.assertRaises(runtime.UnsupportedScriptError):
            next(iterator)
        iterator.close()
        self.assertTrue(context.retired)
        self.assertEqual(component._operation_depth, 0)

    def test_zero_and_rejected_rows_consume_the_shared_work_budget(self):
        _, component, instance, _, context, _, _, _ = setup((0,)*100)
        with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 3),
              self.assertRaises(runtime.ScriptBudgetError)):
            play.evaluate_value(instance, config())
        self.assertIsNotNone(component.fault)
        self.assertTrue(context.retired)
        self.assertEqual(component._operation_depth, 0)

    def test_context_validation_detach_and_replacement_owner_failure(self):
        world, component, _, _, context, _, _, _ = setup()
        for owner, namespace, environment in ((0, True, True), (OWNER, 1, True), (OWNER, True, None)):
            with self.assertRaises(ValueError):
                heroes.Context(owner, namespace, environment)
        with self.assertRaises(ValueError):
            world.set_controller_hero_context(OWNER, heroes.Context(OWNER, True, True))
        world.unbind_script_component(OWNER, component)
        self.assertTrue(context.retired)
        other = runtime.Component(OWNER, world=world, strict=True)
        replacement = heroes.Context(OWNER, True, True)
        world.bind_script_component(OWNER, other)
        world.set_controller_hero_context(OWNER, replacement)
        world.script_failed(component)
        self.assertIs(world.controller_hero_context(OWNER), replacement)
        world.script_failed(other)
        self.assertTrue(replacement.retired)

    def test_stale_instance_cannot_borrow_the_replacement_components_context(self):
        world, component, instance, _, previous, _, _, _ = setup()
        world.unbind_script_component(OWNER, component)
        other = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, other)
        active = other.create(graph(), init=False)
        replacement = heroes.Context(OWNER, True, True,
            lookup=lambda identifier: heroes.Controller(identifier), catalog=lambda: (A,),
            available=lambda guid, target: True, watch_environment=None)
        world.set_controller_hero_context(OWNER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())
        self.assertTrue(previous.retired)
        self.assertFalse(replacement.retired)
        self.assertIs(world.controller_hero_context(OWNER, component=other), replacement)
        self.assertEqual(active.evaluate(config()), expr.Asset(A))


if __name__ == '__main__':
    unittest.main()
