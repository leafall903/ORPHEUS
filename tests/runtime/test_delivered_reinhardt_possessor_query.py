"""CDBDE0/D15520 primary-possession selection, typed failures and dependencies."""

import unittest

from test_delivered_reinhardt_entity_queries import BODY, OWNER, setup

from ow174.game.script import entity_queries as query
from ow174.game.script import expr, runtime

DEFAULT_ENTITY = expr.Entity(BODY)


def config(value=DEFAULT_ENTITY):
    return {'$': query.POSSESSOR, 'm_801B298F': {'$': 'Literal', 'value': value}}


class PossessorQueryTests(unittest.TestCase):
    def test_context_missing_and_lookup_missing_leave_typed_destination_unchanged(self):
        for context_present in (False, True):
            _, _, instance, state, context, _, events = setup()
            context.possession_context_present = context_present
            sentinel = object()
            self.assertIs(instance.evaluate(config(), state, initial=sentinel), sentinel)
            if context_present:
                self.assertEqual(events, [('lookup', BODY), ('environment', state, 0)])
            else:
                self.assertEqual(events, [])

    def test_known_component_absence_and_all_uint32_possessor_values_are_entities(self):
        for value in (None, 0, 0xFFFFFFFF, OWNER):
            _, _, instance, state, context, targets, events = setup()
            context.watch_possessor = lambda watcher, target, events=events: events.append(
                ('possessor', target.entity))
            targets[BODY] = query.Target(BODY, possessor=None if value is None else query.Possession(value))
            result = instance.evaluate(config(), state)
            self.assertIs(type(result), expr.Entity)
            self.assertEqual(result, expr.Entity(0 if value is None else value))
            self.assertEqual(events, [('lookup', BODY)] + ([] if value is None else [('possessor', BODY)]))

    def test_scalar_selector_unwraps_first_array_and_does_not_reject_invalid_lookup_ids(self):
        for selected, expected in ((expr.Entity(0), 0), (expr.Entity(0xFFFFFFFF), 0xFFFFFFFF),
                                   (((expr.Entity(BODY),), expr.Entity(OWNER)), BODY), (True, 0), ((), 0)):
            _, _, instance, _, _, targets, events = setup()
            targets[expected] = query.Target(expected, possessor=None)
            self.assertEqual(instance.evaluate(config(selected)), expr.Entity(0))
            self.assertEqual(events, [('lookup', expected)])

    def test_component_subscription_precedes_read_of_mutable_relation(self):
        _, _, instance, state, context, targets, events = setup()
        relation = query.Possession(OWNER)
        targets[BODY] = query.Target(BODY, possessor=relation)

        def subscribe(watcher, target):
            self.assertIs(watcher, state)
            self.assertIs(target, targets[BODY])
            events.append(('possessor', relation.current))
            relation.current = 0xA0000200

        context.watch_possessor = subscribe
        self.assertEqual(instance.evaluate(config(), state), expr.Entity(0xA0000200))
        self.assertEqual(events, [('lookup', BODY), ('possessor', OWNER)])

    def test_unknown_component_or_observer_remains_strict(self):
        for known in (False, True):
            _, _, instance, state, _, targets, _ = setup()
            targets[BODY] = (query.Target(BODY, possessor=query.Possession(OWNER))
                             if known else query.Target(BODY))
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(), state)

    def test_owner_retirement_during_subscription_rejects_stale_result(self):
        _, _, instance, state, context, targets, _ = setup()
        targets[BODY] = query.Target(BODY, possessor=query.Possession(OWNER))

        def subscribe(*args):
            context.retire()

        context.watch_possessor = subscribe
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(), state)

    def test_missing_entity_watches_active_or_fallback_environment(self):
        for environment, kind in ((True, 0), (False, 46)):
            _, _, instance, state, context, _, events = setup()
            context.environment_present = environment
            sentinel = object()
            self.assertIs(instance.evaluate(config(), state, initial=sentinel), sentinel)
            self.assertEqual(events, [('lookup', BODY), ('environment', state, kind)])


if __name__ == '__main__':
    unittest.main()
