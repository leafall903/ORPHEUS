"""Declared nonzero early-input windows retain the actual original press time."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import nodes, runtime


class AbilityBufferTests(unittest.TestCase):
    def ability(self):
        component = runtime.Component(100, world=runtime.World(1, 1))
        component.set(600, 1.0)
        definition = make_graph(1, [node(0, "STUStatescriptStateAbility", state=0, fields={
            "m_A5F1A77B": {"$": "STUConfigVarInt", "m_value": 1},
            "m_246438AD": {"$": "STUConfigVarFloat", "m_value": 0.1},
            "m_out_CooldownVar": {"$": "STUConfigVarDynamic", "m_identifier": 600, "m_60DB8F99": 1},
        })], states=[0])
        instance = component.create(definition, init=False)
        instance.init()
        ability = instance.node_state(instance.graph.node(0))
        self.assertIsInstance(ability, nodes.Ability)
        ability.begin()
        return component, ability

    def test_early_released_press_does_not_cast_after_cooldown(self):
        component, ability = self.ability()
        ability.button(1, True)
        original = tuple(ability.presses)
        for frame in range(1, 10):
            component.run_frame(frame)
        self.assertEqual(tuple(ability.presses), original)
        for frame in range(10, 80):
            component.run_frame(frame)
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(ability.flags & ability.RUNNING)
        ability.button(1, True)
        component.run_frame(80)
        self.assertTrue(ability.flags & ability.RUNNING)

    def test_short_press_within_declared_window_casts_at_expiry(self):
        component, ability = self.ability()
        for frame in range(1, 59):
            component.run_frame(frame)
        ability.button(1, True)
        for frame in range(59, 70):
            component.run_frame(frame)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(ability.presses, [])


if __name__ == "__main__":
    unittest.main()
