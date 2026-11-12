"""Explicit manual gate policy, not controller allocation or native query emulation."""
import unittest
from types import SimpleNamespace

from test_game_graph_analysis import make_graph

from ow174.game import npc_context
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import controller_queries as query
from ow174.game.script import runtime
from ow174.game.script.bot import BotWorld


class EnforcerContextTests(unittest.TestCase):
    @staticmethod
    def config(cls='STU_B96D3A98'):
        return {'$': cls, 'm_801B298F' if cls == query.RELATED else 'm_entity': {'$': 'STU_B5A0CAF0'}}

    def instance(self, *, hero=npc_context.ENFORCER, components=(75,), graph=0x13EB):
        world = BotWorld(hero, 0, {}, controller_components=components)
        component = runtime.Component(0xA0000101, world=world)
        instance = component.create(make_graph(graph))
        return world, instance

    def test_only_supported_manual_heroes_receive_the_context(self):
        for hero, manual, expected in ((npc_context.ENFORCER, True, (75,)),
                                        (npc_context.ENFORCER, False, None),
                                        (0x02E00000000001BB, True, (75,)), (0, True, None)):
            with self.subTest(hero=hero, manual=manual):
                bot = SimpleNamespace(hero=hero, spawner={'manual_spawn': manual})
                self.assertEqual(npc_context.manual_components(bot), expected)

    def test_declared_tail_uses75_and_excludes92(self):
        for components, expected in ((None, False), ((), False), ((75,), True),
                                      ((44, 75), True), ((75, 92), False)):
            with self.subTest(components=components):
                self.assertIs(npc_context.controller_predicate(components), expected)

    def test_policy_is_limited_to_own_entity_enforcer_weapon_graph_and_known_query(self):
        for settings in ({}, {'hero': 0}, {'components': None}, {'graph': 0x13EC}):
            with self.subTest(settings=settings):
                world, instance = self.instance(**settings)
                result = npc_context.native(world, self.config(), instance)
                self.assertIs(result, True if not settings else NotImplemented)
        world, instance = self.instance()
        cfg = self.config()
        cfg['m_entity'] = {'$': 'STUConfigVarEntity', 'm_value': 0}
        self.assertIs(npc_context.native(world, cfg, instance), NotImplemented)
        self.assertIs(npc_context.native(world, self.config(query.RELATED), instance), NotImplemented)

    def test_native_relation_service_stays_unknown_while_declared_gate_is_available(self):
        _world, instance = self.instance()
        self.assertIs(query.evaluate(instance.context(), self.config()), NotImplemented)
        self.assertIs(query.evaluate(instance.context(), self.config(query.RELATED)), NotImplemented)
        self.assertIs(instance.evaluate(self.config()), True)

    def test_context_assignment_is_visible_in_vm_storage_without_execution(self):
        world, instance = self.instance()
        assigned = snapshot_digest(export_component_vm(instance.component))
        world.controller_components = (75, 92)
        self.assertNotEqual(snapshot_digest(export_component_vm(instance.component)), assigned)
        world.controller_components = None
        self.assertNotEqual(snapshot_digest(export_component_vm(instance.component)), assigned)
