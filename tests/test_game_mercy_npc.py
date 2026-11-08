"""Real five-body catalog controls, with explicit flat collision/permissive VMs.

The external NPC bundle/catalog are read-only. These controls do not establish
NPC AI, specials, difficulty scaling or original-client acceptance.
"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_game_combat import Run, aim
from test_game_mercy_staff import SCENE

from ow174.game import bodytrace, combat, movelog, npc, stats
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.regeneration import MERCY

NPC_DATA = Path("D:/overwatch 1.7.4/verification/retribution/npc-runtime/data")


def clear_graphs():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


@unittest.skipUnless((NPC_DATA / "pve_npcs_174.json").is_file(), "external five-body catalog required")
class MercyNpcTests(unittest.TestCase):
    def setUp(self):
        for diagnostic in (bodytrace, movelog):
            self.enterContext(patch.object(diagnostic, "ENABLED", False))
        self.enterContext(patch.object(graph, "GRAPHS_PATH", NPC_DATA / "game_graphs_174.json.gz"))
        clear_graphs()
        self.addCleanup(clear_graphs)
        self.catalog = npc.load_catalog(NPC_DATA / "pve_npcs_174.json")
        self.assertEqual({d.name for d in self.catalog.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})

    def spawn(self, definition, team):
        run = Run(SCENE)
        player, = run.players
        player.client.queue_bot_death = Mock()  # explicit transport adapter, not a VM/death override
        player.client.queue_bot_death_update = Mock()
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[MERCY])
        run.step({})
        bot = npc.spawn(match, definition, (0, 1, 5), team=team)
        self.assertIsNotNone(bot.body_script)
        target = next(t for t in match.combat._targets() if t.owner is bot)
        return run, player, bot, target

    def tick(self, run, player, bot, count, buttons=FIRE):
        for _ in range(count):
            x, y, z = bot.position
            yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
            run.step({player: (yaw, pitch, buttons)})

    def test_each_friendly_npc_receives_real_staff_healing(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.spawn(definition, 0)
                vitals = run.match.combat.vitals_of(target)
                vitals.damage(100)
                run.match.combat._sync_body_health(bot, vitals)
                before = vitals.total
                self.tick(run, player, bot, 50)
                self.assertAlmostEqual(vitals.total - before, 44, places=4)
                self.assertAlmostEqual(player.stats.value(stats.HEALING), 44, places=4)

    def test_each_hostile_npc_rejects_staff_and_takes_blaster_projectiles(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.spawn(definition, 1)
                vitals = run.match.combat.vitals_of(target)
                vitals.damage(50)
                before = vitals.total
                self.tick(run, player, bot, 10)
                self.assertEqual(vitals.total, before)
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                player.take_commands([Command(run.frame, action=2)])
                self.tick(run, player, bot, 35, buttons=0)
                self.tick(run, player, bot, 20)
                self.tick(run, player, bot, 10, buttons=0)
                self.assertLess(vitals.total, before)
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), before - vitals.total)

    def test_each_npc_source_can_damage_mercy_and_restart_actual_self_healing(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _ = self.spawn(definition, 1)
                fight = run.match.combat
                victim = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(victim)
                fight._damage(bot, victim, 50, False, None, run.frame)
                self.assertEqual(vitals.total, 150)
                self.tick(run, player, bot, 62, buttons=0)
                self.assertEqual(vitals.total, 150)
                self.tick(run, player, bot, 63, buttons=0)
                self.assertAlmostEqual(vitals.total, 170.16, places=4)

    def test_actual_blaster_kills_each_base_pool_body_and_keeps_its_corpse(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.spawn(definition, 1)
                self.tick(run, player, bot, 20, buttons=0)
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                player.take_commands([Command(run.frame, action=2)])
                self.tick(run, player, bot, 35, buttons=0)
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x006D)
                count = 0
                while not bot.dead and count < 6000:
                    ammo = weapon.find_var(0, 53).value()
                    self.tick(run, player, bot, 1, buttons=0x400 if ammo == 0 else FIRE)
                    count += 1
                self.assertTrue(bot.dead, f"{definition.name} survived{count} simulated blaster frames")
                self.assertEqual(run.match.combat.vitals_of(target).total, 0)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                self.tick(run, player, bot, 250, buttons=0)
                self.assertIn(bot, run.match.bots)
                self.assertNotIn(bot.entity, [t.entity for t in run.match.combat._enemies(player)])

    def test_guardian_angel_reaches_each_friendly_npc_and_rejects_hostile_npcs(self):
        for definition in self.catalog.values():
            for team in (0, 1):
                with self.subTest(npc=definition.name, team=team):
                    run, player, bot, _ = self.spawn(definition, team)
                    bot.position = (0, 1, 12)
                    self.tick(run, player, bot, 12, buttons=0x08)
                    if team == 0:
                        self.assertGreater(player.mover.position[2], 0.5)
                    else:
                        self.assertEqual(player.mover.position[2], 0)

    def test_resurrect_does_not_turn_friendly_manual_npc_corpses_into_players(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.spawn(definition, 0)
                bot.position = (0, 1, 3)
                fight = run.match.combat
                fight._damage(bot, target, fight.vitals_of(target).total + 1000, False, None, run.frame)
                self.assertTrue(bot.dead)
                self.tick(run, player, bot, 3, buttons=0)
                self.tick(run, player, bot, 1, buttons=0x10)
                self.tick(run, player, bot, 120, buttons=0)
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x00CA)
                self.assertEqual(instance.find_var(0, 61).value(), 0)
                self.assertTrue(bot.dead)
                self.assertEqual(fight.vitals_of(target).total, 0)
