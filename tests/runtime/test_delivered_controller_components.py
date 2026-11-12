"""Native component predicate and scoped Match relationships, not NPC AI acceptance."""

import unittest

import test_pharah_combat as primary
from test_game_graph_analysis import make_graph

from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, runtime
from ow174.game.script.controller_query import CLASS, matches

SELF_QUERY = {"$": CLASS, "m_entity": {"$": "STU_B5A0CAF0"}}


class ResolvedWorld(runtime.World):
    def controller_components(self, instance, entity):
        return self.resolved.get(entity, NotImplemented)


class ControllerQueryTests(unittest.TestCase):
    def test_native_component75_and_not92_truth_table(self):
        for components, expected in ((None, False), (frozenset(), False),
                                     (frozenset({75}), True), (frozenset({92}), False),
                                     (frozenset({75, 92}), False),
                                     (frozenset({0, 44, 51, 75, 255}), True)):
            with self.subTest(components=components):
                self.assertIs(matches(components), expected)

    def test_unknown_or_malformed_census_is_not_known_absence(self):
        for components in (NotImplemented, set(), frozenset({True}), frozenset({256})):
            with self.subTest(components=components), self.assertRaises(ValueError):
                matches(components)

    def test_strict_query_uses_the_supplied_selected_controller_and_typed_false(self):
        world = ResolvedWorld()
        world.resolved = {123: frozenset({75}), 456: frozenset({75, 92}), 789: None}
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(make_graph(1))
        self.assertIs(instance.evaluate(SELF_QUERY), True)
        for entity in (456, 789):
            component.set(17, expr.Entity(entity))
            cfg = {"$": CLASS, "m_entity": {"$": "STUConfigVarDynamic", "m_identifier": 17,
                                           "m_60DB8F99": 1}}
            self.assertIs(instance.evaluate(cfg), False)

    def test_strict_query_refuses_unbound_relation_instead_of_forcing_boolean(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(make_graph(1))
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(SELF_QUERY)

    def test_resolved_callback_cannot_return_a_result_after_retiring_its_source(self):
        class RetiringWorld(runtime.World):
            def controller_components(self, instance, entity):
                instance.component.destroy(instance)
                return frozenset({75})

        component = runtime.Component(123, world=RetiringWorld(), strict=True)
        instance = component.create(make_graph(1))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "source lifecycle"):
            instance.evaluate(SELF_QUERY)

    def test_actual_match_owner_has_controller75_but_foreign_body_remains_unbound(self):
        run, owner, enemy = primary.PharahCombatTests().encounter()
        instance = next(iter(owner.body_script.component.instances.values()))
        self.assertIn(75, run.match._possession(owner))
        self.assertNotIn(92, run.match._possession(owner))
        self.assertIs(instance.evaluate(SELF_QUERY), True)
        instance.component.set(17, expr.Entity(enemy.body))
        self.assertIsNone(instance.evaluate({"$": CLASS, "m_entity": {
            "$": "STUConfigVarDynamic", "m_identifier": 17, "m_60DB8F99": 1}}))
        # The local reconstruction is intentionally not accepted as the strict
        # native component43/DA6910/cache/observer service.
        instance.component.strict = True
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(SELF_QUERY)

    def test_actual_hero_switch_retires_old_mapping_and_binds_only_new_body(self):
        run, owner, _ = primary.PharahCombatTests().encounter()
        old = owner.body_script
        instance = next(iter(old.component.instances.values()))
        self.assertIs(instance.evaluate(SELF_QUERY), True)
        run.match.switch_hero(owner, heroes()[SOLDIER])
        self.assertTrue(old.retired)
        self.assertIsNone(old.world.match)
        self.assertIsNone(instance.evaluate(SELF_QUERY))
        new = next(iter(owner.body_script.component.instances.values()))
        self.assertNotEqual(new.component.entity, instance.component.entity)
        self.assertIs(new.evaluate(SELF_QUERY), True)


if __name__ == "__main__":
    unittest.main()
