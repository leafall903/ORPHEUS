"""Original entity-query guards, typed failure, dependencies and owner lifetimes."""

import unittest

from ow174.game.script import entity_queries as query
from ow174.game.script import expr, play, runtime, scene
from tests.test_script_runtime_budget import graph, node
from ow174.game.script import controller_queries

OWNER, BODY = 0xA0000100, 0xA0000101
ABSENT = object()


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'Literal':
            return cfg['value']
        return super().native(cfg, instance)


def config(cls=query.EXISTS, value=ABSENT):
    return {'$': cls, **({'m_entity': {'$': 'Literal', 'value': value}} if value is not ABSENT else {})}


def setup():
    world = ValueWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    instance = component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
    state = instance.state(0)
    events, targets = [], {}
    context = query.Context(OWNER, True, True, True,
        lookup=lambda identifier: events.append(('lookup', identifier)) or targets.get(identifier),
        watch_environment=lambda watcher, kind: events.append(('environment', watcher, kind)),
        watch_possession=lambda watcher, target: events.append(('possession', watcher, target)))
    world.set_entity_query_context(OWNER, context)
    return world, component, instance, state, context, targets, events


class EntityQueryTests(unittest.TestCase):
    def test_scene_possession_changes_wake_only_the_watched_owner(self):
        other = OWNER + 2
        world = scene.SceneWorld()
        for entity in (OWNER, BODY, other):
            world.bind(entity)
        world.set_possessions(possessors=(OWNER, other))
        component = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, component)
        world.set_entity_query_context(OWNER)
        cfg = {'$': query.POSSESSED, 'm_entity': {'$': 'STU_B5A0CAF0'}}
        instance = component.create(graph([node(0, 'STUStatescriptStateBooleanSwitch', state=0,
                                               m_condition=cfg)]))
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_frame(0)
        self.assertFalse(state.current)

        world.set_current_possession(other, BODY)
        self.assertFalse(state.flags_changed)
        world.set_current_possession(OWNER, BODY)
        self.assertTrue(state.flags_changed)
        component.run_frame(1)
        self.assertTrue(state.current)
        world.set_current_possession(OWNER, BODY)
        self.assertFalse(state.flags_changed)

        world.unbind(BODY)
        world.bind(BODY)
        component.run_frame(2)
        self.assertFalse(state.current)
        world.set_current_possession(OWNER, BODY)
        component.run_frame(3)
        self.assertTrue(state.current)
        world.set_possessor(OWNER, False)
        component.run_frame(4)
        self.assertFalse(state.current)
        self.assertFalse(state.watched)

    def test_scene_ai_links_wake_the_watcher_and_do_not_survive_target_id_reuse(self):
        world = scene.SceneWorld()
        world.bind(OWNER)
        world.bind(BODY)
        world.set_possessions(possessors=(OWNER,))
        world.bind_ai_behavior(OWNER)
        component = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, component)
        world.set_entity_query_context(OWNER, ai_links=True)
        cfg = {'$': query.LINKED, 'm_152B6961': {'$': 'STU_B5A0CAF0'}}
        instance = component.create(graph([node(0, 'STUStatescriptStateBooleanSwitch', state=0,
                                               m_condition=cfg)]))
        state = instance.state(0)
        state.request(runtime.BEGIN)
        component.run_frame(0)
        self.assertFalse(state.current)
        self.assertEqual(instance.evaluate(cfg), expr.Entity(0))

        world.set_ai_linked_target(OWNER, 0, BODY)
        component.run_frame(1)
        self.assertTrue(state.current)
        self.assertEqual(instance.evaluate(cfg), expr.Entity(BODY))

        world.unbind(BODY)
        world.bind(BODY)
        component.run_frame(2)
        self.assertFalse(state.current)
        self.assertEqual(instance.evaluate(cfg), expr.Entity(0))

    def test_unknown_services_remain_strict_and_legacy_world_is_unchanged(self):
        world = ValueWorld()
        world.bind(OWNER)
        for cls in (query.EXISTS, query.POSSESSED, query.LINKED, query.PARTICIPANT):
            component = runtime.Component(OWNER, world=world, strict=True)
            instance = component.create(graph(), init=False)
            with self.assertRaises(runtime.UnsupportedScriptError) as error:
                instance.evaluate(config(cls))
            self.assertEqual(error.exception.diagnostic['feature'], 'world query')
        permissive = runtime.Component(OWNER, world=world)
        instance = permissive.create(graph(), init=False)
        self.assertEqual(instance.evaluate(config(query.EXISTS)), expr.default_native(config(query.EXISTS)))

    def test_participant_lookup_preserves_output_and_watches_environment_on_miss(self):
        _, _, instance, state, context, _, events = setup()
        context.lookup_participant = lambda entity, watcher: BODY if entity == OWNER else None
        selected = config(query.PARTICIPANT, expr.Entity(OWNER))
        self.assertEqual(instance.evaluate(selected, state), expr.Entity(BODY))
        self.assertEqual(events, [])
        self.assertEqual(instance.evaluate(config(query.PARTICIPANT, expr.Entity(BODY)), state, initial=7), 7)
        self.assertEqual(events, [('environment', state, 0)])
        context.environment_present = False
        self.assertEqual(instance.evaluate(config(query.PARTICIPANT, expr.Entity(BODY)), state, initial=7), 7)
        self.assertEqual(events[-1], ('environment', state, 46))

    def test_linked_entity_reads_the_selected_record_after_subscribing(self):
        _, _, instance, state, context, _, events = setup()
        links = {(OWNER, 0): BODY}

        def lookup(entity, selector):
            events.append(('linked', entity, selector))
            return links.get((entity, selector))

        context.lookup_linked = lookup
        cfg = {'$': query.LINKED, 'm_152B6961': {'$': 'Literal', 'value': expr.Entity(OWNER)}}
        self.assertEqual(instance.evaluate(cfg, state), expr.Entity(BODY))
        self.assertEqual(events, [('environment', state, 0x20), ('linked', OWNER, 0)])
        cfg['m_A60150E2'] = {'$': 'Literal', 'value': 1}
        self.assertEqual(instance.evaluate(cfg, initial=17), expr.Entity(0))
        self.assertEqual(events[-1], ('linked', OWNER, 1))

    def test_namespace_failure_does_not_evaluate_or_subscribe(self):
        _, _, instance, state, context, _, events = setup()
        context.namespace_present = False
        initial = object()
        self.assertIs(instance.evaluate({'$': query.EXISTS, 'm_entity': {'$': 'Unknown'}},
                                       state, initial=initial), initial)
        self.assertEqual(events, [])

    def test_missing_environment_succeeds_false_without_evaluating_selector(self):
        for watched in (False, True):
            _, _, instance, state, context, _, events = setup()
            context.environment_present = False
            result = instance.evaluate({'$': query.EXISTS, 'm_entity': {'$': 'Unknown'}},
                                       state if watched else None, initial=17)
            self.assertIs(result, False)
            self.assertEqual(events, [('environment', state, 46)] if watched else [])

    def test_native_entity_conversion_and_invalid_guard_precede_lookup(self):
        for value in (ABSENT, None, True, OWNER, expr.Asset(OWNER), (),
                      (None, expr.Entity(BODY)), expr.Entity(0), expr.Entity(0x1FFFFFFF),
                      expr.Entity(0xFFFFFFFF), expr.Entity(0x11FFFFFFF)):
            with self.subTest(value=value):
                _, _, instance, state, _, _, events = setup()
                self.assertIs(instance.evaluate(config(value=value), state), False)
                self.assertEqual(events, [('environment', state, 0)])

    def test_lookup_presence_uses_full_wrapped_native_identity(self):
        for value in (expr.Entity(BODY), ((expr.Entity(BODY),),), expr.Entity(BODY+(1 << 32))):
            _, _, instance, state, _, targets, events = setup()
            targets[BODY] = query.Target(BODY)
            self.assertIs(instance.evaluate(config(value=value), state), True)
            self.assertEqual(events, [('environment', state, 0), ('lookup', BODY)])
            targets.clear()
            self.assertIs(instance.evaluate(config(value=value)), False)

    def test_group_selector_uses_first_config_and_preserves_variable_watch(self):
        _, _, instance, state, _, targets, events = setup()
        state.begin()
        targets[BODY] = query.Target(BODY)
        variable = instance.find_var(0, 19, create=True)
        variable.base = ((expr.Entity(BODY),),)
        cfg = {'$': query.EXISTS, 'm_entity': {'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'STUConfigVarDynamic', 'm_identifier': 19, 'm_60DB8F99': 0}, {'$': 'Unknown'}]}}
        self.assertTrue(instance.evaluate(cfg, state))
        self.assertIn(variable, state.watched)
        self.assertIn(state, variable.watchers)
        self.assertTrue(state.polled)
        self.assertEqual(events[-1], ('lookup', BODY))

    def test_known_no_output_selector_becomes_invalid_entity(self):
        _, _, instance, _, _, _, events = setup()
        cfg = {'$': query.EXISTS, 'm_entity': {'$': 'STU_7BD2CA19', 'm_resourceKey': None}}
        self.assertIs(instance.evaluate(cfg, initial=17), False)
        self.assertEqual(events, [])

    def test_base_boolean_fallback_preserves_output_without_evaluating_operand(self):
        _, _, instance, state, _, _, events = setup()
        cfg = {'$': 'STU_B40ACFBA', 'm_52EA7AB9': {'$': 'Unknown'}}
        self.assertEqual(play.evaluate_scalar_result(instance, cfg, initial=True, watcher=state),
                         (True, False))
        self.assertEqual(events, [])

    def test_possession_context_failure_skips_selector(self):
        _, _, instance, state, context, _, events = setup()
        context.possession_context_present = False
        initial = object()
        self.assertIs(instance.evaluate({'$': query.POSSESSED, 'm_entity': {'$': 'Unknown'}},
                                       state, initial=initial), initial)
        self.assertEqual(events, [])

    def test_missing_entity_preserves_output_and_subscribes_to_correct_environment(self):
        for present in (False, True):
            _, _, instance, state, context, _, events = setup()
            context.environment_present = present
            initial = object()
            self.assertIs(instance.evaluate(config(query.POSSESSED, expr.Entity(OWNER)),
                                           state, initial=initial), initial)
            self.assertEqual(events, [('lookup', OWNER), ('environment', state, 0 if present else 46)])

    def test_possession_passes_invalid_input_to_lookup_without_entity_exists_guard(self):
        for value, identifier in ((ABSENT, 0), (OWNER, 0), (expr.Entity(0xFFFFFFFF), 0xFFFFFFFF)):
            _, _, instance, _, _, _, events = setup()
            initial = object()
            self.assertIs(instance.evaluate(config(query.POSSESSED, value), initial=initial), initial)
            self.assertEqual(events, [('lookup', identifier)])

    def test_existing_entity_without_component_writes_typed_zero_without_subscription(self):
        _, _, instance, state, _, targets, events = setup()
        targets[OWNER] = query.Target(OWNER, None)
        self.assertEqual(instance.evaluate(config(query.POSSESSED, expr.Entity(OWNER)), state),
                         expr.Entity(0))
        self.assertEqual(events, [('lookup', OWNER)])

    def test_current_possession_is_read_after_component_subscription_and_not_reverse_lookup(self):
        world, _, instance, state, context, targets, events = setup()
        world.bind(BODY)
        world.set_possessions(possessors=(OWNER,), primary=((BODY, OWNER),))
        current = query.Possession(0)
        target = query.Target(OWNER, current)
        targets[OWNER] = target

        def watch(watcher, selected):
            events.append(('possession', watcher, selected))
            current.current = 0xFFFFFFFF

        context.watch_possession = watch
        self.assertEqual(instance.evaluate(config(query.POSSESSED, expr.Entity(OWNER)), state),
                         expr.Entity(0xFFFFFFFF))
        self.assertEqual(events, [('lookup', OWNER), ('possession', state, target)])
        current.current = BODY
        self.assertEqual(instance.evaluate(config(query.POSSESSED, expr.Entity(OWNER))), expr.Entity(BODY))

    def test_nested_query_evaluates_body_before_existence_lookup(self):
        _, _, instance, state, _, targets, events = setup()
        targets[OWNER] = query.Target(OWNER, query.Possession(BODY))
        targets[BODY] = query.Target(BODY, None)
        nested = {'$': query.EXISTS, 'm_entity': {'$': query.POSSESSED, 'm_entity': {'$': 'STU_B5A0CAF0'}}}
        self.assertTrue(instance.evaluate(nested, state))
        self.assertEqual([row[0] for row in events], ['environment', 'lookup', 'possession', 'lookup'])
        self.assertEqual(events[-1], ('lookup', BODY))

    def test_unknown_possession_and_lookup_results_remain_strict(self):
        for result, feature in ((NotImplemented, 'entity query lookup result'),
                                (True, 'entity query lookup result'),
                                (query.Target(BODY), 'entity query lookup result'),
                                (query.Target(OWNER), 'entity query current possession')):
            _, _, instance, _, context, _, _ = setup()
            context.lookup = lambda identifier, result=result: result
            with self.assertRaises(runtime.UnsupportedScriptError) as error:
                instance.evaluate(config(query.POSSESSED, expr.Entity(OWNER)))
            self.assertEqual(error.exception.diagnostic['feature'], feature)

    def test_unknown_dependency_service_is_not_substituted_with_polling(self):
        for field in ('watch_environment', 'watch_possession'):
            _, _, instance, state, context, targets, _ = setup()
            targets[OWNER] = query.Target(OWNER, query.Possession(BODY))
            setattr(context, field, NotImplemented)
            cls = query.EXISTS if field == 'watch_environment' else query.POSSESSED
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(cls, expr.Entity(OWNER)), state)

    def test_known_absent_environment_hub_skips_only_environment_subscription(self):
        _, _, instance, state, context, _, events = setup()
        context.watch_environment = None
        context.environment_present = False
        self.assertFalse(instance.evaluate(config(), state))
        self.assertEqual(events, [])

    def test_callback_retirement_cannot_return_success(self):
        for callback in ('lookup', 'watch_environment', 'watch_possession'):
            world, component, instance, state, context, targets, _ = setup()
            target = query.Target(OWNER, query.Possession(BODY))
            targets[OWNER] = target

            def retire(*args, world=world, target=target):
                world.unbind(OWNER)
                return target

            setattr(context, callback, retire)
            cls = query.EXISTS if callback == 'watch_environment' else query.POSSESSED
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(cls, expr.Entity(OWNER)), state)
            self.assertTrue(context.retired)
            self.assertIsNotNone(component.fault)
            self.assertIs(world.entity_query_context(OWNER), NotImplemented)

    def test_detach_fault_and_rebind_retire_only_the_owning_context(self):
        world, component, _, _, context, _, _ = setup()
        world.unbind_script_component(OWNER, component)
        self.assertTrue(context.retired)
        replacement = query.Context(OWNER, True, True, True)
        other = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, other)
        world.set_entity_query_context(OWNER, replacement)
        world.script_failed(component)
        self.assertIs(world.entity_query_context(OWNER), replacement)
        world.script_failed(other)
        self.assertTrue(replacement.retired)

    def test_binding_and_native_input_validation(self):
        world, _, _, _, context, _, _ = setup()
        with self.assertRaises(ValueError):
            world.set_entity_query_context(OWNER, query.Context(OWNER, True, True, True))
        for args in ((0, True, True, True), (OWNER, 1, True, True), (OWNER, True, None, True)):
            with self.assertRaises(ValueError):
                query.Context(*args)
        for value in (True, -1, 1 << 32):
            with self.assertRaises(ValueError):
                query.Possession(value)
        context.retire()
        with self.assertRaises(ValueError):
            world.set_entity_query_context(OWNER, context)


    def test_scene_definition_query_preserves_resource_type_and_binding_lifetime(self):
        world = scene.SceneWorld()
        world.bind(OWNER)
        component = runtime.Component(OWNER, world=world, strict=True)
        instance = component.create(graph(), init=False)
        cfg = {"$": query.DEFINITION, "m_entity": expr.Entity(BODY)}
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))
        world.bind(BODY)
        self.assertIs(query.evaluate(instance.context(), cfg), NotImplemented)
        world.unbind(BODY)
        world.bind(BODY, definition=0x0400000000001D40)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0x0400000000001D40))
        world.unbind(BODY)
        world.bind(BODY, definition=0)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))


    def test_component92_query_reads_the_source_without_following_component43(self):
        world, _, instance, _, _, _, _ = setup()
        world.bind(BODY)
        cfg = {"$": controller_queries.SOURCE_HAS_92, "m_entity": expr.Entity(OWNER)}
        world.set_controller_record(controller_queries.Record(OWNER, frozenset({43}), BODY))
        world.set_controller_record(controller_queries.Record(BODY, frozenset({92})))
        self.assertIs(instance.evaluate(cfg), False)
        world.set_controller_record(controller_queries.Record(OWNER, frozenset({43, 92}), BODY))
        world.set_controller_record(controller_queries.Record(BODY, frozenset({44})))
        self.assertIs(instance.evaluate(cfg), True)
        world.unbind(OWNER)
        self.assertIs(instance.evaluate(cfg), False)


    def test_component76_primary_can_be_suppressed_without_fallback(self):
        world, _, instance, _, _, _, _ = setup()
        world.bind(BODY)
        cfg = {"$": query.COMPONENT76_KEY, "m_entity": expr.Entity(BODY),
               "m_88B0050A": "0x0D6000000000003A"}
        self.assertIs(query.evaluate(instance.context(), cfg), NotImplemented)
        world.set_component76_keys(BODY, None)
        self.assertIs(instance.evaluate(cfg), False)
        keys = frozenset({58})
        world.set_component76_keys(BODY, query.Component76Keys(primary=keys))
        self.assertIs(instance.evaluate(cfg), True)
        world.set_component76_keys(BODY, query.Component76Keys(keys, keys, keys))
        self.assertIs(instance.evaluate(cfg), False)
        world.set_component76_keys(BODY, query.Component76Keys(fallback=keys, suppressed=keys))
        cfg["m_88B0050A"] = "0x048000000000003A"
        self.assertIs(instance.evaluate(cfg), True)
        world.unbind(BODY)
        self.assertIs(instance.evaluate(cfg), False)


    def test_target_queries_distinguish_missing_records_from_numeric_zero(self):
        world, _, instance, _, _, _, _ = setup()
        world.bind(BODY)
        target = {"$": query.INDEXED_TARGET, "m_152B6961": expr.Entity(OWNER), "m_A60150E2": 3}
        scalar = {"$": query.TARGET_RECORD_SCALAR, "m_target": target, "m_7826B991": expr.Entity(OWNER)}
        self.assertIs(query.evaluate(instance.context(), target), NotImplemented)
        world.indexed_targets[OWNER] = None
        self.assertEqual(instance.evaluate(target, initial=17), 17)
        world.indexed_targets[OWNER] = {}
        self.assertEqual(instance.evaluate(target), expr.Entity(0))
        world.indexed_targets[OWNER] = {(OWNER, 3): BODY}
        self.assertEqual(instance.evaluate(target), expr.Entity(BODY))
        self.assertIs(query.evaluate(instance.context(), scalar), NotImplemented)
        for records in (None, {}):
            world.target_record_scalars[OWNER] = records
            self.assertEqual(instance.evaluate(scalar, initial=17), 17)
        for value in (0.0, 1.0, -0.5):
            world.target_record_scalars[OWNER] = {BODY: value}
            result = instance.evaluate(scalar, initial=17)
            self.assertIs(type(result), float)
            self.assertEqual(result, value)
        world.unbind(OWNER)
        self.assertIs(query.evaluate(instance.context(), target), NotImplemented)



if __name__ == '__main__':
    unittest.main()
