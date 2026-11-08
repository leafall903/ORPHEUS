"""Actual five-body fixtures from an explicitly supplied read-only NPC data pack.

These cases prove player attacks against base pools. They do not prove enemy AI,
special attacks, NPC displacement, difficulty scaling or original-client visuals.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_combat as model
import test_pharah_combat as primary

from ow174.game import combat, npc, world
from ow174.game.script import graph
from ow174.paths import DATA_DIR


class NpcClient(model.FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.deaths.append((entity, event))


class PharahPveTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    fire_once = primary.PharahCombatTests.fire_once
    health = primary.PharahCombatTests.health

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()
        graph._data.cache_clear()

    @classmethod
    def setUpClass(cls):
        folder = Path(os.environ.get("ORPHEUS_NPC_DATA_DIR", DATA_DIR))
        catalog = folder / "pve_npcs_174.json"
        bundle = folder / "game_graphs_174.json.gz"
        if not catalog.is_file() or not bundle.is_file():
            raise unittest.SkipTest("Supply read-only five-enemy pack with ORPHEUS_NPC_DATA_DIR")
        cls.catalog = npc.load_catalog(catalog)
        if {row.name for row in cls.catalog.values()} != {
                "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise ValueError("Acceptance pack must contain the five actual enemy types")
        cls.mount = patch.object(graph, "GRAPHS_PATH", bundle)
        cls.mount.start()
        cls.clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.mount.stop()
        cls.clear_graphs()

    def create(self, definition, *, distance=8, team=1):
        run, player, spare = self.encounter()
        player.client = NpcClient(player)
        spare.client = NpcClient(spare)
        spare.mover.position[0] = 30
        spare.mover.body = None
        bot = npc.spawn(run.match, definition, (0, player.mover.position[1], distance), team=team)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        return run, player, bot

    def test_all_five_receive_120_primary_damage_with_real_base_pool_routing(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                before = self.health(run, bot).total
                creates = [u for u in player.client.entities
                           if u.entity == bot.entity and u.op == world.OP_CREATE]
                self.assertTrue(creates)
                self.assertGreater(creates[0].build(0).count, 0)
                self.fire_once(run, player)
                self.assertEqual(before - self.health(run, bot).total, 120)
                self.assertEqual([h["+0x78"]["+0x4"] for h in player.client.messages(combat.HIT)], [40, 80])

    def test_all_five_receive_30_melee_damage_once(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition, distance=1.8)
                before = self.health(run, bot).total
                run.step({player: (0, 0, 0x800)})
                for _ in range(70):
                    run.step({})
                self.assertEqual(before - self.health(run, bot).total, 30)

    def test_all_five_keep_dead_body_beyond_respawn_deadline(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                fight._damage(player, target, self.health(run, bot).total + 1, False, None, run.frame)
                self.assertTrue(bot.dead)
                self.assertTrue(player.client.deaths)
                for _ in range(round(combat.BOT_RESPAWN / combat.TICK) + 5):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertEqual(self.health(run, bot).total, 0)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def start_heavy_weapon(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot = self.create(definition)
        bot.yaw = -32768
        bot.firing = True
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x114F)
        # Explicit attack-trigger fixture, not AI target acquisition. The attack,
        # shot schedule and five-damage hit definition are the actual body graph.
        weapon.state(25).begin()
        weapon.component.run_now()
        return run, player, bot

    def test_heavy_actual_weapon_damages_pharah_then_npc_death_stops_attacks(self):
        run, player, bot = self.start_heavy_weapon()
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, player).total, 185)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        fight._damage(player, target, self.health(run, bot).total + 1, False, None, run.frame)
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, player).total, 185)

    def test_heavy_actual_weapon_can_kill_pharah_without_overkill_or_respawn(self):
        run, player, _ = self.start_heavy_weapon()
        for _ in range(170):
            run.step({})
        vitals = self.health(run, player)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)


if __name__ == "__main__":
    unittest.main()
