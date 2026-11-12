"""Explicit external five-NPC catalog fixtures; transport/AI/client remain separate."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GenjiFixture
from test_genji_ultimate import dash_instance

from ow174.game import combat, mover, npc, npc_control
from ow174.game.collision import World
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
        self.enterContext(patch.object(player.client, "bot_stream", return_value=None, create=True))
        if deflect:
            for frame in range(5):
                run.step({player: (0, 0, 16 if frame == 0 else 0)})
        bot = npc.spawn(run.match, self.catalog[key], (0, 1, distance), team=1, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        run.step({player: (0, 0, 0)})  # establish the selector's authoritative view angles
        return run, player, bot

    def check_damage(self, attack, expected):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name, attack=attack):
                # Keep the eye outside Heavy's authored .7m capsule and aim at
                # the torso; the old .8m fixture clipped its head first.
                run, player, bot = self.scene(key, distance=1.5 if attack == "secondary" else 4)
                before = run.match.combat.vitals[bot.entity].total
                if attack == "blade":
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                for frame in range(110 if attack == "blade" else 60):
                    if attack == "blade":
                        buttons = 32 if frame == 1 else (1 if 70 < frame < 90 else 0)
                    else:
                        buttons = {"primary": 1, "secondary": 2, "strike": 8}[attack] if frame == 0 else 0
                    yaw, pitch = 0, 0
                    if attack in ("primary", "secondary"):
                        x, y, z = bot.position
                        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1.0, z))
                    run.step({player: (yaw, pitch, buttons)})
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - expected)
                if attack in ("primary", "secondary"):
                    self.assertEqual(len(player.client.messages(combat.HIT)), 3)
                    self.assertTrue(all(not m["+0x78"]["+0x8"] & combat.CRITICAL
                                        for m in player.client.messages(combat.HIT)))

    def test_authored_five_body_mover_entries_and_combat_capsules_are_not_fallbacks(self):
        expected = {"1": (1.7, .5, 5.75), "2": (1.9, .5, 5), "3": (1.75, .5, 4.5),
                    "4": (1.5, .5, 14), "5": (2.3, .7, 2.2)}
        for key, values in expected.items():
            with self.subTest(npc=self.catalog[key].name):
                run, _player, bot = self.scene(key)
                data = mover.mover_data(bot.body)
                self.assertIn(bot.body, mover.loaded_mover_table(),
                              "requires explicit authored five-NPC mover runtime; fallback is unaccepted")
                self.assertEqual((data.stand_height, data.stand_radius, data.run_forward), values)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                self.assertEqual((target.height, target.radius), values[:2])

    def test_projectile_extent_expands_authored_npc_capsule_width(self):
        for key, offset, expected in (("1", .55, 87), ("1", .65, 0), ("5", .65, 87), ("5", .81, 0)):
            with self.subTest(npc=self.catalog[key].name, offset=offset):
                run, player, bot = self.scene(key)
                bot.position = (offset, 1, 4)
                height = mover.mover_data(bot.body).stand_height
                yaw, pitch = aim(run.match.combat._eye(player), (0, 1 + height / 2, 4))
                before = run.match.combat.vitals[bot.entity].total
                for frame in range(60):
                    run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - expected)

    def test_assassin_top_cap_admits_projectile_graze_but_rejects_higher_aim(self):
        for height, expected in ((2.55, 174), (2.65, 0)):
            with self.subTest(height=height):
                run, player, bot = self.scene("4")
                yaw, pitch = aim(run.match.combat._eye(player), (0, height, 4))
                before = run.match.combat.vitals[bot.entity].total
                for frame in range(60):
                    run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - expected)

    def test_all_five_melee_hits_and_rejects_wall_range_and_allies(self):
        for key in self.catalog:
            for reason in ("hit", "wall", "range", "team"):
                with self.subTest(npc=self.catalog[key].name, reason=reason):
                    run, player, bot = self.scene(key, distance=4 if reason == "range" else 1.5)
                    if reason == "wall":
                        run.match.collision_world = World(FLOOR + wall(-5, .75, 5, .75))
                    if reason == "team":
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        bot.team = player.team
                    before = run.match.combat.vitals[bot.entity].total
                    self.fire(run, player, 0x800)
                    self.assertEqual(run.match.combat.vitals[bot.entity].total,
                                     before - (30 if reason == "hit" else 0))
                    melee = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0043)
                    self.assertEqual(melee.state(4).counter, 1)

    def test_all_five_primary_strike_and_blade_reject_wall_range_and_allies(self):
        for key in self.catalog:
            for attack, distant, frames in (("primary", 102, 130), ("strike", 20, 60), ("blade", 8, 110)):
                for reason in ("wall", "range", "team"):
                    with self.subTest(npc=self.catalog[key].name, attack=attack, reason=reason):
                        run, player, bot = self.scene(key, distance=distant if reason == "range" else 4)
                        if reason == "wall":
                            run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
                        if reason == "team":
                            run.match.game_map = replace(run.match.game_map, free_for_all=False)
                            bot.team = player.team
                        before = run.match.combat.vitals[bot.entity].total
                        if attack == "blade":
                            player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                        yaw, pitch = aim(run.match.combat._eye(player), (0, 2, bot.position[2]))
                        for frame in range(frames):
                            buttons = ((32 if frame == 0 else (1 if 70 < frame < 90 else 0))
                                       if attack == "blade" else
                                       ((1 if attack == "primary" else 8) if frame == 0 else 0))
                            run.step({player: (yaw, pitch, buttons)})
                        self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                        self.assertFalse(player.client.messages(combat.HIT))
                        if attack == "primary":
                            weapon = next(i for i in player.body_script.component.instances.values()
                                          if i.graph.index == 0x0304)
                            self.assertEqual(weapon.vars[53].value(), 27)
                        if attack == "strike":
                            self.assertGreater(dash_instance(player).state(0).var.value(), 0)
                        if attack == "blade":
                            blade = next(i for i in player.body_script.component.instances.values()
                                         if i.graph.index == 0x0317)
                            self.assertTrue(blade.state(3).active)
                            weapon = next(i for i in player.body_script.component.instances.values()
                                          if i.graph.index == 0x0304)
                            self.assertTrue(any(state.counter for index, state in weapon.states.items()
                                                if index in (66, 67)))

    def test_primary_against_all_five(self):
        self.check_damage("primary", 87)

    def test_secondary_against_all_five(self):
        self.check_damage("secondary", 87)

    def test_secondary_rejects_wall_range_and_allies_for_all_five_npcs(self):
        for key in self.catalog:
            for reason in ("wall", "range", "team"):
                with self.subTest(npc=self.catalog[key].name, reason=reason):
                    run, player, bot = self.scene(key, distance=102 if reason == "range" else 4)
                    if reason == "wall":
                        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
                    if reason == "team":
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        bot.team = player.team
                    fight = run.match.combat
                    before = fight.vitals[bot.entity].total
                    weapon = next(i for i in player.body_script.component.instances.values()
                                  if i.graph.index == 0x0304)
                    x, y, z = bot.position
                    yaw, pitch = aim(fight._eye(player), (x, y + 1.0, z))
                    run.step({player: (yaw, pitch, 2)})
                    self.assertEqual(weapon.vars[53].value(), 27)
                    self.assertEqual(sum(rocket.shooter is player for rocket in fight.rockets), 3)
                    for _ in range(125 if reason == "range" else 115):
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(fight.vitals[bot.entity].total, before)
                    self.assertFalse(player.client.messages(combat.HIT))

    def test_strike_against_all_five(self):
        self.check_damage("strike", 50)

    def test_blade_against_all_five(self):
        self.check_damage("blade", 110)

    def test_trooper_and_heavy_authored_fire_reacquires_genji_after_los_returns(self):
        for key in ("1", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, player, bot = self.scene(key)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                open_world = run.match.collision_world
                blocked_world = World(FLOOR + wall(-5, 2, 5, 2))
                run.match.collision_world = blocked_world
                for _ in range(60):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(self.health(run, player), 200)

                run.match.collision_world = open_world
                for _ in range(90):
                    run.step({})
                    if self.health(run, player) < 200:
                        break
                wounded = self.health(run, player)
                self.assertGreater(wounded, 0)
                self.assertLess(wounded, 200)
                self.assertTrue(bot.attack_input)
                run.match.collision_world = blocked_world
                for _ in range(60):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(self.health(run, player), wounded)

                npc_control.stop(run.match)
                run.match.collision_world = open_world
                for _ in range(60):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(self.health(run, player), wounded)

    def test_primary_and_four_authored_ranged_weapons_trade_damage(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, player, bot = self.scene(key, distance=8 if key == "2" else 4)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for frame in range(240):
                    run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
                    if vitals.total < before and self.health(run, player) < 200:
                        break

                self.assertGreater(self.health(run, player), 0)
                self.assertLess(self.health(run, player), 200)
                self.assertTrue(bot.attack_input)
                self.assertEqual(bot.attack_target, player.entity)
                for _ in range(25):
                    run.step({player: (yaw, pitch, 0)})
                self.assertEqual(vitals.total, before - 87)
                hits = player.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29] * 3)

    def concurrent_scene(self):
        run, player = self.spawn(names=("Genji",))
        match = run.match
        match._pending_bot_scripts.clear()
        self.enterContext(patch.object(player.client, "bot_stream", return_value=None, create=True))
        bots = {
            key: npc.spawn(match, self.catalog[key], position, team=1, yaw=0)
            for key, position in (
                ("1", (0, 1, 4)), ("2", (8, 1, 0)), ("3", (0, 1, -8)),
                ("5", (-6, 1, 0)), ("4", (2, 1, 1.7)),
            )
        }
        return run, player, bots

    def start_manual_enemy(self, run, player, bot):
        x, y, z = bot.position
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, player), bot.entity)

    def test_five_manual_enemies_engage_the_same_genji_in_one_fight(self):
        run, player, bots = self.concurrent_scene()
        match = run.match
        # Keep the same body alive long enough for every authored attack to arrive.
        vitals = combat.Vitals(player.body, 5000)
        match.combat.vitals[player.body] = vitals
        applied_damage = {}
        apply_damage = match.combat._damage

        def observe_damage(shooter, target, *args, **kwargs):
            before = vitals.total
            applied = apply_damage(shooter, target, *args, **kwargs)
            if applied and target.owner is player:
                applied_damage.setdefault(shooter.entity, []).append(before - vitals.total)
            return applied

        self.enterContext(patch.object(match.combat, "_damage", observe_damage))

        def shoot_trooper():
            x, y, z = bots["1"].position
            yaw, pitch = aim(match.combat._eye(player), (x, y + 1, z))
            for frame in range(60):
                run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})

        for key in ("1", "2", "3", "5"):
            self.start_manual_enemy(run, player, bots[key])
        shoot_trooper()
        self.start_manual_enemy(run, player, bots["4"])
        for _ in range(260):
            run.step({})
            if len(applied_damage) == 5 and bots["4"].entity in match.combat.assassin_victims:
                break
        self.assertEqual(match.combat.assassin_victims[bots["4"].entity].target, player.body)
        self.assertTrue(all(bot.attack_input and bot.attack_target == player.entity for bot in bots.values()))
        self.assertEqual(
            {key: applied_damage[bot.entity][0] for key, bot in bots.items()},
            {"1": 20, "2": 2, "3": 100, "4": 25, "5": 5},
        )
        self.assertAlmostEqual(vitals.total, 5000 - sum(sum(hits) for hits in applied_damage.values()))

        self.assertEqual(npc_control.stop(match), 5)
        stopped_health = vitals.total
        for _ in range(220):
            run.step({})
        self.assertNotIn(bots["4"].entity, match.combat.assassin_victims)
        self.assertTrue(all(not bot.attack_input and bot.attack_target is None for bot in bots.values()))
        shoot_trooper()
        self.assertEqual(vitals.total, stopped_health)
        hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
        self.assertEqual(
            [(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
            [(bots["1"].entity, 29)] * 6,
        )

    def test_five_manual_enemies_can_defeat_genji_at_normal_health(self):
        run, player, bots = self.concurrent_scene()
        match = run.match
        fight = match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 200)
        applied_damage = {}
        apply_damage = fight._damage

        def observe_damage(shooter, target, *args, **kwargs):
            before = vitals.total
            applied = apply_damage(shooter, target, *args, **kwargs)
            if applied and target.owner is player:
                applied_damage.setdefault(shooter.entity, []).append(before - vitals.total)
            return applied

        self.enterContext(patch.object(fight, "_damage", observe_damage))
        # Give the ranged windups a head start so the encounter exercises all five sources.
        for key in ("3", "2"):
            self.start_manual_enemy(run, player, bots[key])
            for _ in range(35):
                run.step({})
        for key in ("1", "5"):
            self.start_manual_enemy(run, player, bots[key])

        trooper = bots["1"]
        x, y, z = trooper.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, 2)})
        self.start_manual_enemy(run, player, bots["4"])
        for _ in range(260):
            run.step({})
            if vitals.dead:
                break

        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, bots["3"].entity)
        self.assertEqual(
            {key: applied_damage[bot.entity][0] for key, bot in bots.items()},
            {"1": 20, "2": 2, "3": 47, "4": 25, "5": 5},
        )
        self.assertAlmostEqual(sum(sum(hits) for hits in applied_damage.values()), 200)
        hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits], [(trooper.entity, 29)] * 3)

        run.step({})
        self.assertTrue(all(not bot.attack_input for bot in bots.values()))
        self.assertNotIn(bots["4"].entity, fight.assassin_victims)
        self.assertEqual(npc_control.stop(match), 5)
        self.assertTrue(all(bot.attack_target is None for bot in bots.values()))

    def test_four_ranged_npcs_release_on_death_and_reacquire_respawn(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, player, bot = self.scene(key)
                player.spawn = ((0, 1, 0), 0)
                old_body = player.body
                old_script = player.body_script
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(900):
                    run.step({})
                    if run.match.combat.is_dead(player):
                        break
                self.assertTrue(run.match.combat.is_dead(player))
                self.assertEqual(run.match.combat.vitals[old_body].killer, bot.entity)
                for _ in range(650):
                    run.step({})
                    if player.body != old_body:
                        break
                    self.assertFalse(bot.attack_input)
                self.assertNotEqual(player.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertEqual(bot.attack_target, player.entity)
                self.assertEqual(self.health(run, player), 200)
                for _ in range(240):
                    run.step({})
                    if self.health(run, player) < 200:
                        break
                self.assertTrue(bot.attack_input)
                self.assertGreater(self.health(run, player), 0)
                self.assertLess(self.health(run, player), 200)

    def test_trooper_and_heavy_authored_fire_is_deflected(self):
        for key, expected in (("1", 60), ("5", 35)):
            with self.subTest(npc=self.catalog[key].name):
                run, player, bot = self.scene(key, deflect=True)
                before = run.match.combat.vitals[bot.entity].total
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(60):
                    run.step({})
                self.assertEqual(self.health(run, player), 200)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - expected)

    def test_sniper_authored_weapon_damages_genji_and_stops(self):
        run, player, bot = self.scene("3")
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(200):
            run.step({})
        self.assertLess(self.health(run, player), 200)
        self.assertIsNotNone(bot.body_script)
        npc_control.stop(run.match)
        before = self.health(run, player)
        for _ in range(80):
            run.step({})
        self.assertEqual(self.health(run, player), before)

    def test_sniper_shot_respects_live_deflect_facing_and_expiry(self):
        cases = (("front", 50, 0, 100), ("rear", 50, -32768, 0), ("expired", 0, 0, 0))
        for name, press_frame, facing, reflected in cases:
            with self.subTest(case=name):
                run, player, bot = self.scene("3")
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for frame in range(145):
                    run.step({player: (facing, 0, 16 if frame == press_frame else 0)})
                self.assertEqual(self.health(run, player), 200 if reflected else 100)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, 300 - reflected)
                self.assertEqual(player.body_script.component.entity_var(342).value(), reflected)
                hits = player.client.messages(combat.HIT)
                self.assertEqual(len(hits), bool(reflected))
                if reflected:
                    self.assertEqual(hits[0]["+0x78"]["+0x4"], reflected)

    def test_enforcer_authored_ring_damage_ammo_and_stop_against_genji(self):
        for index, damage in enumerate((38, 76, 114, 152)):
            with self.subTest(index=index):
                run, player, bot = self.scene("2", distance=8)
                bot.body_script.component.set(9948, index)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                maximum = 0
                for _ in range(100):
                    run.step({})
                    maximum = max(maximum, len(run.match.combat.rockets))
                self.assertEqual(maximum, 25)
                self.assertEqual(self.health(run, player), 200 - damage)
                weapon = next(i for i in bot.body_script.component.instances.values()
                              if i.graph.index == 0x13EB)
                self.assertEqual(weapon.state(14).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 3)
                npc_control.stop(run.match)
                before = self.health(run, player)
                for _ in range(100):
                    run.step({})
                self.assertEqual(self.health(run, player), before)
                self.assertFalse(bot.attack_input)
                self.assertFalse(run.match.combat.rockets)

    def test_enforcer_frontal_pellets_are_reflected_and_rear_pellets_damage_genji(self):
        for facing, damage, reflected in ((0, 0, 50), (-32768, 38, 0)):
            with self.subTest(facing=facing):
                run, player, bot = self.scene("2", distance=8)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                before = run.match.combat.vitals[bot.entity].total
                npc_control.start(run.match, player)
                for frame in range(100):
                    run.step({player: (facing, 0, 16 if frame == 0 else 0)})
                self.assertEqual(self.health(run, player), 200 - damage)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - reflected)
                self.assertEqual(player.body_script.component.entity_var(342).value(), reflected)
                self.assertEqual(len(player.client.messages(combat.HIT)), 25 if reflected else 0)

    def test_enforcer_manual_default_and_invalid_damage_index(self):
        run, player, bot = self.scene("2", distance=8)
        self.assertIsNone(bot.body_script.component.entity_var(9948).value())
        npc_control.start(run.match, player)
        self.assertEqual(bot.body_script.component.entity_var(9948).value(), 0)
        for index in (-1, 4, True, 0.5):
            with self.subTest(index=index):
                run, player, bot = self.scene("2", distance=8)
                bot.body_script.component.set(9948, index)
                with self.assertRaises(ValueError):
                    npc_control.start(run.match, player)
                self.assertFalse(bot.firing)
                self.assertIsNone(bot.attack_target)

    def test_ranged_npcs_release_and_reacquire_manual_input(self):
        for key in ("1", "2", "3", "5"):
            for reason in ("wall", "range", "team"):
                with self.subTest(npc=self.catalog[key].name, reason=reason):
                    distance = 8 if key == "2" else 4
                    run, player, bot = self.scene(key, distance=distance)
                    match = run.match
                    open_world = match.collision_world
                    match.game_map = replace(match.game_map, free_for_all=False)
                    enemy_team = bot.team
                    position = bot.position
                    self.assertEqual(npc_control.start(match, player), bot.entity)
                    run.step({})
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(self.health(run, player), 200)

                    if reason == "wall":
                        middle = distance / 2
                        match.collision_world = World(FLOOR + wall(-5, middle, 5, middle))
                    elif reason == "range":
                        bot.position = (0, 1, npc_control.AIM_RANGE + 20)
                    else:
                        bot.team = player.team
                    for _ in range(160):
                        run.step({})
                        self.assertFalse(bot.attack_input)
                    self.assertEqual(self.health(run, player), 200)
                    self.assertFalse(match.combat.rockets)
                    self.assertEqual(bot.attack_target, player.entity)

                    match.collision_world = open_world
                    bot.position = position
                    bot.team = enemy_team
                    for _ in range(200):
                        run.step({})
                        if self.health(run, player) < 200:
                            break
                    self.assertTrue(bot.attack_input)
                    self.assertLess(self.health(run, player), 200)

    def test_enforcer_two_authored_reloads_continue_against_explicit_durable_genji_fixture(self):
        run, player, bot = self.scene("2", distance=8)
        # Explicit durability override isolates cadence from normal death/respawn.
        vitals = combat.Vitals(player.body, 5000)
        run.match.combat.vitals[player.body] = vitals
        npc_control.start(run.match, player)
        for _ in range(1200):
            run.step({})
        weapon = next(i for i in bot.body_script.component.instances.values()
                      if i.graph.index == 0x13EB)
        self.assertEqual(weapon.state(14).counter, 8)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertEqual(vitals.total, 4696)  # eight volleys of19 hits at2 damage

    def test_all_five_actual_npc_deaths_reset_dash_and_retain_corpses(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, player, bot = self.scene(key)
                self.enterContext(patch.object(player.client, "queue_bot_death", create=True))
                self.enterContext(patch.object(player.client, "queue_bot_death_update", create=True))
                vitals = run.match.combat.vitals[bot.entity]
                vitals.damage(vitals.total - 100)
                run.match.combat._sync_body_health(bot, vitals)
                for frame in range(40):
                    run.step({player: (0, 0, 8 if frame == 0 else 0)})
                self.assertGreater(dash_instance(player).state(0).var.value(), 7)
                # Aim within each actual capsule, including the1.5m Assassin.
                height = mover.mover_data(bot.body).stand_height
                yaw, pitch = aim(run.match.combat._eye(player), (0, 1 + height / 2, 4))
                for frame in range(60):
                    run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
                self.assertTrue(bot.dead)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(vitals.total, 0)
                self.assertEqual(dash_instance(player).state(0).var.value(), 0)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertTrue(bot.retain_corpse)


if __name__ == "__main__":
    unittest.main()
