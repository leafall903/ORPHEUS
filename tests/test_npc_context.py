"""Scoped declared controller semantics, including causal VM snapshot storage."""

import unittest
from types import SimpleNamespace

from test_game_graph_analysis import make_graph

from ow174.game import npc_context
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import runtime
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import OWNER_HEALTH


class NpcContextTests(unittest.TestCase):
    def arena(self, components=(75,), hero=npc_context.ENFORCER, index=npc_context.WEAPON):
        world = BotWorld(hero, 0, {"0": 20}, controller_components=components)
        component = runtime.Component(0xC0001010, world=world)
        component.set(OWNER_HEALTH, 350.0)
        instance = component.create(make_graph(index), init=False)
        return world, component, instance

    def query(self, cls, entity=None):
        return {"$": cls, "m_entity": entity or {"$": "STU_B5A0CAF0"}}

    def test_predicate_requires75_and_excludes92(self):
        for components, expected in ((None, False), ((), False), ((75,), True), ((92,), False),
                                     ((75, 92), False), ((43, 75), True)):
            world, _, instance = self.arena(components)
            self.assertEqual(npc_context.controller_predicate(components), expected)
            self.assertIs(npc_context.native(world, self.query("STU_B96D3A98"), instance),
                          NotImplemented if components is None else expected)

    def test_manual_declaration_is_scoped_to_supported_enforcer_and_assassin(self):
        for hero, manual, expected in ((npc_context.ENFORCER, True, (75,)),
                                       (npc_context.ENFORCER, 1, None),
                                       (npc_context.ENFORCER, False, None),
                                       (npc_context.ASSASSIN, True, (75,)), (0, True, None)):
            self.assertEqual(npc_context.manual_components(
                SimpleNamespace(hero=hero, spawner={"manual_spawn": manual})), expected)

    def test_owner_health_adapter_tracks_damage_and_death(self):
        world, component, instance = self.arena()
        for health in (350.0, 315.0, 0.0):
            component.set(OWNER_HEALTH, health)
            self.assertEqual(world.native(self.query("STU_9B7648C3"), instance), health)

    def test_other_contexts_and_foreign_entities_remain_unresolved(self):
        for kwargs in ({"components": None}, {"hero": 0}, {"index": 1}):
            world, _, instance = self.arena(**kwargs)
            self.assertIs(npc_context.native(world, self.query("STU_B96D3A98"), instance), NotImplemented)
        world, _, instance = self.arena()
        self.assertIs(npc_context.native(world, self.query("STU_F7262B36"), instance), NotImplemented)
        cfg = self.query("STU_B96D3A98", {"$": "STUConfigVarInt", "m_value": 99})
        self.assertIs(npc_context.native(world, cfg, instance), NotImplemented)

    def test_controller_components_are_causal_snapshot_state(self):
        world, component, _ = self.arena()
        before = snapshot_digest(export_component_vm(component))
        world.controller_components = (75, 92)
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        world.controller_components = (75,)
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
