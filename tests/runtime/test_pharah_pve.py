"""Actual five-body fixtures from an explicitly supplied read-only NPC data pack.

These cases prove base-pool attacks and three manual return-input paths. They do
not prove enemy AI, specials, NPC displacement, difficulty scaling or client visuals.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_combat as model
import test_pharah_combat as primary

from ow174.game import combat, npc, npc_control, world
from ow174.game.collision import World
from ow174.game.commands import FIRE
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

    def bot_stream(self, entity):
        return None  # authoritative combat fixture; NPC projectile transport is not accepted here


class PharahPveTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    health = primary.PharahCombatTests.health

    @staticmethod
    def aim_at_body(run, player, bot):
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        eye = run.match.combat._eye(player)
        point = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        dx, dy, dz = (b - a for a, b in zip(eye, point, strict=True))
        return (round(math.atan2(dx, dz) * 65536 / (2 * math.pi)),
                round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))

    def fire_once(self, run, shooter):
        bot = next(bot for bot in run.match.bots if bot.spawner.get("manual_spawn"))
        yaw, pitch = self.aim_at_body(run, shooter, bot)
        run.step({shooter: (yaw, pitch, FIRE)})
        for _ in range(60):
            run.step({})

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
        player.mover.yaw, player.mover.pitch = self.aim_at_body(run, player, bot)
        return run, player, bot

    def test_all_five_receive_120_primary_damage_with_real_base_pool_routing(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                before = self.health(run, bot).total
                instance = next(iter(bot.body_script.component.instances.values()))
                cfg = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
                self.assertEqual(instance.evaluate(cfg), before)
                self.assertEqual(bot.body_script.component.entity_var(253).value(), before)
                creates = [u for u in player.client.entities
                           if u.entity == bot.entity and u.op == world.OP_CREATE]
                self.assertTrue(creates)
                self.assertGreater(creates[0].build(0).count, 0)
                self.fire_once(run, player)
                self.assertEqual(before - self.health(run, bot).total, 120)
                self.assertEqual(instance.evaluate(cfg), before - 120)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 120)
                self.assertEqual([h["+0x78"]["+0x4"] for h in player.client.messages(combat.HIT)], [40, 80])

    def test_actual_primary_damage_charges_barrage_then_a_fresh_press_spends_it(self):
        heavy = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot = self.create(heavy, distance=12)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        self.assertGreater(cost, 6 * 120)
        health = self.health(run, bot)
        full_health = health.total
        self.assertGreater(full_health, cost + 120)
        weapon = next(instance for instance in script.component.instances.values()
                      if instance.graph.index == 0x23)
        aim = self.aim_at_body(run, player, bot)
        finishing_rocket = None
        for _ in range(2100):
            run.step({player: (*aim, FIRE)})
            self.assertEqual(charge.value(), full_health - health.total)
            if 0 < cost - charge.value() <= 120:
                finishing_rocket = next((rocket for rocket in fight.rockets if rocket.shooter is player), None)
                if finishing_rocket is not None:
                    break
        self.assertIsNotNone(finishing_rocket)
        self.assertGreater(weapon.states[6].counter, 6)
        self.assertLess(charge.value(), cost)

        ultimate = 32
        run.step({player: (*aim, ultimate)})
        self.assertFalse(script.ultimate_running())
        for _ in range(45):
            if finishing_rocket not in fight.rockets:
                break
            run.step({player: (*aim, ultimate)})
            self.assertFalse(script.ultimate_running())
        self.assertNotIn(finishing_rocket, fight.rockets)
        self.assertEqual(charge.value(), cost)
        for _ in range(8):
            run.step({player: (*aim, ultimate)})
            self.assertFalse(script.ultimate_running())
            self.assertEqual(charge.value(), cost)

        run.step({player: (*aim, 0)})
        before = health.total
        run.step({player: (*aim, ultimate)})
        self.assertTrue(script.ultimate_running())
        self.assertEqual(charge.value(), 0)
        for _ in range(50):
            run.step({player: (*aim, 0)})
            if health.total < before:
                break
        self.assertLess(health.total, before)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(charge.value(), 0)
        volley = next(instance for instance in script.component.instances.values()
                      if instance.graph.index == 0x2B).states[1]
        for _ in range(330):
            run.step({player: (16384, 0, 0)})
            self.assertEqual(charge.value(), 0)
            if not script.ultimate_running() and not fight.rockets:
                break
        self.assertFalse(script.ultimate_running())
        self.assertFalse(fight.rockets)
        self.assertEqual(volley.reported, 76)

        before = health.total
        self.assertGreater(before, 120)
        for _ in range(160):
            run.step({player: (*aim, FIRE)})
            if health.total < before:
                break
        self.assertEqual(health.total, before - 120)
        self.assertEqual(charge.value(), 120)

    def test_all_five_selection_uses_body_height_and_rejects_ray_above_head(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                self.assertIs(npc_control.selected(run.match, player), bot)
                eye = run.match.combat._eye(player)
                dy = target.feet[1] + target.height + 0.1 - eye[1]
                player.mover.pitch = round(-math.atan2(dy, 8) * 65536 / (2 * math.pi))
                self.assertIsNone(npc_control.selected(run.match, player))
                player.mover.yaw, player.mover.pitch = self.aim_at_body(run, player, bot)
                self.assertIs(npc_control.selected(run.match, player), bot)

    def test_primary_extent_hits_all_five_actual_npc_capsules(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                eye = fight._eye(player)
                midpoint = target.feet[1] + target.height / 2
                pitch = round(-math.atan2(midpoint - eye[1], target.feet[2]) * 65536 / math.tau)
                bot.position = (target.radius + .1, *bot.position[1:])
                if bot.impulse_mover is not None:
                    bot.impulse_mover.position = list(bot.position)
                    bot.impulse_mover.body = None
                before = self.health(run, bot).total
                run.step({player: (0, pitch, FIRE)})
                for _ in range(60):
                    run.step({})
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

    def test_all_five_keep_body_after_lethal_pharah_rocket(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                fight = run.match.combat
                instance = next(iter(bot.body_script.component.instances.values()))
                vitals = self.health(run, bot)
                vitals.damage(vitals.total - 60)
                self.assertGreater(vitals.total, 0)
                self.fire_once(run, player)
                self.assertTrue(bot.dead)
                self.assertEqual(instance.evaluate({"$": "STU_9B7648C3",
                                                    "m_entity": {"$": "STU_B5A0CAF0"}}), 0)
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
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        # Actual slot0 input runs the authored windup. Manual target selection
        # remains a sandbox policy, not autonomous AI acquisition.
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, player).total, 200)
        return run, player, bot

    def test_three_actual_primary_inputs_damage_pharah_and_stop_without_late_hits(self):
        for name, first_hit, damage in (("Trooper", 29, 20), ("Sniper", 129, 100),
                                        ("Heavy Assault", 32, 5)):
            with self.subTest(enemy=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot = self.create(definition)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(first_hit - 1):
                    run.step({})
                self.assertEqual(self.health(run, player).total, 200)
                run.step({})
                self.assertEqual(self.health(run, player).total, 200 - damage)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(80):
                    run.step({})
                self.assertEqual(self.health(run, player).total, 200 - damage)
                self.assertFalse(bot.attack_input)
                self.assertFalse(bot.firing)
                self.assertNotIn(20, bot.body_script.component.held)

    def test_three_actual_primary_inputs_kill_pharah_and_release_on_death(self):
        for name in ("Trooper", "Sniper", "Heavy Assault"):
            with self.subTest(enemy=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot = self.create(definition)
                vitals = self.health(run, player)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                run.step({})
                self.assertTrue(bot.attack_input)
                for _ in range(600):
                    if vitals.dead:
                        break
                    run.step({})
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertEqual(vitals.total, 0)
                run.step({})
                self.assertFalse(bot.attack_input)
                self.assertNotIn(20, bot.body_script.component.held)
                for _ in range(150):
                    run.step({})
                self.assertEqual(vitals.total, 0)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(20, bot.body_script.component.held)

    def test_all_five_release_primary_when_static_wall_blocks_pharah(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                npc_control.start(run.match, player)
                run.step({})
                self.assertTrue(bot.attack_input)
                y = player.mover.position[1]
                wall = World([
                    (-20, y, -20, -20, y, 20, 20, y, 20),
                    (-20, y, -20, 20, y, 20, 20, y, -20),
                    (-10, y - 5, 5, 10, y - 5, 5, 10, y + 5, 5),
                    (-10, y - 5, 5, 10, y + 5, 5, -10, y + 5, 5),
                ])
                with patch.object(run.match, "collision_world", wall):
                    for _ in range(160):
                        run.step({})
                    self.assertIsNone(npc_control.selected(run.match, player))
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(20, bot.body_script.component.held)
                    self.assertEqual(self.health(run, player).total, 200)

    def test_all_five_release_primary_when_pharah_becomes_friendly(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition)
                npc_control.start(run.match, player)
                run.step({})
                self.assertTrue(bot.attack_input)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                bot.team = player.team
                for _ in range(160):
                    run.step({})
                self.assertIsNone(npc_control.selected(run.match, player))
                self.assertFalse(bot.attack_input)
                self.assertNotIn(20, bot.body_script.component.held)
                self.assertEqual(self.health(run, player).total, 200)

    def test_all_five_friendly_bodies_reject_pharah_primary_and_melee(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                run, player, bot = self.create(definition, distance=1.8)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                bot.team = player.team
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                before = self.health(run, bot).total
                self.fire_once(run, player)
                run.step({player: (0, 0, 0x800)})
                for _ in range(70):
                    run.step({})
                self.assertEqual(self.health(run, bot).total, before)
                self.assertFalse(player.client.messages(combat.HIT))
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_heavy_actual_weapon_damages_pharah_then_npc_death_stops_attacks(self):
        run, player, bot = self.start_heavy_weapon()
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, player).total, 185)
        vitals = self.health(run, bot)
        vitals.damage(vitals.total - 60)
        self.assertGreater(vitals.total, 0)
        yaw, pitch = self.aim_at_body(run, player, bot)
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(30):
            if bot.dead:
                break
            run.step({})
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.total, 0)
        at_death = self.health(run, player).total
        self.assertGreater(at_death, 0)
        for _ in range(60):
            run.step({})
        self.assertEqual(self.health(run, player).total, at_death)
        self.assertFalse(bot.attack_input)
        self.assertNotIn(20, bot.body_script.component.held)

    def test_heavy_actual_weapon_can_kill_pharah_without_overkill_or_respawn(self):
        run, player, _ = self.start_heavy_weapon()
        for _ in range(170):
            run.step({})
        vitals = self.health(run, player)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)


if __name__ == "__main__":
    unittest.main()
