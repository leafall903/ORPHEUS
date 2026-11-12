"""Authored AI difficulty query and explicit scene binding lifetimes."""

import unittest
from unittest.mock import patch

from ow174.game.script import expr, graph, runtime, scene
from tests.test_script_runtime_budget import graph as fixture_graph

DEFINITION = 0x0400000000001D41
NORMAL = 0x0D8000000000195F
HARD = 0x0D80000000001960
RECORD = {"ai_difficulty_profiles": {
    f"0x{NORMAL:016X}": "0x0B50000000000068",
    f"0x{HARD:016X}": "0x0B5000000000006E",
}}


class AIDifficultyTests(unittest.TestCase):
    def caller(self, world, entity):
        return runtime.Component(entity, world=world, strict=True).create(fixture_graph(), init=False)

    def test_difficulty_query_uses_the_selected_entity_and_retires_its_binding(self):
        world = scene.SceneWorld()
        world.bind(123)
        world.bind(456)
        config = {"$": "STU_C3619A38", "m_F52607FF": {"$": "STU_B5A0CAF0"}}
        with patch.object(graph, "entity_body", return_value=RECORD):
            with self.assertRaisesRegex(ValueError, "entity must already be bound"):
                world.set_ai_difficulty(789, DEFINITION, NORMAL)
            with self.assertRaisesRegex(ValueError, "not authored"):
                world.set_ai_difficulty(123, DEFINITION, 0x0B50000000000068)
            world.set_ai_difficulty(123, DEFINITION, NORMAL)
            world.set_ai_difficulty(456, DEFINITION, HARD)
        self.assertEqual(self.caller(world, 123).evaluate(config), expr.Asset(NORMAL))
        self.assertEqual(self.caller(world, 456).evaluate(config), expr.Asset(HARD))
        caller = self.caller(world, 123)
        caller.write((0, 7), expr.Entity(456))
        selected = {"$": "STUConfigVarDynamic", "m_identifier": 7, "m_60DB8F99": 0}
        self.assertEqual(caller.evaluate(dict(config, m_F52607FF=selected)), expr.Asset(HARD))
        world.unbind(456)
        world.bind(456)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_C3619A38"):
            self.caller(world, 456).evaluate(config)
        self.assertEqual(self.caller(world, 123).evaluate(config), expr.Asset(NORMAL))

    def test_mode_query_resolves_only_an_explicitly_bound_live_mode_component(self):
        world = scene.SceneWorld()
        config = {"$": "STU_A3DEF368"}
        mode = runtime.GAME_MODE_ENTITY
        world.bind(mode)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_A3DEF368"):
            self.caller(world, 123).evaluate(config)
        component = runtime.Component(mode, world=world)
        world.bind_script_component(mode, component)
        self.assertEqual(self.caller(world, 123).evaluate(config), expr.Entity(mode))
        component.set(6751, expr.Asset(NORMAL))
        self.assertEqual(world.entity_var(mode, 6751), expr.Asset(NORMAL))
        world.unbind_script_component(mode)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_A3DEF368"):
            self.caller(world, 123).evaluate(config)
