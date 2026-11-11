"""Build 104319 difficulty routing probes for Roadhog's five NPC opponents.

The selected difficulty and body identity are declared services. These probes
execute the authored selector, entries, switches and amount expressions without
assuming how native HealthPool applies the returned operands.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game.script import expr, graph, nodes, runtime

DIFFICULTY = 9948
DIFFICULTY_SELECTION = 6751
CHOICES = (0x195E, 0x195F, 0x26DF, 0x1960)


CONDITIONAL = {
    "Trooper": (),
    "Heavy Assault": ((3, 0, (2000, 3000, 5000, 7000)),),
    "Assassin": ((4, 0, (0, 350, 600, 400)), (14, 2, (0, 0, 0, 300))),
    "Sniper": ((5, 0, (0, 150, 200, 100)), (8, 2, (0, 0, 100, 400))),
    "Enforcer": ((6, 0, (0, 150, 250, 250)), (7, 2, (0, 50, 100, 200))),
}


class ConditionalPoolWorld(runtime.World):
    def __init__(self, definition, mode, override=None):
        super().__init__(definition.hero, mode=0)
        self.definition = definition.body
        self.mode_component = mode
        self.override = override

    def native(self, cfg, instance):
        kind = cfg.get("$")
        if kind == "STU_1A3FA5B0":
            self.assert_owner(cfg, instance)
            return expr.Asset(self.definition)
        if kind == "STU_C3619A38":
            return self.override
        if kind == "STU_A3DEF368":
            return expr.Entity(runtime.GAME_MODE_ENTITY)
        return super().native(cfg, instance)

    def assert_owner(self, cfg, instance):
        if instance.evaluate(cfg["m_entity"]) != expr.Entity(instance.component.entity):
            raise AssertionError("pool definition query did not address the NPC owner")

    def entity_var(self, entity, var):
        if entity == runtime.GAME_MODE_ENTITY and var == DIFFICULTY:
            return self.mode_component.entity_var(var).value()
        return super().entity_var(entity, var)


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS") and os.environ.get("ORPHEUS_NPC_CATALOG"),
                     "actual prepared NPC graphs and base catalog required")
class RoadhogNpcPoolRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ow174.game import npc

        cls.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        edit = patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_NPC_GRAPHS"]))
        edit.start()
        cls.addClassCleanup(edit.stop)
        cls.addClassCleanup(cls.clear_graphs)
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()
        graph._data.cache_clear()

    def select_mode(self, tier):
        mode = runtime.Component(runtime.GAME_MODE_ENTITY)
        instance = mode.create(graph.graph(0x100B), init=False)
        mode.set(DIFFICULTY_SELECTION, expr.Asset(0x0D80000000000000 | CHOICES[tier]))
        nodes.action_switch(instance, instance.graph.node(77), None)
        self.assertEqual(mode.entity_var(DIFFICULTY).value(), tier)
        return mode

    def pool_nodes(self, definition, mode, override=None):
        body = runtime.Component(0xC0000020, world=ConditionalPoolWorld(definition, mode, override))
        record = graph.entity_body(definition.body)
        self.assertIn(0x143A, {root["graph"] for root in record["graphs"]})
        instance = body.create(graph.graph(0x143A))
        body.run_now()
        result = tuple((state.node.pos, state.node.field("m_7E8B666C"),
                        state.evaluate(state.node.field("m_amount"), watch=False))
                       for state in instance.states.values()
                       if state.active and state.node.cls == "STUStatescriptStateHealthPool")
        return body.entity_var(DIFFICULTY).value(), result

    def test_mode_writer_drives_all_twenty_body_and_difficulty_combinations(self):
        for tier in range(4):
            mode = self.select_mode(tier)
            for definition in self.catalog.values():
                with self.subTest(tier=tier, npc=definition.name):
                    index, result = self.pool_nodes(definition, mode)
                    self.assertEqual(index, tier)
                    self.assertEqual(result, tuple((node, kind, values[tier])
                                                   for node, kind, values in CONDITIONAL[definition.name]))

    def test_declared_controller_override_takes_precedence_over_mode_index(self):
        mode = self.select_mode(0)
        for tier, amount in enumerate((2000, 3000, 5000, 7000)):
            with self.subTest(tier=tier):
                definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
                override = expr.Asset(0x0D80000000000000 | CHOICES[tier])
                index, result = self.pool_nodes(definition, mode, override)
                self.assertEqual(index, tier)
                self.assertEqual(result, ((3, 0, amount),))

    def test_normal_manual_create_and_real_slug_use_the_declared_base_pools(self):
        import struct

        import test_game_roadhog_npcs as npc_tests
        from test_game_repeated_lifecycles import wire_frame

        from ow174.game import world
        from ow174.game.link import KIND_FRAME, Packet

        def wire_pools(match, bot):
            create = match._bot_create_update(bot)
            payload = world.frame(match.tick, 0, entities=[create])
            _, ((record, fields),) = wire_frame(Packet(1, KIND_FRAME, payload=payload))
            self.assertEqual(record.entity, bot.entity)
            self.assertEqual(record.op, world.OP_CREATE)
            return {kind: (most, now) for _, most, now, kind, *_ in
                    (struct.unpack("<IffI4B", part) for part in fields[51].values())}

        expected = {
            "Trooper": (200, 0), "Enforcer": (250, 100), "Sniper": (300, 0),
            "Assassin": (400, 100), "Heavy Assault": (4000, 0),
        }
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, pool, angles = npc_tests.RoadhogFiveNpcTests.session(self, definition)
                match = run.match
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == 0x143A)
                self.assertFalse(any(s.active and s.node.cls == "STUStatescriptStateHealthPool"
                                     for s in instance.states.values()))
                health, shields = expected[definition.name]
                pools = {world.HEALTH_POOL: (health, health)}
                if shields:
                    pools[world.SHIELDS_POOL] = (shields, shields)
                self.assertEqual(wire_pools(match, bot), pools)
                for _ in range(35):
                    run.step({})
                run.step({hog: (*angles, 2)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(pool.total, health + shields - 50)
                if shields:
                    pools[world.SHIELDS_POOL] = (shields, shields - 50)
                else:
                    pools[world.HEALTH_POOL] = (health, health - 50)
                self.assertEqual(wire_pools(match, bot), pools)

    def test_empty_mode_selector_matches_blank_case_and_unknown_asset_takes_default(self):
        mode = runtime.Component(runtime.GAME_MODE_ENTITY)
        instance = mode.create(graph.graph(0x100B), init=False)
        nodes.set_var(instance, instance.graph.node(322), None)
        # A missing case operand uses integer zero for this switch mode.
        # The fifth case has no link. It matches instead of taking the default.
        self.assertEqual(mode.entity_var(DIFFICULTY_SELECTION).value(), 0)
        self.assertIsNone(mode.entity_var(DIFFICULTY).value())
        mode.set(DIFFICULTY_SELECTION, expr.Asset(0x0D80000000000001))
        nodes.action_switch(instance, instance.graph.node(77), None)
        self.assertEqual(mode.entity_var(DIFFICULTY_SELECTION).value(), expr.Asset(0x0D8000000000195F))
        self.assertEqual(mode.entity_var(DIFFICULTY).value(), 1)

    def test_unscoped_live_health_state_does_not_activate_the_pool_host(self):
        import test_game_roadhog_npcs as npc_tests

        from ow174.game.script.bot import BotWorld

        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        original = BotWorld.native

        def selected(world, cfg, instance):
            if instance.graph.index == 0x143A:
                if cfg.get("$") == "STU_1A3FA5B0":
                    return expr.Asset(definition.body)
                if cfg.get("$") == "STU_C3619A38":
                    return expr.Asset(0x0D80000000001960)
            return original(world, cfg, instance)

        with patch.object(BotWorld, "native", selected):
            run, hog, bot, pool, angles = npc_tests.RoadhogFiveNpcTests.session(self, definition)
            conditional = next(i for i in bot.body_script.component.instances.values()
                               if i.graph.index == 0x143A)
            state = conditional.states[0]
            self.assertTrue(state.active)
            self.assertEqual(state.evaluate(state.node.field("m_amount"), watch=False), 7000)
            for _ in range(35):
                run.step({})
            run.step({hog: (*angles, 2)})
            for _ in range(30):
                run.step({})
        # The current server consumes the base catalog. Graph selection alone
        # cannot distinguish an additive pool from a native override.
        self.assertEqual(pool.maximum, 4000)
        self.assertEqual(pool.total, 3950)

    def test_explicit_spawn_applies_all_twenty_profiles_and_real_secondary_damage(self):
        import test_game_roadhog_npcs as npc_tests

        for tier in range(4):
            for definition in self.catalog.values():
                with self.subTest(tier=tier, npc=definition.name):
                    run, hog, bot, pool, angles = npc_tests.RoadhogFiveNpcTests.session(
                        self, definition, difficulty_index=tier,
                    )
                    base = definition.health + definition.shields + definition.armour
                    amounts = {kind: values[tier] for _, kind, values in CONDITIONAL[definition.name]
                               if values[tier]}
                    self.assertEqual(pool.maximum, base + sum(amounts.values()))
                    current = {kind: item.now for kind, item in pool.conditional_pools.items()}
                    self.assertEqual(current, amounts)
                    for _ in range(35):
                        run.step({})
                    run.step({hog: (*angles, 2)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(pool.total, pool.maximum - 50)
                    self.assertEqual(bot.body_script.component.world.pool_profile, (definition.body, tier))

    def test_selected_shield_state_exits_without_removing_base_or_reusing_wire_identity(self):
        import struct

        import test_game_roadhog_npcs as npc_tests
        from test_game_repeated_lifecycles import wire_frame

        from ow174.game import npc, world
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
        from ow174.game.link import KIND_FRAME, Packet

        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        run, hog, bot, pool, angles = npc_tests.RoadhogFiveNpcTests.session(
            self, definition, difficulty_index=3,
        )
        match = run.match
        kind = world.SHIELDS_POOL
        slot = kind * world.POOLS + 1
        shield = pool.conditional_pools[kind]
        self.assertEqual((shield.most, shield.now), (200, 200))
        self.assertNotEqual(shield.pool_id, pool.ids[kind])
        for _ in range(35):
            run.step({})
        run.step({hog: (*angles, 2)})
        for _ in range(30):
            run.step({})
        self.assertEqual(shield.now, 150)
        self.assertEqual(pool.now[kind], 100)
        create = match._bot_create_update(bot)
        payload = world.frame(match.tick, 0, entities=[create])
        _, ((_, fields),) = wire_frame(Packet(1, KIND_FRAME, payload=payload))
        self.assertEqual(struct.unpack("<IffI4B", fields[51][slot])[:4], (shield.pool_id, 200, 150, kind))
        before = snapshot_digest(export_component_vm(bot.body_script.component))
        self.assertEqual(snapshot_digest(export_component_vm(bot.body_script.component)), before)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x143A)
        instance.states[4].end(False)
        run.step({})
        self.assertEqual((shield.most, shield.now, pool.now[kind]), (0, 0, 100))
        self.assertEqual(pool.maximum, 600)
        self.assertEqual(pool.total, 600)
        record = pool.component(pool.ids)[51][slot]
        self.assertEqual(struct.unpack("<IffI4B", record)[:4], (shield.pool_id, 0, 0, kind))
        self.assertNotEqual(snapshot_digest(export_component_vm(bot.body_script.component)), before)

        self.assertIn(bot.entity, npc.remove_spawned(match))
        self.assertNotIn(bot.entity, match.combat.vitals)

    def test_selected_health_extends_lifetime_and_retires_on_actual_slug_death(self):
        import test_game_roadhog_npcs as npc_tests

        from ow174.game import world

        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, hog, bot, pool, angles = npc_tests.RoadhogFiveNpcTests.session(
            self, definition, difficulty_index=0,
        )
        extra = pool.conditional_pools[world.HEALTH_POOL]
        self.assertEqual(pool.maximum, 6000)
        self.assertEqual(pool.damage(5950), 5950)
        self.assertEqual((pool.total, extra.now, pool.dead), (50, 0, False))
        for _ in range(35):
            run.step({})
        run.step({hog: (*angles, 2)})
        for _ in range(30):
            run.step({})
        self.assertTrue(pool.dead)
        self.assertEqual(pool.total, 0)
        self.assertEqual(extra.most, 0)
        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
