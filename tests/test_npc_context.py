"""Explicit manual-controller policy and its scoped query/snapshot boundary."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_graph_analysis import make_graph

from ow174.game import npc_context
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, runtime
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import OWNER_HEALTH


class NpcContextTests(unittest.TestCase):
    def arena(self, components=(75,), hero=npc_context.ENFORCER, index=npc_context.WEAPON):
        world = BotWorld(hero, 0, {"0": 20}, controller_components=components)
        component = runtime.Component(0xC0001010, world=world)
        component.set(OWNER_HEALTH, 350.0)
        instance = component.create(make_graph(index), init=False)
        return world, component, instance

    def query(self, cls, **kwargs):
        return {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}, **kwargs}

    def test_bitset_predicate_distinguishes_missing_and_disallowed_controller(self):
        for components, expected in ((None, False), ((), False), ((75,), True), ((92,), False),
                                     ((75, 92), False), ((43, 44, 75), True)):
            with self.subTest(components=components):
                self.assertEqual(npc_context.controller_predicate(components), expected)
                world, _, instance = self.arena(components=components)
                value = npc_context.native(world, self.query("STU_B96D3A98"), instance)
                self.assertIs(value, NotImplemented if components is None else expected)

    def test_only_manual_enforcer_and_assassin_declare_the_sandbox_controller(self):
        for hero, spawner, expected in ((npc_context.ENFORCER, {"manual_spawn": True}, (75,)),
                                        (0x02E00000000001BB, {"manual_spawn": True}, (75,)),
                                        (npc_context.ENFORCER, {}, None),
                                        (npc_context.ENFORCER, {"manual_spawn": 1}, None),
                                        (0, {"manual_spawn": True}, None)):
            self.assertEqual(npc_context.manual_components(SimpleNamespace(hero=hero, spawner=spawner)),
                             expected)

    def test_assassin_button_routes_only_with_explicit_manual_context(self):
        for components, expected in ((None, 20), ((75,), 36)):
            world, _, _ = self.arena(components=components, hero=0x02E00000000001BB)
            self.assertEqual(world.ai_weapon_button(0), expected)
            self.assertIsNone(world.ai_weapon_button(1))

    def test_health_follows_authoritative_owner_slot_and_missing_owner_stays_unknown(self):
        world, component, instance = self.arena()
        cfg = self.query("STU_9B7648C3")
        self.assertEqual(world.native(cfg, instance), 350.0)
        component.set(OWNER_HEALTH, 0.0)
        self.assertEqual(world.native(cfg, instance), 0.0)
        del component.vars[OWNER_HEALTH]
        self.assertIsNone(world.native(cfg, instance))

    def test_foreign_entity_other_hero_graph_and_queries_keep_unknown_service(self):
        for kwargs in ({"components": None}, {"hero": 0}, {"index": 1}):
            world, _, instance = self.arena(**kwargs)
            self.assertIs(npc_context.native(world, self.query("STU_B96D3A98"), instance), NotImplemented)
        world, _, instance = self.arena()
        foreign = self.query("STU_B96D3A98", m_entity={"$": "STUConfigVarDynamic", "m_identifier": 536})
        instance.write((expr.INSTANCE, 536), expr.Entity(0xC0001111))
        self.assertIs(npc_context.native(world, foreign, instance), NotImplemented)
        self.assertIs(npc_context.native(world, self.query("STU_F7262B36"), instance), NotImplemented)
        self.assertIs(npc_context.native(world, self.query("STU_B96D3A98"), None), NotImplemented)

    def test_context_components_are_causal_vm_snapshot_input(self):
        world, component, _ = self.arena()
        before = snapshot_digest(export_component_vm(component))
        world.controller_components = (75, 92)
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        world.controller_components = (75,)
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)

    def test_manual_target_and_pin_registry_change_causal_snapshots(self):
        import test_server_snapshot as snapshot_tests

        from ow174.game import server_snapshot

        world, component, _ = self.arena(hero=0x02E00000000001BB, index=0x1157)
        before = snapshot_digest(export_component_vm(component))
        world.manual_target = 0xC0000201
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        world.manual_target = None
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        fight = players[0].match.combat
        before = server_snapshot.snapshot_digest(case.capture(session))
        fight.pins[0xC0000201] = players[0].body
        self.assertNotEqual(server_snapshot.snapshot_digest(case.capture(session)), before)
        fight.pins.clear()
        self.assertEqual(server_snapshot.snapshot_digest(case.capture(session)), before)

    def test_manual_spawn_synchronizes_full_aggregate_after_pool_creation(self):
        from test_game_combat import Run

        from ow174.game import npc

        run = Run()
        sample = run.match.bots[0]
        definition = npc.Definition(npc_context.ENFORCER, sample.body, 'Pool fixture', 300, 100, 50)
        with patch('ow174.game.script.graph.entity_body', return_value={'graphs': []}):
            bot = npc.spawn(run.match, definition, run.players[0].mover.position, team=1)
        component = bot.body_script.component
        self.assertEqual(component.entity_var(OWNER_HEALTH).value(), 450)
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 450)

    def test_match_preparation_passes_policy_only_to_manual_enforcer(self):
        from test_game_combat import Run

        run = Run()
        bot = run.match.bots[0]
        with (patch("ow174.game.match.BotScript") as prepare,
              patch("ow174.game.match.graph.entity_body", return_value={"graphs": []})):
            bot.hero = npc_context.ENFORCER
            # Preserve shared spawner data: this fixture owns its replacement.
            bot.spawner = {"manual_spawn": True}
            run.match._prepare_bot_script(bot)
            self.assertEqual(prepare.call_args.kwargs["controller_components"], (75,))
            bot.spawner = {}
            run.match._prepare_bot_script(bot)
            self.assertNotIn("controller_components", prepare.call_args.kwargs)
