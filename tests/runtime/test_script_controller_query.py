"""Controller eligibility keeps native ordering and unknown host facts separate."""

import unittest

from test_script_entity_queries import BODY, OWNER
from test_script_entity_queries import setup as query_setup

from ow174.game.script import controller_query as query
from ow174.game.script import expr, runtime


def setup():
    world, component, instance, state, _, _, _ = query_setup()
    events, targets, controllers = [], {}, {}
    context = query.Context(OWNER, True,
        lookup=lambda entity: events.append(('lookup', entity)) or targets.get(entity),
        resolve_controller=lambda target: events.append(('controller', target.entity))
        or controllers.get(target.entity),
        watch_component43=lambda watcher, target: events.append(('component43', target.entity)))
    world.entity_controller_context = lambda entity: context
    return world, component, instance, state, context, targets, controllers, events


DEFAULT_ENTITY = expr.Entity(BODY)


def config(value=DEFAULT_ENTITY):
    return {'$': query.CLASS, 'm_entity': {'$': 'Literal', 'value': value}}


class ControllerQueryTests(unittest.TestCase):
    def test_unmounted_service_preserves_world_fallback_without_selector_side_effects(self):
        world, _, instance, _, _, _, _, _ = setup()
        world.entity_controller_context = lambda entity: NotImplemented
        seen = []
        base = world.native

        def native(cfg, instance):
            seen.append(cfg.get('$'))
            return False if cfg.get('$') == query.CLASS else base(cfg, instance)

        world.native = native
        self.assertIs(instance.evaluate(config()), False)
        self.assertEqual(seen, [query.CLASS])

    def test_unknown_backend_does_not_become_missing_controller(self):
        world, _, instance, _, _, _, _, _ = setup()
        world.entity_controller_context = lambda entity: NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_missing_entity_false_and_failed_scalar_still_looks_up_zero(self):
        _, _, instance, state, _, _, _, events = setup()
        for value in (expr.Entity(BODY), expr.EVALUATION_FAILED, None, True, 123):
            events.clear()
            self.assertIs(instance.evaluate(config(value), state), False)
            self.assertEqual(events, [('lookup', expr.to_entity(value) & 0xFFFFFFFF)])

    def test_presence_observation_then_fresh_controller_resolution(self):
        _, _, instance, state, context, targets, controllers, events = setup()
        targets[BODY] = query.Target(BODY, True)
        controllers[BODY] = query.Controller(OWNER, True, False)
        self.assertIs(instance.evaluate(config(), state), True)
        self.assertEqual(events, [('lookup', BODY), ('component43', BODY), ('controller', BODY)])
        context.watch_component43 = lambda *args: controllers.clear()
        self.assertIs(instance.evaluate(config(), state), False)

    def test_no_component43_does_not_skip_independent_controller_lookup(self):
        _, _, instance, state, _, targets, controllers, events = setup()
        targets[BODY] = query.Target(BODY, False)
        controllers[BODY] = query.Controller(OWNER, True, False)
        self.assertIs(instance.evaluate(config(), state), True)
        self.assertEqual(events, [('lookup', BODY), ('controller', BODY)])

    def test_bit_tests_short_circuit_unknown92_when75_is_absent(self):
        _, _, instance, _, _, targets, controllers, _ = setup()
        targets[BODY] = query.Target(BODY, False)
        for has75, has92, expected in ((False, NotImplemented, False), (True, False, True),
                                       (True, True, False), (False, True, False)):
            controllers[BODY] = query.Controller(OWNER, has75, has92)
            self.assertIs(instance.evaluate(config()), expected)
        controllers[BODY] = query.Controller(OWNER, True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_missing_namespace_and_unknown_component_facts_remain_unsupported(self):
        _, _, instance, _, context, targets, _, _ = setup()
        context.namespace_present = False
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())
        context.namespace_present = True
        targets[BODY] = query.Target(BODY)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_callback_cannot_retire_or_replace_bound_owner_or_target(self):
        for mutate in ('context', 'target', 'instance', 'binding', 'binding_getter'):
            with self.subTest(mutate=mutate):
                world, component, instance, state, context, targets, _, _ = setup()
                targets[BODY] = query.Target(BODY, True)

                def retire(*args, mutate=mutate, context=context, targets=targets,
                           component=component, instance=instance, world=world):
                    if mutate == 'context':
                        context.retired = True
                    elif mutate == 'target':
                        targets[BODY].retired = True
                    elif mutate == 'instance':
                        component.destroy(instance)
                    elif mutate == 'binding':
                        world.entity_controller_context = lambda entity: NotImplemented
                    else:
                        def retired_binding(entity):
                            context.retired = True
                            return context

                        world.entity_controller_context = retired_binding

                context.watch_component43 = retire
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate(config(), state)


if __name__ == '__main__':
    unittest.main()
