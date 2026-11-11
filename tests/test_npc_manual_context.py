"""Declared manual context scope and causal storage; no native allocator claim."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_graph_analysis import make_graph

from ow174.game import npc_context
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import expr, runtime
from ow174.game.script.bot import BotWorld


class ManualEnforcerContextTests(unittest.TestCase):
    def scene(self, *, hero=npc_context.ENFORCER, graph=0x13EB, declared=(0x4B,), strict=False):
        world = BotWorld(hero, 0, {}, controller_components=declared)
        component = runtime.Component(123, world=world, strict=strict)
        instance = component.create(make_graph(graph))
        return world, component, instance

    def test_declared_component_sets_match_the_predicate_without_allocating_a_controller(self):
        for values in (None, (), (0x4B,), (0x5C,), (0x4B, 0x5C),
                       (1, 0x4B), (0x4A, 0x5B), (0x4B, 0x5B, 0x5D)):
            with self.subTest(values=values):
                expected = values is not None and 0x4B in values and 0x5C not in values
                self.assertEqual(npc_context.controller_predicate(values), expected)

    def test_only_actual_manual_enforcer_spawn_policy_declares_components(self):
        for hero in (npc_context.ENFORCER, 0x02E00000000001BB, 0x02E000000000012F):
            for manual in (False, True):
                bot = SimpleNamespace(hero=hero, spawner={'manual_spawn': manual})
                expected = (0x4B,) if manual and hero in (npc_context.ENFORCER, 0x02E00000000001BB) else None
                self.assertEqual(npc_context.manual_components(bot), expected)

    def test_owner_only_weapon_graph_query_refuses_strict_foreign_and_undeclared_contexts(self):
        owner = {'$': 'STU_B96D3A98', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        world, _, instance = self.scene()
        self.assertIs(npc_context.native(world, owner, instance), True)
        for config in ({'declared': None}, {'graph': 0x13EC}, {'hero': 0}, {'strict': True}):
            with self.subTest(config=config):
                world, _, instance = self.scene(**config)
                self.assertIs(npc_context.native(world, owner, instance), NotImplemented)
        world, _, instance = self.scene()
        instance.write((0, 77), expr.Entity(999))
        foreign = {'$': 'STU_B96D3A98', 'm_entity': {
            '$': 'STUConfigVarDynamic', 'm_identifier': 77, 'm_60DB8F99': 0}}
        self.assertIs(npc_context.native(world, foreign, instance), NotImplemented)
        self.assertIs(npc_context.native(world, {'$': 'STU_F7262B36'}, instance), NotImplemented)

    def test_controller_declaration_is_vm_causal_input_without_service_evaluation(self):
        world, component, _ = self.scene()
        first = json.dumps(export_component_vm(component), sort_keys=True)
        world.controller_components = (0x4B, 0x5C)  # mechanical input mutation, not combat acceptance
        with patch.object(BotWorld, 'native', side_effect=AssertionError('capture cannot execute services')):
            second = json.dumps(export_component_vm(component), sort_keys=True)
        self.assertNotEqual(first, second)
