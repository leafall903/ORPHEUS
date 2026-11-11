"""Typed manual controller namespace; no native controller/wire equivalence claim."""

import unittest
from unittest.mock import patch

from test_game_combat import Run
from test_game_graph_analysis import make_graph

from ow174.game import assassin, npc, npc_context
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, graph, runtime
from ow174.game.script.bot import BotScript, BotWorld


class AssassinContextTests(unittest.TestCase):
    def arena(self):
        world = BotWorld(assassin.HERO, 0, {"0": 20}, controller_components=(75,),
                         controller_entity=0xC0001001)
        component = runtime.Component(0xC0001000, world=world)
        instance = component.create(make_graph(assassin.GRAPH), init=False)
        return world, component, instance

    def test_possessor_is_entity_and_target_is_a_member_of_that_distinct_namespace(self):
        world, component, instance = self.arena()
        query = {"$": "STU_DB742151", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(instance.evaluate(query), expr.Entity(world.controller_entity))
        self.assertNotEqual(world.controller_entity, component.entity)
        self.assertIsNone(instance.context().member(world.controller_entity, 1650))
        world.manual_target = 0xA0000102
        self.assertEqual(instance.context().member(world.controller_entity, 1650), expr.Entity(0xA0000102))
        self.assertIsNone(instance.context().member(component.entity, 1650))
        self.assertEqual(world.ai_weapon_button(0), 36)

    def test_foreign_possessor_and_unscoped_queries_remain_unsupported(self):
        world, _, instance = self.arena()
        self.assertIs(assassin.native(world, {"$": "STU_DB742151", "m_entity": expr.Entity(0xA0000102)},
                                      instance), NotImplemented)
        instance.graph = make_graph(assassin.GRAPH+1)
        self.assertIs(assassin.native(world, {"$": "STU_DB742151", "m_entity": {"$": "STU_B5A0CAF0"}},
                                      instance), NotImplemented)

    def test_invalid_or_aliased_controller_declarations_are_rejected(self):
        for controller in (0, -1, 0x100000000, 0xDFFFFFFF, True):
            with self.subTest(controller=controller), self.assertRaises(ValueError):
                BotWorld(assassin.HERO, 0, {}, controller_components=(75,), controller_entity=controller)
        with patch.object(graph, "entity_body", return_value={}), self.assertRaisesRegex(ValueError, "alias"):
            BotScript(0x0400000000001EBD, assassin.HERO, 0xC0001000, 0, 0, 500,
                      controller_components=(75,), controller_entity=0xC0001000)

    def test_controller_identity_and_target_are_causal_vm_snapshot_inputs(self):
        world, component, _ = self.arena()
        before = snapshot_digest(export_component_vm(component))
        world.manual_target = 0xA0000102
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        world.manual_target = None
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        world.controller_entity = 0xC0001010
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)

    def test_spawn_reserves_controller_from_same_allocator_and_rejects_collisions_or_exhaustion(self):
        run = Run()
        sample = run.match.bots[0]
        definition = npc.Definition(assassin.HERO, sample.body, "Allocator fixture", 500)
        with patch.object(graph, "entity_body", return_value=None):
            first = npc.spawn(run.match, definition, (0, 0, 0), team=1)
            controller = first.spawner["manual_controller"]
            second = npc.spawn(run.match, definition, (0, 0, 0), team=1)
            self.assertEqual(controller, first.entity+1)
            self.assertEqual(second.entity, controller+1)
            run.match.combat.next_bot = controller
            with self.assertRaisesRegex(ValueError, "collided"):
                npc.spawn(run.match, definition, (0, 0, 0), team=1)
            run.match.combat.next_bot = 0xCFFFFFFF
            with self.assertRaisesRegex(ValueError, "exhausted"):
                npc.spawn(run.match, definition, (0, 0, 0), team=1)
        self.assertEqual(npc_context.manual_components(first), (75,))
