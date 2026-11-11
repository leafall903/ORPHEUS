"""Native member identifier width and distinct entity script namespaces."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph

from ow174.game.script import expr, runtime


def member_config(key, opcode=15):
    value = ({"$": "STUConfigVarInt", "m_value": key} if opcode == 15 else {
        "$": "STUConfigVarIdentifier", "m_identifier": f"0x{key & 0xFFFFFFFFFFFFFFFF:016X}"})
    return {"$": "STUConfigVarExpression", "m_expression": {
        "$": "STUConfigVarExpressionData", "m_opcodes": [14, 0, 14, 1, opcode, 0]},
        "m_configVars": [{"$": "declared_target"}, value]}


class MemberTests(unittest.TestCase):
    def instance(self):
        component = runtime.Component(100, world=runtime.World(1, 1))
        return component.create(make_graph(1), init=False)

    def test_member_key_uses_native_uint16_boundary(self):
        instance = self.instance()
        world = instance.component.world
        for opcode in (15, 16):
            for key, expected in ((14695, 14695), (65537, 1), (-1, 65535), (65536, 0), (0x1FFFF, 65535)):
                with self.subTest(opcode=opcode, key=key):
                    slot = runtime.Var((102, expected))
                    slot.base = True
                    with (
                        patch.object(world, "native", return_value=expr.Entity(102)),
                        patch.object(world, "entity_var_slot", return_value=slot) as lookup,
                    ):
                        self.assertTrue(instance.evaluate(member_config(key, opcode)))
                        lookup.assert_called_once_with(102, expected, create=False)

    def test_controller_and_body_member_slots_remain_independent(self):
        instance = self.instance()
        controller, body = runtime.Var((102, 14695)), runtime.Var((103, 14695))
        controller.base, body.base = False, True

        def lookup(entity, identifier, *, create=False):
            return {102: controller, 103: body}.get(entity)

        with patch.object(instance.component.world, "entity_var_slot", side_effect=lookup):
            for entity, expected in ((102, False), (103, True), (104, None)):
                with patch.object(instance.component.world, "native", return_value=expr.Entity(entity)):
                    self.assertIs(instance.evaluate(member_config(14695)), expected)

    def test_invalid_entity_never_borrows_another_namespace(self):
        instance = self.instance()
        for entity in (0, 0x1FFFFFFF, 0xFFFFFFFF):
            with (self.subTest(entity=entity),
                  patch.object(instance.component.world, "native", return_value=expr.Entity(entity)),
                  patch.object(instance.component.world, "entity_var_slot") as lookup):
                self.assertIsNone(instance.evaluate(member_config(14695)))
                lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
