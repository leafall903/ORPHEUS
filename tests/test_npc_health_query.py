"""Bounded NPC owner-health adapter; foreign health services are not inferred."""

import unittest

from test_game_graph_analysis import make_graph

from ow174.game.script import expr, runtime
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import OWNER_HEALTH


class NpcOwnerHealthTests(unittest.TestCase):
    def scene(self):
        component = runtime.Component(0xA0000101, world=BotWorld(0, 0, {}), strict=True)
        instance = component.create(make_graph(0x7F02), init=False)
        return component, instance

    def test_current_aggregate_and_zero_are_read_as_float32(self):
        component, instance = self.scene()
        cfg = {'$': 'STU_9B7648C3', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        for value in (350, 17.3, 0):
            with self.subTest(value=value):
                component.set(OWNER_HEALTH, value)
                self.assertEqual(instance.evaluate(cfg), expr.f32(value))

    def test_missing_aggregate_is_not_fabricated_alive(self):
        _, instance = self.scene()
        cfg = {'$': 'STU_9B7648C3', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        self.assertIsNone(instance.evaluate(cfg))

    def test_foreign_lookup_does_not_alias_owner_health(self):
        component, instance = self.scene()
        component.set(OWNER_HEALTH, 350)
        component.set(99, expr.Entity(0xC0000051))
        cfg = {'$': 'STU_9B7648C3', 'm_entity': {
            '$': 'STUConfigVarDynamic', 'm_identifier': 99, 'm_60DB8F99': expr.ENTITY,
        }}
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(cfg)


if __name__ == '__main__':
    unittest.main()
