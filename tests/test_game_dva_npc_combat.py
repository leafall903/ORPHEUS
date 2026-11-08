"""Actual NPC scripts/manual primary inputs; autonomous controllers/specials OPEN."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from test_game_mover import FLOOR
from test_game_mover import wall as collision_wall

from ow174.game import combat, npc
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph
from ow174.game.script.driver import DEFAULT_SLOT, OWNER_HEALTH, WEAPON_SLOTS

MECH = 0x0400000000000407
PRIMARY = {"Trooper": (20, 15, 18), "Sniper": (100, 95, 110), "Heavy Assault": (5, 2.5, 18)}


class NpcIncomingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
        if not path:
            raise unittest.SkipTest("requires external actual NPC catalog/graphs/movers; acceptance OPEN")
        cls.catalog = npc.load_catalog(Path(path))
        cls.by_name = {row.name: row for row in cls.catalog.values()}
        if set(cls.by_name) != {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise ValueError("requires actual five-NPC catalog")
        if any(graph.entity_body(row.body) is None or defined_mover_data(row.body) is None
               for row in cls.catalog.values()) or graph.entity_body(MECH) is None:
            raise unittest.SkipTest("requires actual NPC and0407 graph/mover supplements; no fallback")

    def scenario(self, mech, name, case="hit"):
        run = Run(KANEZAKA_DM, ("DVa",))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player = run.players[0]
        hero = find_hero("dva")
        if mech:
            hero = replace(hero, body=MECH)
        self.assertTrue(match.switch_hero(player, hero))
        player.mover.position = [0, 1, 6]
        player.mover.body = None
        for _ in range(100):
            run.step({})
        if case == "wall":
            match.collision_world = World(FLOOR + collision_wall(-10, 3, 10, 3))
            player.mover.use(world=match.collision_world)
        if case == "ally":
            match.game_map = replace(match.game_map, free_for_all=False)
        bot = npc.spawn(match, self.by_name[name], (0, 1, 0),
                        team=player.team if case == "ally" else 1,
                        yaw=16384 if case == "miss" else 0)
        self.assertIsNotNone(bot.body_script)
        button = bot.body_script.component.world.ai_weapon_button(0)
        self.assertEqual(button, 20)
        bot.firing = True
        bot.body_script.component.edges([(button, True)])
        target = next(t for t in match.combat._targets() if t.owner is player)
        return run, player, bot, match.combat.vitals_of(target)

    def first_loss(self, run, vitals, maximum=300):
        before = vitals.total
        for frame in range(1, maximum + 1):
            run.step({})
            if vitals.total < before:
                return frame
        return None

    def test_three_authored_primary_weapons_damage_both_forms_with_per_hit_armour(self):
        for mech in (False, True):
            for name, (pilot_damage, mech_damage, _) in PRIMARY.items():
                with self.subTest(mech=mech, npc=name):
                    run, player, bot, vitals = self.scenario(mech, name)
                    before = vitals.total
                    self.assertIsNotNone(self.first_loss(run, vitals))
                    self.assertIsNotNone(bot.body_script)
                    self.assertEqual(before - vitals.total, mech_damage if mech else pilot_damage)
                    if mech:
                        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
                        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 300 - mech_damage)
                    self.assertEqual(player.body_script.component.entity_var(OWNER_HEALTH).value(),
                                     vitals.total)

    def test_incoming_miss_wall_and_allies_preserve_both_forms(self):
        for mech in (False, True):
            for name in PRIMARY:
                for case in ("miss", "wall", "ally"):
                    with self.subTest(mech=mech, npc=name, case=case):
                        run, _, bot, vitals = self.scenario(mech, name, case)
                        before = vitals.total
                        for _ in range(150):
                            run.step({})
                        self.assertEqual(vitals.total, before)
                        self.assertIsNotNone(bot.body_script)

    def test_matrix_intercepts_actual_npc_fire_then_release_restores_incoming_damage(self):
        for name, (_, _, lead) in PRIMARY.items():
            with self.subTest(npc=name):
                run, player, _, vitals = self.scenario(True, name)
                for _ in range(lead):
                    run.step({})
                self.assertEqual(vitals.total, 600)
                distances = []
                original = run.match.combat._matrix_hit

                def observe(*args, _original=original, _distances=distances):
                    result = _original(*args)
                    _distances.append(result)
                    return result

                with patch.object(run.match.combat, "_matrix_hit", side_effect=observe):
                    for _ in range(40):
                        run.step({player: (-32768, 0, 2)})
                self.assertTrue(any(d is not None for d in distances))
                self.assertEqual(vitals.total, 600)
                self.assertIsNotNone(self.first_loss(run, vitals))

    def test_all_five_scripts_load_with_explicit_pools_and_sniper_sparse_weapon_slot(self):
        for name, definition in self.by_name.items():
            with self.subTest(npc=name):
                run, _, bot, _ = self.scenario(False, name)
                script = bot.body_script
                vitals = run.match.combat.vitals[bot.entity]
                self.assertEqual(vitals.total, definition.health + definition.armour + definition.shields)
                self.assertEqual(script.component.entity_var(OWNER_HEALTH).value(), vitals.total)
                self.assertFalse(script.component.owner_dead)
                if name == "Sniper":
                    self.assertEqual(script.component.entity_var(WEAPON_SLOTS).value(), (False, True, True))
                    self.assertEqual(script.component.entity_var(DEFAULT_SLOT).value(), 1)
                    self.assertTrue(any(i.graph.index == 0x1122 and i.vars[28].value() == 1
                                        for i in script.component.instances.values()))

    def test_removal_retires_npc_script_and_owned_flights_without_late_damage(self):
        run, _, bot, vitals = self.scenario(False, "Sniper")
        script = bot.body_script
        for _ in range(180):
            run.step({})
            if any(r.shooter is bot for r in run.match.combat.rockets):
                break
        self.assertTrue(any(r.shooter is bot for r in run.match.combat.rockets))
        self.assertEqual(vitals.total, 150)  # remove a real in-flight shot before its first impact
        before = vitals.total
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertTrue(script.component.owner_dead)
        self.assertEqual(script.component.instances, {})
        self.assertFalse(any(r.shooter is bot for r in run.match.combat.rockets))
        for _ in range(160):
            run.step({})
        self.assertEqual(vitals.total, before)


if __name__ == "__main__":
    unittest.main()
