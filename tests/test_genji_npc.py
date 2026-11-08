"""Explicit external five-NPC catalog fixtures; transport/AI/client remain separate."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_genji_combat import GenjiFixture

from ow174.game import npc
from ow174.game.script import graph

NPC_DATA = os.environ.get("ORPHEUS_NPC_DATA_DIR")


def clear_graphs():
    for cached in (graph._data, graph._records, graph.graph, graph.mode_variables):
        cached.cache_clear()


@unittest.skipUnless(NPC_DATA, "requires explicit read-only ORPHEUS_NPC_DATA_DIR")
class GenjiNpcTests(GenjiFixture):
    @classmethod
    def setUpClass(cls):
        cls.catalog = npc.load_catalog(Path(NPC_DATA) / "pve_npcs_174.json")
        cls.data_patch = patch.object(graph, "GRAPHS_PATH", Path(NPC_DATA) / "game_graphs_174.json.gz")
        cls.data_patch.start()
        clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.data_patch.stop()
        clear_graphs()

    def scene(self, key, *, distance=4, deflect=False):
        run, player = self.spawn(names=("Genji",))
        run.match._pending_bot_scripts.clear()
        if deflect:
            for frame in range(5):
                run.step({player: (0, 0, 16 if frame == 0 else 0)})
        bot = npc.spawn(run.match, self.catalog[key], (0, 1, distance), team=1, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        return run, player, bot

    def check_damage(self, attack, expected):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name, attack=attack):
                run, player, bot = self.scene(key, distance=0.8 if attack == "secondary" else 4)
                before = run.match.combat.vitals[bot.entity].total
                if attack == "blade":
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                for frame in range(110 if attack == "blade" else 60):
                    if attack == "blade":
                        buttons = 32 if frame == 1 else (1 if 70 < frame < 90 else 0)
                    else:
                        buttons = {"primary": 1, "secondary": 2, "strike": 8}[attack] if frame == 0 else 0
                    run.step({player: (0, 0, buttons)})
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - expected)

    def test_primary_against_all_five(self):
        self.check_damage("primary", 87)

    def test_secondary_against_all_five(self):
        self.check_damage("secondary", 87)

    def test_strike_against_all_five(self):
        self.check_damage("strike", 50)

    def test_blade_against_all_five(self):
        self.check_damage("blade", 110)

    def test_trooper_and_heavy_authored_fire_damage_genji(self):
        for key in ("1", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, player, bot = self.scene(key)
                # Explicit fire control, not autonomous acquisition/AI acceptance.
                bot.firing = True
                bot.body_script.component.edges([(20, True)])
                bot.body_script.component.run_now()
                for _ in range(60):
                    run.step({})
                self.assertLess(self.health(run, player), 200)

    def test_trooper_and_heavy_authored_fire_is_deflected(self):
        for key, expected in (("1", 60), ("5", 35)):
            with self.subTest(npc=self.catalog[key].name):
                run, player, bot = self.scene(key, deflect=True)
                before = run.match.combat.vitals[bot.entity].total
                bot.firing = True
                bot.body_script.component.edges([(20, True)])
                bot.body_script.component.run_now()
                for _ in range(60):
                    run.step({})
                self.assertEqual(self.health(run, player), 200)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - expected)


if __name__ == "__main__":
    unittest.main()
