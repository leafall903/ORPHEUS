"""Direct body-owner query guards, typed results and live match ownership."""

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
    world.context = owner.Context(BODY, True, True,
        lookup=lambda identifier: events.append(("lookup", identifier)) or targets.get(identifier),
        watch_environment=lambda watcher, kind: events.append(("environment", kind)),
        watch_owner=lambda watcher, target: events.append(("owner", target.current)))
    return world, component, instance, state, targets, events


def config(value):
    return {"$": owner.CLASS, "m_801B298F": {"$": "Literal", "value": value}}


class BodyOwnerTests(unittest.TestCase):
    def test_unknown_context_stays_strict(self):
        world, _, instance, _, _, _ = setup()
        world.context = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(expr.Entity(BODY)))

    def test_missing_instance_skips_selector_and_preserves_destination(self):
        world, _, instance, state, _, events = setup()
        world.context.instance_present = False
        cfg = {"$": owner.CLASS, "m_801B298F": {"$": "Unknown"}}
        self.assertEqual(instance.evaluate(cfg, state, initial=99), 99)
        self.assertEqual(events, [])

    def test_missing_body_preserves_destination_and_subscribes_to_environment(self):
        for present in (True, False):
            world, _, instance, state, _, events = setup()
            world.context.environment_present = present
            self.assertEqual(instance.evaluate(config(expr.Entity(BODY)), state, initial=99), 99)
            self.assertEqual(events, [("lookup", BODY), ("environment", 0 if present else 46)])

    def test_absent_component_and_present_null_have_distinct_subscriptions(self):
        for current in (None, 0, PLAYER):
            _, _, instance, state, targets, events = setup()
            targets[BODY] = owner.Target(BODY, current)
            self.assertEqual(instance.evaluate(config(expr.Entity(BODY)), state), expr.Entity(current or 0))
            self.assertEqual(events, [("lookup", BODY)] + ([] if current is None else [("owner", current)]))

    def test_scalar_and_array_conversion_do_not_treat_plain_integers_as_entities(self):
        for value, identifier in ((BODY, 0), (True, 0), (expr.Asset(BODY), 0),
                                  (None, 0), (expr.Entity(BODY + (1 << 32)), BODY),
                                  (((expr.Entity(BODY),),), BODY), ((None, expr.Entity(BODY)), 0)):
            _, _, instance, _, targets, events = setup()
            targets[identifier] = owner.Target(identifier, PLAYER)
            self.assertEqual(instance.evaluate(config(value)), expr.Entity(PLAYER))
            self.assertEqual(events, [("lookup", identifier)])

    def test_retirement_during_lookup_rejects_result(self):
        world, _, instance, _, _, _ = setup()

        def lookup(identifier):
            world.context.retired = True
            return owner.Target(identifier, PLAYER)

        world.context.lookup = lookup
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(expr.Entity(BODY)))

    def test_malformed_target_and_unknown_subscription_do_not_silently_succeed(self):
        for mode in ("target", "subscription"):
            world, _, instance, state, targets, _ = setup()
            targets[BODY] = owner.Target(BODY, PLAYER)
            if mode == "target":
                targets[BODY] = owner.Target(PLAYER, PLAYER)
            else:
                world.context.watch_owner = NotImplemented
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(expr.Entity(BODY)), state)


if __name__ == "__main__":
    unittest.main()
