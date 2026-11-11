"""Native basic classification mask and effective-mode query contracts."""
import unittest

from test_game_graph_analysis import make_graph

from ow174.game.content import PRACTICE_RANGE_MODE, TUTORIAL_MODE
from ow174.game.script import entity_filter, expr, runtime, scene


class EntityFilterTests(unittest.TestCase):
    def test_wildcard_exclusion_and_unknown_classification_are_distinct(self):
        world = scene.SceneWorld()
        world.bind(123)
        instance = runtime.Component(123, world=world, strict=True).create(make_graph(0xF001))
        cfg = {
            "$": entity_filter.CLASS,
            "m_entity": expr.Entity(123),
            "m_filter": {"$": "STUConfigVarFilter", "m_84065FD1": entity_filter.WILDCARD,
                         "m_4EA686DB": 0x100},
        }
        self.assertIs(entity_filter.evaluate(instance.context(), cfg), NotImplemented)
        for bits, expected in ((0, True), (0x100, False)):
            world.set_entity_filter_record(123, entity_filter.Record(bits, False, False))
            self.assertIs(instance.evaluate(cfg), expected)
        world.unbind(123)
        self.assertIs(instance.evaluate(cfg), False)
        cfg["m_filter"] = None
        self.assertIs(instance.evaluate(cfg), True)

    def test_narrow_masks_require_97_and_all_required_classification_bits(self):
        world = scene.SceneWorld()
        world.bind(123)
        instance = runtime.Component(123, world=world, strict=True).create(make_graph(0xF001))
        cfg = {
            "$": entity_filter.CLASS,
            "m_entity": expr.Entity(123),
            "m_filter": {"$": "STUConfigVarFilter", "m_84065FD1": 0x100, "m_1E14CCA8": 0x200},
        }
        for bits, component97, extension_rejects, expected in (
                (0x300, False, False, False), (0x100, True, False, False),
                (0x300, True, True, False), (0x300, True, False, True)):
            world.set_entity_filter_record(123, entity_filter.Record(bits, component97, extension_rejects))
            self.assertIs(instance.evaluate(cfg), expected)

    def test_mode_predicate_compares_the_explicit_effective_mode(self):
        for mode, expected in ((TUTORIAL_MODE, True), (PRACTICE_RANGE_MODE, False)):
            world = scene.SceneWorld(mode=mode)
            instance = runtime.Component(123, world=world, strict=True).create(make_graph(0xF001))
            self.assertIs(instance.evaluate({"$": "STU_A77AC2AC"}), expected)
            self.assertIs(instance.evaluate({"$": "STU_4A101787"}, initial=True), False)

    def test_nested_condition_uses_candidate_and_restores_enclosing_parameter(self):
        world = scene.SceneWorld()
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(make_graph(0xF001))
        for entity in (123, 456):
            world.bind(entity)
            world.set_entity_filter_record(entity, entity_filter.Record(1, True, False))
        condition = {
            "$": "STU_919FD47C",
            "m_configVars": [expr.Entity(123)],
            "m_expression": {"m_opcodes": [13, 0, 14, 0, 37, 0],
                             "m_dynamicVars": [{"m_identifier": 8}]},
        }
        filt = {"$": "STUConfigVarFilter", "m_84065FD1": 1, "m_condition": condition}
        cfg = {"$": entity_filter.CLASS, "m_entity": expr.Entity(123), "m_filter": filt}
        with component.parameter_scope({8: expr.Entity(456)}):
            self.assertIs(instance.evaluate(cfg), True)
            self.assertIs(instance.evaluate({**cfg, "m_entity": expr.Entity(456)}), False)
            self.assertEqual(instance.context().param(8), expr.Entity(456))

        filt["m_condition"] = {"$": "unsupported_condition"}
        world.set_entity_filter_record(123, entity_filter.Record(0, True, False))
        self.assertIs(instance.evaluate(cfg), False)

    def test_nested_condition_defaults_to_true_on_no_scalar_or_empty_array(self):
        world = scene.SceneWorld()
        world.bind(123)
        world.set_entity_filter_record(123, entity_filter.Record(0, False, False))
        instance = runtime.Component(123, world=world, strict=True).create(make_graph(0xF001))
        filt = {"$": "STUConfigVarFilter"}
        cfg = {"$": entity_filter.CLASS, "m_entity": expr.Entity(123), "m_filter": filt}
        for condition in ({"$": "STU_06610EBC", "m_configVars": []}, ()):
            with self.subTest(condition=condition):
                filt["m_condition"] = condition
                self.assertIs(instance.evaluate(cfg), True)
