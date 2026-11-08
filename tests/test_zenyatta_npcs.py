"""Opt-in actual five-body fixture. No AI/conditional difficulty/client claim.

Set ORPHEUS_NPC_DATA to the prepared read-only folder containing the graph
bundle and pve_npcs_174.json. Ordinary tests require no mission assets.
"""

import os
import unittest
from pathlib import Path

from test_game_combat import Run

from ow174.game import combat, npc, npc_control
from ow174.game.collision import World
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

    def test_full_secondary_damages_each_actual_enemy_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, _, vitals = self.arena(definition)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                for _ in range(5):
                    expected.damage(48)
                for _ in range(130):
                    run.step({zen: (0, 600, 0x02)})
                for _ in range(60):
                    run.step({zen: (0, 600, 0)})
                self.assertEqual(vitals.total, expected.total)

    def test_melee_damages_each_actual_enemy_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                x, y, z = zen.mover.position
                bot.position = (x, y, z+1.5)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(30)
                run.step({zen: (0, 0, 0x800)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(vitals.total, expected.total)

    def test_transcendence_heals_each_actual_friendly_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, _, vitals = self.arena(definition, team=0)
                before = vitals.total
                vitals.damage(100)
                damaged = vitals.total
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.total, min(before, damaged+48))

    def test_melee_cannot_damage_each_actual_enemy_npc_through_static_wall(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                x, y, z = zen.mover.position
                bot.position = (x, y, z+1.5)
                run.match.collision_world = World([
                    (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
                    (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
                    (x-5, y-1, z+.75, x+5, y-1, z+.75, x+5, y+5, z+.75),
                    (x-5, y-1, z+.75, x+5, y+5, z+.75, x-5, y+5, z+.75),
                ])
                before = vitals.total
                run.step({zen: (0, 0, 0x800)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(vitals.total, before)

    def test_four_ranged_npcs_damage_zen_and_ultimate_rejects_damage(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                run, zen, bot, _ = self.arena(definition)
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
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

    def test_enforcer_uses_all_twenty_five_pellets_and_four_authored_damage_entries(self):
        for index, total_damage in enumerate((28, 56, 84, 112)):
            with self.subTest(index=index):
                run, zen, bot, _ = self.arena(self.catalog['2'])
                bot.body_script.component.set(9948, index)
                npc_control.start(run.match, zen)
                target = next(t for t in run.match.combat._targets() if t.owner is zen)
                vitals = run.match.combat.vitals_of(target)
                maximum_projectiles = 0
                for _ in range(100):
                    run.step({})
                    maximum_projectiles = max(maximum_projectiles, len(run.match.combat.rockets))
                self.assertEqual(maximum_projectiles, 25)
                self.assertEqual(vitals.total, 200-total_damage)
                weapon = next(
                    i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB
                )
                self.assertEqual(weapon.state(14).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 3)

    def test_enforcer_stop_prevents_subsequent_damage(self):
        run, zen, bot, _ = self.arena(self.catalog['2'])
        npc_control.start(run.match, zen)
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(100):
            run.step({})
        self.assertLess(vitals.total, 200)
        npc_control.stop(run.match)
        before = vitals.total
        for _ in range(80):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertFalse(bot.attack_input)
        self.assertFalse(run.match.combat.rockets)

    def test_enforcer_rejects_invalid_damage_index_without_starting_attack(self):
        for index in (-1, 4, True, 0.5):
            with self.subTest(index=index):
                run, zen, bot, _ = self.arena(self.catalog['2'])
                bot.body_script.component.set(9948, index)
                with self.assertRaises(ValueError):
                    npc_control.start(run.match, zen)
                self.assertFalse(bot.firing)
                self.assertIsNone(bot.attack_target)

    def test_enforcer_cannot_attack_through_wall_out_of_range_or_after_team_change(self):
        for reason in ('wall', 'range', 'team'):
            with self.subTest(reason=reason):
                run, zen, bot, _ = self.arena(self.catalog['2'])
                npc_control.start(run.match, zen)
                x, y, z = zen.mover.position
                if reason == 'wall':
                    run.match.collision_world = World([
                        (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
                        (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
                        (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
                        (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
                    ])
                elif reason == 'range':
                    bot.position = (x, y, z+120)
                else:
                    bot.team = zen.team
                for _ in range(100):
                    run.step({})
                target = next(t for t in run.match.combat._targets() if t.owner is zen)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)
                self.assertFalse(bot.attack_input)

    def test_enforcer_authored_cadence_and_reload_continue_beyond_two_magazines(self):
        run, zen, bot, _ = self.arena(self.catalog['2'])
        # Explicit durable test actor: verify repeated volleys without player death/respawn.
        vitals = combat.Vitals(zen.body, 5000)
        run.match.combat.vitals[zen.body] = vitals
        npc_control.start(run.match, zen)
        for _ in range(1200):
            run.step({})
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(weapon.state(14).counter, 8)
        self.assertEqual(vitals.total, 4776)
        self.assertEqual(weapon.vars[53].value(), 4)

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
