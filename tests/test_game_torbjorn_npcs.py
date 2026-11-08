"""External five-body NPC fixtures; permissive VM/transport adapter, no AI acceptance.

Set ORPHEUS_NPC_CATALOG_DIR to the prepared read-only directory containing
pve_npcs_174.json and game_graphs_174.json.gz. Normal runtime inputs are unchanged.
"""

import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_torbjorn as torbjorn

from ow174.game import combat, npc, npc_control
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs external five-NPC catalog")
class TorbjornNpcTests(unittest.TestCase):
    @staticmethod
    def clear_graph_cache():
        for function in (graph._data, graph._records, graph.graph, graph.mode_variables):
            function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        directory = Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"])
        graph.GRAPHS_PATH = directory / "game_graphs_174.json.gz"
        cls.clear_graph_cache()
        cls.catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        if len(cls.catalog) != 5:
            raise ValueError("the acceptance fixture requires all five NPC definitions")

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, key, distance=5.0, *, friendly=False):
        run, owner, _ = torbjorn.TorbjornProjectileTests().arena(100.0)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        for viewer in match.players:
            viewer.client.queue_bot_death = lambda *args: None
            viewer.client.queue_bot_death_update = lambda *args: None
        x, y, z = owner.mover.position
        bot = npc.spawn(match, self.catalog[key], (x, y, z + distance),
                        team=owner.team if friendly else 1 - owner.team, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        self.assertGreater(match._bot_create_update(bot).build(0).count, 0)
        return run, owner, bot, match.combat.vitals[bot.entity]

    def test_primary_secondary_quick_melee_and_hammer_damage_all_five(self):
        for key, definition in self.catalog.items():
            for label, button, expected in (("primary", 1, 70.0), ("secondary", 2, 125.0),
                                             ("quick-melee", 0x800, 30.0), ("hammer", 1, 55.0)):
                with self.subTest(npc=definition.name, action=label):
                    run, owner, _, vitals = self.arena(key, 1.5)
                    before = vitals.total
                    if label == "hammer":
                        from ow174.game.commands import Command

                        run.frame += 1
                        owner.take_commands([Command(run.frame, action=2)])
                        for _ in range(45):
                            run.step({})
                    run.step({owner: (0, 0, button)})
                    for _ in range(40):
                        run.step({owner: (0, 0, button if label == "hammer" else 0)})
                    self.assertAlmostEqual(before - vitals.total, expected)

    def test_friendly_npc_takes_no_primary_secondary_or_melee_damage(self):
        for key in self.catalog:
            for button in (1, 2, 0x800):
                with self.subTest(npc=key, button=button):
                    run, owner, _, vitals = self.arena(key, 1.5, friendly=True)
                    before = vitals.total
                    run.step({owner: (0, 0, button)})
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(vitals.total, before)

    def test_actual_heavy_assault_weapon_damages_torbjorn_armour(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Heavy Assault")
        run, owner, bot, _ = self.arena(key)
        fight = run.match.combat
        while run.frame < bot.records_from:
            run.step({})
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x114F)
        state = instance.state(25)
        self.assertEqual(combat.hit_def(state, state.node.field("m_6396149F")).amount, 5.0)
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        npc.aim_at(run.match, bot,
                   (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2]))
        self.assertNotEqual(bot.pitch, 0)
        self.assertEqual(bot.movement(run.frame).pitch, bot.pitch)
        self.assertTrue(bot.record_due(run.frame))
        fight._shoot(bot, None, state, 0, run.frame * 16, activation=(1, run.frame * 16))
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(vitals.total, 247.5)

    def test_trooper_and_sniper_actual_primary_input_hits_and_respects_team(self):
        for name, damage in (("Trooper", 15.0), ("Sniper", 95.0)):
            key = next(key for key, row in self.catalog.items() if row.name == name)
            for friendly in (False, True):
                with self.subTest(npc=name, friendly=friendly):
                    run, owner, bot, _ = self.arena(key, friendly=friendly)
                    # This adapter omits observer projectile transport; authoritative
                    # input, authored weapon delays and health damage still execute.
                    for viewer in run.match.players:
                        viewer.client.bot_stream = lambda *args: None
                    while run.frame < bot.records_from:
                        run.step({})
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is owner)
                    hp = fight.vitals_of(target)
                    npc.aim_at(run.match, bot, (target.feet[0], target.feet[1] + target.height * 0.5,
                                              target.feet[2]))
                    button = bot.body_script.component.world.weapon_buttons["0"]
                    bot.body_script.component.edges([(button, True)])
                    bot.attack_target = owner.entity
                    bot.firing = True
                    for _ in range(200):
                        run.step({})
                        if hp.total < 250.0:
                            break
                    self.assertIsNotNone(bot.body_script)
                    self.assertAlmostEqual(250.0 - hp.total, 0.0 if friendly else damage)

    def test_each_real_npc_identity_can_damage_and_kill_at_combat_boundary(self):
        # This exercises attribution and pools, not authored AI or attack execution.
        for key in self.catalog:
            with self.subTest(npc=key):
                run, owner, bot, _ = self.arena(key)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                fight._damage(bot, target, 20.0, False, None, run.frame)
                self.assertEqual(vitals.total, 235.0)
                fight._damage(bot, target, 1000.0, False, None, run.frame)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertTrue(owner.body_script.component.owner_dead)

    def test_actual_manual_engagement_stop_and_restart_for_three_working_attackers(self):
        for name in ("Trooper", "Sniper", "Heavy Assault"):
            with self.subTest(npc=name):
                key = next(key for key, row in self.catalog.items() if row.name == name)
                run, owner, bot, _ = self.arena(key)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = run.match.combat.vitals[owner.body]
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hp.total < 250.0:
                        break
                self.assertLess(hp.total, 250.0)
                npc_control.stop(run.match)
                before = hp.total
                for _ in range(80):
                    run.step({})
                self.assertEqual(hp.total, before)
                self.assertFalse(bot.firing)
                self.assertIsNone(bot.attack_target)
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hp.total < before:
                        break
                self.assertLess(hp.total, before)

    def test_all_five_manual_npc_corpses_survive_the_respawn_deadline(self):
        for key in self.catalog:
            with self.subTest(npc=key):
                run, owner, bot, vitals = self.arena(key)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                fight._damage(owner, target, vitals.total + 500.0, False, None, run.frame)
                for _ in range(220):
                    run.step({})
                self.assertTrue(bot.dead)
                self.assertTrue(bot.retain_corpse)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(vitals.total, 0.0)
                self.assertFalse(any(actor is bot for _, actor in fight.respawns))
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.body_script.component.owner_dead)
