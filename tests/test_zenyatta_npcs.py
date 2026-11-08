"""Opt-in actual five-body fixture. No AI/conditional difficulty/client claim.

Set ORPHEUS_NPC_DATA to the prepared read-only folder containing the graph
bundle and pve_npcs_174.json. Ordinary tests require no mission assets.
"""

import os
import unittest
from pathlib import Path

from test_game_combat import Run

from ow174.game import combat, npc
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.zenyatta import HERO


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA"), "needs explicit external prepared NPC data")
class ZenyattaNpcTests(unittest.TestCase):
    @classmethod
    def clear_graph_cache(cls):
        for name in ("_data", "_records", "graph", "bodies", "entity_body", "entity_script_component"):
            function = getattr(graph, name)
            if hasattr(function, "cache_clear"):
                function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        folder = Path(os.environ["ORPHEUS_NPC_DATA"])
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        graph.GRAPHS_PATH = folder / "game_graphs_174.json.gz"
        cls.clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, definition, team=1):
        run = Run()
        zen = run.players[0]
        run.match.switch_hero(zen, heroes()[HERO])
        run.match.bots.clear()
        run.step({})
        zen.client.queue_bot_death = lambda *args: None
        zen.client.queue_bot_death_update = lambda *args: None
        zen.client.bot_stream = lambda body: None  # adapter has no observer transport stream
        x, y, z = zen.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z+8), team=team, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        return run, zen, bot, run.match.combat.vitals[bot.entity]

    def test_primary_and_discord_hit_each_actual_enemy_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(60)
                run.step({zen: (0, 0, 0x08)})
                run.step({zen: (0, 600, FIRE)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, expected.total)
                self.assertEqual(zen.client.messages(combat.HIT)[0]["+0x78"]["+0x0"]["+0x0"], bot.entity)

    def test_harmony_heals_each_actual_friendly_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, _, vitals = self.arena(definition, team=0)
                before = vitals.total
                vitals.damage(100)
                damaged = vitals.total
                run.step({zen: (0, 0, 0x10)})
                for _ in range(60):
                    run.step({})
                self.assertAlmostEqual(vitals.total, min(before, damaged+28.8))

    def test_native_trooper_sniper_heavy_fire_damage_zen_and_ultimate_rejects_damage(self):
        for key in ("1", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                run, zen, bot, _ = self.arena(definition)
                bot.firing = True
                bot.body_script.component.run_frame(run.frame+1, [(20, True)])
                own = next(t for t in run.match.combat._targets() if t.owner is zen)
                vitals = run.match.combat.vitals_of(own)
                for _ in range(400):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertLess(vitals.total, 200)
                self.assertGreater(vitals.total, 0)
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                for _ in range(300):
                    run.step({})
                self.assertEqual(vitals.total, 200)

    def test_sniper_manager_and_authored_weapons_are_hosted(self):
        run, _, bot, _ = self.arena(self.catalog['3'])
        scripts = {instance.graph.index for instance in bot.body_script.component.instances.values()}
        self.assertTrue({0x0015, 0x1122, 0x1134} <= scripts)
        # This allocation check deliberately does not claim that Sniper fires.
        self.assertIn(bot, run.match.bots)

    def test_each_manual_npc_killed_by_orbs_keeps_its_corpse_past_respawn_deadline(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                for _ in range(4000):
                    run.step({zen: (0, 600, FIRE)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                for _ in range(220):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertIs(run.match.combat.vitals[bot.entity], vitals)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))


if __name__ == "__main__":
    unittest.main()
