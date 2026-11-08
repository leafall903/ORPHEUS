"""Real five-body NPC inputs, ordinary Roadhog commands and authoritative pools.

Explicit external inputs are required. The catalog's base pools do not represent
authored difficulty totals; permissive NPC body VM/fixture transport are not AI
or original-client acceptance. NPC attacks/specials still need a live input host.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, FakeClient, Run, aim

from ow174.game import combat, npc
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040


class NpcClient(FakeClient):
    """Record death dispatch explicitly; fixture transport is still not a client."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []
        self.death_updates = []

    def queue_bot_death(self, entity, payload):
        self.deaths.append((entity, payload))

    def queue_bot_death_update(self, entity, payload):
        self.death_updates.append((entity, payload))


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                     "explicit prepared five-NPC catalog and graph bundle required")
class RoadhogFiveNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        cls.graph_patch = patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_NPC_GRAPHS"]))
        cls.graph_patch.start()
        cls.clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.graph_patch.stop()
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def session(self, definition, *, distance=2.0):
        run = Run(KANEZAKA_DM)
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, = run.players
        hog.client = NpcClient(hog)
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1)
        self.assertIsNotNone(bot.body_script)
        target = next(t for t in match.combat._targets() if t.owner is bot)
        vitals = match.combat.vitals_of(target)
        self.assertEqual(vitals.total, definition.health + definition.armour + definition.shields)
        self.assertEqual(vitals.now[2], definition.shields)
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        angles = aim(match.combat._eye(hog), centre)
        return run, hog, bot, vitals, angles

    def test_real_primary_secondary_and_melee_damage_all_five_authored_bodies(self):
        self.assertEqual({d.name for d in self.definitions.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for definition in self.definitions.values():
            for button in (1, 2, 0x800):
                with self.subTest(npc=definition.name, button=button):
                    run, hog, _, vitals, angles = self.session(definition, distance=1.0)
                    before = vitals.total
                    run.step({hog: (*angles, button)})
                    for _ in range(40):
                        run.step({hog: (*angles, 0)})
                    self.assertLess(vitals.total, before)
                    self.assertGreater(len(hog.client.messages(combat.HIT)), 0)

    def test_charged_whole_hog_damages_each_authored_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, _, vitals, angles = self.session(definition)
                before = vitals.total
                component = hog.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({hog: (*angles, 32)})
                for _ in range(100):
                    run.step({hog: (*angles, 0)})
                self.assertLess(vitals.total, before)
                self.assertGreater(len(hog.client.messages(combat.HIT)), 0)

    def test_primary_kill_retains_every_manual_npc_corpse_after_respawn_deadline(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=1.0)
                # Explicit near-death fixture tests the ordinary weapon death boundary.
                vitals.damage(vitals.total - 1)
                run.step({hog: (*angles, 1)})
                for _ in range(240):
                    run.step({hog: (*angles, 0)})
                self.assertTrue(vitals.dead)
                self.assertTrue(bot.dead)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(vitals.total, 0)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                self.assertIn(bot.entity, run.match.combat.vitals)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(any(entity == bot.entity for entity, _ in hog.client.deaths))

    def test_heavy_authored_volley_can_damage_roadhog_through_npc_source_path(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, hog, bot, _, _ = self.session(definition, distance=2.0)
        bot.yaw = -32768.0
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        state = next(i.state(n.state) for i in bot.body_script.component.instances.values()
                     for n in i.graph.nodes if n and n.cls == "STUStatescriptStateWeaponVolley")
        # Explicit weapon admission fixture, NOT authored AI target acquisition or
        # fire-control acceptance. Damage and source/team/flight are ordinary Combat.
        run.step({})  # Establish the authoritative world tick after both body creates.
        fight._shoot(bot, None, state, 0, run.frame * 16, activation=(0, 0))
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(own.total, 595.0)


if __name__ == "__main__":
    unittest.main()
