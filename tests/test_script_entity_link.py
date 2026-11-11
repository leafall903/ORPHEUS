"""CE2CF0/D1BEE0 linked identity semantics; no inferred Match field producer."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import entity_link as link
from ow174.game.script import expr, runtime

BODY, OTHER = 0xA0000101, 0xA0000001


class ValueWorld(runtime.World):
    context = NotImplemented

    def entity_link_context(self, entity):
        return self.context

    def native(self, cfg, instance):
        if cfg.get("$") == "Literal":
            return cfg["value"]
        if cfg.get("$") == "Failed":
            return expr.EVALUATION_FAILED
        return super().native(cfg, instance)


def setup():
    world = ValueWorld()
    component = runtime.Component(BODY, world=world, strict=True)
    instance = component.create(make_graph(0xFFFF, [node(0, state=0)], states=[0]), init=False)
    state = instance.state(0)
    targets, events = {}, []
    world.context = link.Context(BODY, True, True,
        lookup=lambda identifier: events.append(("lookup", identifier)) or targets.get(identifier),
        watch_environment=lambda watcher, channel: events.append(("environment", channel)),
        watch_link=lambda watcher, target: events.append(("link", target.linked)))
    return world, component, instance, state, targets, events


def config(value):
    return {"$": link.CLASS, "m_D63CBEC2": {"$": "Literal", "value": value}}


class EntityLinkTests(unittest.TestCase):
    def test_unknown_host_does_not_infer_link_from_ownership(self):
        world, _, instance, _, _, _ = setup()
        world.context = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(expr.Entity(BODY)))

    def test_missing_instance_preserves_destination_without_evaluating_selector(self):
        world, _, instance, state, _, events = setup()
        world.context.instance_present = False
        cfg = {"$": link.CLASS, "m_D63CBEC2": {"$": "Unknown"}}
        self.assertEqual(instance.evaluate(cfg, state, initial=99), 99)
        self.assertEqual(events, [])

    def test_missing_entity_writes_typed_null_and_observes_selected_environment(self):
        for environment in (False, True):
            world, _, instance, state, _, events = setup()
            world.context.environment_present = environment
            self.assertEqual(instance.evaluate(config(expr.Entity(BODY)), state, initial=99), expr.Entity(0))
            self.assertEqual(events, [("lookup", BODY), ("environment", 0 if environment else 46)])

    def test_missing_component_present_null_and_live_link_have_distinct_subscriptions(self):
        for linked in (None, 0, OTHER):
            _, _, instance, state, targets, events = setup()
            targets[BODY] = link.Target(BODY, linked)
            self.assertEqual(instance.evaluate(config(expr.Entity(BODY)), state, initial=99),
                             expr.Entity(linked or 0))
            self.assertEqual(events, [("lookup", BODY)] + ([] if linked is None else [("link", linked)]))

    def test_scalar_conversion_group_first_and_failed_selector_select_uint32_null(self):
        for value, identifier in ((BODY, 0), (True, 0), (expr.Asset(BODY), 0), (None, 0),
                                  (expr.Entity(BODY+(1 << 32)), BODY),
                                  (((expr.Entity(BODY),),), BODY), ((None, expr.Entity(BODY)), 0)):
            _, _, instance, _, targets, events = setup()
            targets[identifier] = link.Target(identifier, OTHER)
            self.assertEqual(instance.evaluate(config(value)), expr.Entity(OTHER))
            self.assertEqual(events, [("lookup", identifier)])
        _, _, instance, _, targets, _ = setup()
        targets[0] = link.Target(0, OTHER)
        cfg = {"$": link.CLASS, "m_D63CBEC2": {"$": "Failed"}}
        self.assertEqual(instance.evaluate(cfg), expr.Entity(OTHER))
        cfg["m_D63CBEC2"] = {"$": "STU_06610EBC", "m_configVars": [
            {"$": "Literal", "value": expr.Entity(0)}, {"$": "Unknown"}]}
        self.assertEqual(instance.evaluate(cfg), expr.Entity(OTHER))

    def test_context_retirement_during_lookup_rejects_result(self):
        world, _, instance, _, _, _ = setup()

        def lookup(identifier):
            world.context.retired = True
            return link.Target(identifier, OTHER)

        world.context.lookup = lookup
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(expr.Entity(BODY)))

    def test_mismatched_target_and_unknown_subscription_are_explicit_failures(self):
        for mode in ("target", "subscription"):
            world, _, instance, state, targets, _ = setup()
            targets[BODY] = link.Target(BODY, OTHER)
            if mode == "target":
                targets[BODY] = link.Target(OTHER, OTHER)
            else:
                world.context.watch_link = NotImplemented
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config(expr.Entity(BODY)), state)


if __name__ == "__main__":
    unittest.main()
