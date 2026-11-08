"""Lucio inputs and authored status graphs against the five real PvE bodies.

External catalog/graphs are read only. Flat collision, adapter transport and
permissive NPC VM execution are explicit fixtures, not AI/client acceptance.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_lucio_combat as fixtures
from test_game_combat import FakeClient

from ow174.game import combat, npc
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.script import graph

CATALOG = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
BUNDLE = os.environ.get("ORPHEUS_PVE_GRAPH_BUNDLE")


class PveClient(FakeClient):
    """Immediate transport adapter that records real NPC terminal snapshots."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.deaths.append((entity, event))


def clear_graph_cache():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


@unittest.skipUnless(CATALOG and BUNDLE, "requires external five-type NPC catalog and graphs")
class LucioPveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph_patch = patch.object(graph, "GRAPHS_PATH", Path(BUNDLE))
        cls.graph_patch.start()
        clear_graph_cache()
        cls.definitions = npc.load_catalog(Path(CATALOG))
        if {d.name for d in cls.definitions.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault",
        }:
            raise ValueError("fixture must supply all five real Archives archetypes")

    @classmethod
    def tearDownClass(cls):
        cls.graph_patch.stop()
        clear_graph_cache()

    def scenario(self, definition, *, team=1, position=(0, 1, 4)):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        for player in run.players:
            player.client = PveClient(player)
        # Other players do not obstruct this NPC's target capsule.
        enemy.mover.position[:] = (50, 1, 50)
        ally.mover.position[:] = (-50, 1, 50)
        bot = npc.spawn(run.match, definition, position, team=team)
        self.assertIsNotNone(bot.body_script)
        self.assertFalse(bot.body_script.component.strict)
        self.assertEqual(bot.hero, definition.hero)
        self.assertEqual(bot.body, definition.body)
        return helper, run, source, bot

    def test_primary_burst_damages_every_actual_enemy_body(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                helper, run, source, bot = self.scenario(definition)
                before = helper.health(run, bot)
                for _ in range(25):
                    run.step({source: (0, 0, FIRE)})
                for _ in range(8):
                    run.step({})  # let the fourth authored projectile reach the NPC
                self.assertEqual(before - helper.health(run, bot), 80)

    def test_soundwave_damages_all_five_but_never_allied_bodies(self):
        for definition in self.definitions.values():
            for team in (0, 1):
                with self.subTest(npc=definition.name, team=team):
                    helper, run, source, bot = self.scenario(definition, team=team)
                    before = helper.health(run, bot)
                    run.step({source: (0, 0, SECONDARY)})
                    self.assertEqual(before - helper.health(run, bot), 25 if team else 0)

    def test_healing_targets_allied_real_npcs_and_excludes_enemy_npcs(self):
        self.assertIsNotNone(graph.graph(0x0267), "requires actual Crossfade status supplement")
        for definition in self.definitions.values():
            for team in (0, 1):
                with self.subTest(npc=definition.name, team=team):
                    helper, run, source, bot = self.scenario(definition, team=team)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    fight.vitals_of(target).damage(100)
                    run.step({source: (0, 0, 8)})
                    for _ in range(30):
                        run.step({})
                    before = helper.health(run, bot)
                    for _ in range(64):
                        run.step({})
                    expected = 16 * 64 * combat.TICK if team == 0 else 0
                    self.assertAlmostEqual(helper.health(run, bot) - before, expected, places=5)
                    self.assertEqual(bot.body_script.frame, run.frame)

    def test_soundwave_kills_keep_real_corpses_and_never_auto_respawn(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                helper, run, source, bot = self.scenario(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                vitals = fight.vitals_of(target)
                vitals.damage(vitals.total - 20)
                run.step({source: (0, 0, SECONDARY)})
                self.assertTrue(bot.dead)
                self.assertTrue(any(entity == bot.entity for entity, _ in source.client.deaths))
                self.assertEqual(helper.health(run, bot), 0)
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertTrue(bot.movement(run.frame).flags & combat.DEAD)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertNotIn(bot, [t.owner for t in fight._enemies(source)])

    def test_explicit_npc_removal_retires_its_aura_without_healing_a_stale_body(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                _, run, source, bot = self.scenario(definition, team=0)
                run.step({source: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                aura = run.match.combat.lucio_auras
                self.assertTrue(any(key[1] == bot.entity for key in aura.leases))
                npc.remove_spawned(run.match)
                run.step({})
                self.assertFalse(any(key[1] == bot.entity for key in aura.leases))

    def wall(self, run):
        run.match.collision_world = World([
            (-50, 1, -50, -50, 1, 50, 50, 1, 50),
            (-50, 1, -50, 50, 1, 50, 50, 1, -50),
            (-5, 1, 0.8, 5, 1, 0.8, 5, 6, 0.8),
            (-5, 1, 0.8, 5, 6, 0.8, -5, 6, 0.8),
        ], name="explicit melee/projectile occlusion fixture")

    def test_melee_real_npcs_honours_team_and_static_wall(self):
        for definition in self.definitions.values():
            for team, wall in ((1, False), (0, False), (1, True)):
                with self.subTest(npc=definition.name, team=team, wall=wall):
                    helper, run, source, bot = self.scenario(definition, team=team, position=(0, 1, 1.8))
                    if wall:
                        self.wall(run)
                    before = helper.health(run, bot)
                    run.step({source: (0, 0, 0x800)})  # logical button31: quick melee
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(before - helper.health(run, bot), 30 if team and not wall else 0)

    def test_projectile_miss_and_real_wall_block_primary_and_soundwave(self):
        for definition in self.definitions.values():
            for attack, yaw, wall in ((FIRE, 16384, False), (FIRE, 0, True), (SECONDARY, 0, True)):
                with self.subTest(npc=definition.name, attack=attack, yaw=yaw, wall=wall):
                    helper, run, source, bot = self.scenario(definition)
                    if wall:
                        self.wall(run)
                    before = helper.health(run, bot)
                    for _ in range(40):
                        run.step({source: (yaw, 0, attack)})
                    self.assertEqual(helper.health(run, bot), before)

    def test_amp_heals_every_allied_real_body_at_authored_rate(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                helper, run, source, bot = self.scenario(definition, team=0)
                run.step({source: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                run.step({source: (0, 0, 16)})
                for _ in range(20):
                    run.step({})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                fight.vitals_of(target).damage(100)
                before = helper.health(run, bot)
                for _ in range(64):
                    run.step({})
                self.assertAlmostEqual(helper.health(run, bot) - before, 52 * 64 * combat.TICK, places=5)

    def test_heal_and_amp_never_resurrect_dead_allied_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                helper, run, source, bot = self.scenario(definition, team=0)
                run.step({source: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                enemy = run.players[1]
                fight._damage(enemy, target, definition.health + definition.shields + 100,
                              False, None, run.frame)
                run.step({source: (0, 0, 16)})
                for _ in range(64):
                    run.step({})
                self.assertTrue(bot.dead)
                self.assertEqual(helper.health(run, bot), 0)
                self.assertFalse(any(key[1] == bot.entity for key in fight.lucio_auras.leases))
