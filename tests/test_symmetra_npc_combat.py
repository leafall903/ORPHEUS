"""Actual five-NPC resource fixtures; opt in with external catalog/graph paths.

These tests exercise player input against real NPC definitions and scripts.
They do not supply enemy acquisition/attack AI or claim original-client proof.
Canonical runtime inputs are read only; supplementary graphs are test-scoped.
"""

import gzip
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_symmetra_combat import arena, fire, health

from ow174.game import combat, npc
from ow174.game.script import graph
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import OWNER_HEALTH

CATALOG = os.environ.get("ORPHEUS_PVE_CATALOG")
BUNDLE = os.environ.get("ORPHEUS_PVE_GRAPHS")


@unittest.skipUnless(CATALOG and BUNDLE, "requires explicit external five-NPC resource fixtures")
class SymmetraNpcResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = npc.load_catalog(Path(CATALOG))
        if {d.name for d in cls.definitions.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault",
        } or any(d.mover is None for d in cls.definitions.values()):
            raise ValueError("five NPC definitions with authored mover data are required")
        cls.bundle = json.loads(gzip.decompress(Path(BUNDLE).read_bytes()))
        cls.extra = {r["index"]: graph.Graph(r) for r in cls.bundle["graphs"]}
        original_graph, original_entity = graph.graph, graph.entity_body

        def lookup(index):
            return original_graph(index) or cls.extra.get(index)

        def entity(body):
            return original_entity(body) or cls.bundle.get("entities", {}).get(f"0x{body:016X}")

        cls.graph_patch = patch.object(graph, "graph", side_effect=lookup)
        cls.entity_patch = patch.object(graph, "entity_body", side_effect=entity)
        cls.graph_patch.start()
        cls.entity_patch.start()
        cls.addClassCleanup(cls.graph_patch.stop)
        cls.addClassCleanup(cls.entity_patch.stop)

    def create(self, definition, *, distance=4, ally=False):
        run, player, other = arena()
        other.mover.position = [40, 1, 40]
        bot = npc.spawn(run.match, definition, (0, 1, distance),
                        team=player.team if ally else 1 - player.team)
        self.assertIsInstance(bot.body_script, BotScript)
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(),
                         definition.health + definition.armour + definition.shields)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        self.assertEqual((target.height, target.radius),
                         (definition.mover.stand_height, definition.mover.stand_radius))
        return run, player, bot, target

    def test_primary_commands_remove_actual_enemy_pools_for_every_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                before = health(run, bot).total
                fire(run, player, 4)
                self.assertAlmostEqual(before - health(run, bot).total, 4 * 60 * .016, places=4)

    def test_charged_orb_commands_fly_and_remove_actual_pools_for_every_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                before = health(run, bot).total
                fire(run, player, 90, buttons=2)
                self.assertEqual(health(run, bot).total, before)
                fire(run, player, 30, buttons=0)
                self.assertAlmostEqual(before - health(run, bot).total, 120, places=4)

    def test_melee_commands_remove_actual_enemy_pools_for_every_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition, distance=2)
                before = health(run, bot).total
                fire(run, player, buttons=0x800)
                self.assertAlmostEqual(before - health(run, bot).total, 30, places=4)

    def test_primary_miss_range_and_ally_protection_for_every_npc(self):
        for definition in self.definitions.values():
            for reason in ("miss", "range", "ally"):
                with self.subTest(npc=definition.name, reason=reason):
                    run, player, bot, _target = self.create(
                        definition, distance=30 if reason == "range" else 4, ally=reason == "ally")
                    if reason == "ally":
                        run.match.game_map = run.match.game_map.__class__(
                            **{**run.match.game_map.__dict__, "free_for_all": False})
                    before = health(run, bot).total
                    fire(run, player, 4, yaw=-32768 if reason == "miss" else 0)
                    self.assertEqual(health(run, bot).total, before)

    def test_preinjured_npc_lethal_beam_retains_every_actual_body(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.create(definition)
                # Fixture setup: a prior server damage event leaves .5 health.
                # The final lethal event must come from the actual beam command.
                fight = run.match.combat
                fight._damage(player, target, health(run, bot).total - .5, False, None, run.frame)
                self.assertFalse(bot.dead)
                fire(run, player)
                self.assertTrue(bot.dead)
                self.assertEqual(health(run, bot).total, 0)
                self.assertTrue(any(entity == bot.entity and snapshot is not None
                                    for entity, snapshot in player.client.bot_deaths))
                fire(run, player, 200, buttons=0)
                self.assertIn(bot, run.match.bots)
                self.assertIsInstance(bot.body_script, BotScript)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertNotIn(bot.entity, {t.entity for t in fight._enemies(player)})

    def test_npc_damage_dispatch_reaches_symmetra_shields_with_npc_attribution(self):
        # This isolates damage dispatch, not the still-missing NPC attack AI.
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                fight = run.match.combat
                fight._damage(bot, target, 30, False, None, run.frame)
                self.assertEqual(health(run, player).now[combat.SHIELDS_POOL], 95)
                fight._damage(bot, target, 1000, False, None, run.frame)
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(health(run, player).killer, bot.entity)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_hits_slows_and_restores_actual_npc_route_movement(self):
        from test_symmetra_turrets import wall

        from ow174.game.content import SOLDIER, heroes

        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, _target = self.create(definition)
                bot.position = (2, 1, 1)
                fight = run.match.combat
                before = health(run, bot).total
                with patch.object(fight, "_collision", return_value=wall()):
                    fire(run, owner, 40, buttons=8)
                    fire(run, owner, 80, buttons=0)
                    self.assertLess(health(run, bot).total, before)
                    self.assertAlmostEqual(bot.body_script.component.entity_var(23186).value(), .2, places=5)
                    self.assertAlmostEqual(bot.speed, definition.mover.run_forward * .8, places=5)
                    bot.route = [(2, 1, 1), (2, 1, 9)]
                    bot.target, bot.start, bot.wait = (2, 1, 9), bot.position, 0
                    bot.direction = (0, 1)
                    bot.keys = True
                    old = bot.position
                    fire(run, owner, 4, buttons=0)
                    self.assertGreater(bot.position[2], old[2])
                    run.match.switch_hero(owner, heroes()[SOLDIER])
                    fire(run, owner, buttons=0)
                    self.assertFalse(bot.body_script.component.entity_var(23186).value())
                    self.assertEqual(bot.speed, definition.mover.run_forward)


if __name__ == "__main__":
    unittest.main()
