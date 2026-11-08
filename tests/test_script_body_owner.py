"""F726 direct relation contracts; strict unknowns and original-output preservation."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import body_owner as owner
from ow174.game.script import expr, runtime

BODY, PLAYER = 0xA0000101, 0xA0000001


class ValueWorld(runtime.World):
    context = NotImplemented

    def body_owner_context(self, entity):
        return self.context

    def native(self, cfg, instance):
        if cfg.get("$") == "Literal":
            return cfg["value"]
        return super().native(cfg, instance)


def setup():
    world = ValueWorld()
    component = runtime.Component(BODY, world=world, strict=True)
    instance = component.create(make_graph(0xFFFF, [node(0, state=0)], states=[0]), init=False)
    state = instance.state(0)
    targets, events = {}, []
    world.context = owner.Context(
        BODY, True, True,
        lookup=lambda identifier: events.append(("lookup", identifier)) or targets.get(identifier),
        watch_environment=lambda watcher, kind: events.append(("environment", kind)),
        watch_owner=lambda watcher, target: events.append(("owner", target.current)),
    )
    return world, component, instance, state, targets, events


def config(value):
    return {"$": owner.CLASS, "m_801B298F": {"$": "Literal", "value": value}}


class BodyOwnerTests(unittest.TestCase):
    def test_recorded_relation_baseline_requires_unique_uint32_pairs(self):
        self.assertEqual(owner.normalize_relations([[BODY, PLAYER]]), ((BODY, PLAYER),))
        for rows in ([[BODY, PLAYER], [BODY, 0]], [[BODY, True]], [[BODY, 1 << 32]], [BODY]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                owner.normalize_relations(rows)

    def test_unknown_context_and_unknown_entity_relation_do_not_become_null_owners(self):
        for unknown in ("context", "relation"):
            world, _, instance, _, _, _ = setup()
            if unknown == "context":
                world.context = NotImplemented
            else:
                world.context.lookup = lambda _identifier: NotImplemented
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(expr.Entity(BODY)))

    def test_missing_instance_skips_selector_and_preserves_destination(self):
        world, _, instance, state, _, events = setup()
        world.context.instance_present = False
        self.assertEqual(instance.evaluate({"$": owner.CLASS, "m_801B298F": {"$": "Unknown"}},
                                           state, initial=99), 99)
        self.assertEqual(events, [])

    def test_missing_entity_preserves_destination_and_declares_environment_subscription(self):
        for present in (True, False):
            world, _, instance, state, _, events = setup()
            world.context.environment_present = present
            self.assertEqual(instance.evaluate(config(expr.Entity(BODY)), state, initial=99), 99)
            self.assertEqual(events, [("lookup", BODY), ("environment", 0 if present else 46)])

    def test_absent_component_null_relation_and_real_relation_are_distinct(self):
        for value in (None, 0, PLAYER):
            _, _, instance, state, targets, events = setup()
            targets[BODY] = owner.Target(BODY, value)
            self.assertEqual(instance.evaluate(config(expr.Entity(BODY)), state), expr.Entity(value or 0))
            self.assertEqual(events, [("lookup", BODY)] + ([] if value is None else [("owner", value)]))

    def test_only_typed_entities_select_ids_and_shared_array_conversion_takes_first_member(self):
        for value, identifier in ((BODY, 0), (True, 0), (expr.Asset(BODY), 0),
                                  (None, 0), (expr.Entity(BODY + (1 << 32)), BODY),
                                  (((expr.Entity(BODY),),), BODY), ((None, expr.Entity(BODY)), 0)):
            _, _, instance, _, targets, events = setup()
            targets[identifier] = owner.Target(identifier, PLAYER)
            self.assertEqual(instance.evaluate(config(value)), expr.Entity(PLAYER))
            self.assertEqual(events, [("lookup", identifier)])

    def test_retirement_or_replacement_during_lookup_rejects_output(self):
        for mode in ("retire", "replace"):
            world, _, instance, _, _, _ = setup()

            def lookup(identifier, mode=mode, world=world):
                if mode == "retire":
                    world.context.retired = True
                else:
                    world.context = owner.Context(BODY, True, True)
                return owner.Target(identifier, PLAYER)

            world.context.lookup = lookup
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(expr.Entity(BODY)))

    def test_invalid_target_or_missing_subscription_rejects_output(self):
        for mode in ("target", "subscription"):
            world, _, instance, state, targets, _ = setup()
            targets[BODY] = owner.Target(PLAYER if mode == "target" else BODY, PLAYER)
            if mode == "subscription":
                world.context.watch_owner = NotImplemented
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(expr.Entity(BODY)), state)
