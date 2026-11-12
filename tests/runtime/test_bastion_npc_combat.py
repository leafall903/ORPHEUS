"""Actual Talon definitions/graphs from an explicit external read-only bundle.

Static collision and transport adapters remain fixtures. Directed logical input
is not AI acquisition/navigation acceptance. Never relabel training bots as Talon.
"""

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_bastion_combat as bastion_tests
import test_mccree_flashbang as flashbang_tests
from test_game_combat import aim

from ow174.game import combat, npc, npc_control, sniper_reposition, stats, zarya_barriers
from ow174.game import mover as mover_module
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.driver import SOLDIER
from ow174.game.world import HEALTH_POOL

NPC_DATA = os.environ.get("ORPHEUS_NPC_DATA_DIR")
NPC_MOVERS = os.environ.get("ORPHEUS_NPC_MOVERS_PATH")
NPC_SHAPES = {"1": (1.7, 0.5, 5.75), "2": (1.9, 0.5, 5.0),
              "3": (1.75, 0.5, 4.5), "4": (1.5, 0.5, 14.0), "5": (2.3, 0.7, 2.2)}


def clear_graphs():
    graph.graph.cache_clear()
    graph._records.cache_clear()
    graph._data.cache_clear()


@unittest.skipUnless(NPC_DATA, "set ORPHEUS_NPC_DATA_DIR to the verified five-NPC data directory")
class BastionNpcCombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(NPC_DATA)
        cls.catalog = npc.load_catalog(cls.directory / "pve_npcs_174.json")
        if not NPC_MOVERS:
            raise ValueError("set ORPHEUS_NPC_MOVERS_PATH to the verified43-row mover table")
        cls.mover_path = Path(NPC_MOVERS)
        table = json.loads(cls.mover_path.read_text(encoding="utf-8"))
        for definition in cls.catalog.values():
            if f"0x{definition.body:016X}" not in table:
                raise ValueError(f"actual NPC mover input missing: {definition.name}")
        cls.mover_patch = patch.object(mover_module, "MOVERS_PATH", cls.mover_path)
        cls.mover_patch.start()
        mover_module._clear_movers()
        cls.bundle_patch = patch.object(graph, "GRAPHS_PATH", cls.directory / "game_graphs_174.json.gz")
        cls.bundle_patch.start()
        clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.bundle_patch.stop()
        clear_graphs()
        cls.mover_patch.stop()
        mover_module._clear_movers()

    def fixture(self, key, mode="recon", distance=8, team=1):
        case = self.case = bastion_tests.BastionCombatTests()
        case.fixture(enemy_position=(10, 1, 0))
        for actor in case.run.players:
            actor.client.bot_stream = lambda body: None  # no projectile presentation claim
            actor.client.queue_bot_death = lambda entity, event: None
            actor.client.queue_bot_death_update = lambda entity, event: None
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        if team == 0:
            case.run.match.game_map = replace(case.run.match.game_map,
                                             free_for_all=False, team_sizes=(6, 6))
            case.player.team = 0
        bot = self.bot = npc.spawn(case.run.match, self.catalog[key], (0, 1, distance),
                                  team=team, yaw=-32768)
        data = mover_module.loaded_mover_table()[bot.body]
        expected_height, expected_radius, expected_speed = NPC_SHAPES[key]
        self.assertAlmostEqual(data.stand_height, expected_height, places=6)
        self.assertAlmostEqual(data.stand_radius, expected_radius, places=6)
        self.assertAlmostEqual(data.run_forward, expected_speed, places=6)
        target = case.target(bot)
        self.assertEqual((target.height, target.radius), (data.stand_height, data.stand_radius))
        case.command_aim = aim(case.fight._eye(case.player),
                               (bot.position[0], bot.position[1] + target.height / 2, bot.position[2]))
        self.assertIsNotNone(bot.body_script)
        self.assertFalse(bot.body_script.component.strict)  # explicit evidence limit
        self.assertEqual(bot.body_script.component.entity_var(253).value(),
                         case.fight.vitals[bot.entity].total)
        instance = next(iter(bot.body_script.component.instances.values()))
        for query in ("STU_9B7648C3", "STU_32E6E4D8", "STU_7D7D2470"):
            self.assertEqual(instance.evaluate({"$": query, "m_entity": {"$": "STU_B5A0CAF0"}}),
                             case.fight.vitals[bot.entity].total)
        case.step(0, 8)
        return case.fight.vitals[bot.entity]

    def test_tank_body_replacement_retires_flight_before_all_five_actual_targets(self):
        for key in self.catalog:
            for leased in (True, False):
                with self.subTest(npc=self.catalog[key].name, leased=leased):
                    health = self.fixture(key, "tank", distance=60)
                    case = self.case
                    initial = health.total
                    if leased:
                        case.step(1)
                    else:
                        with patch.object(case.fight.projectiles, "lease_id", return_value=None):
                            case.step(1)
                    self.assertEqual(len(case.fight.rockets), 1)
                    self.assertEqual(case.fight.rockets[0].shot is not None, leased)
                    self.assertTrue(case.run.match.switch_hero(case.player, heroes()[SOLDIER]))
                    case.player.mover.position[:] = (10, 1, 0)
                    case.step(0, 90)
                    self.assertEqual(health.total, initial)
                    self.assertFalse(health.dead)
                    self.assertFalse(case.fight.rockets)

    def test_posthumous_tank_hits_all_five_actual_targets_with_and_without_lease(self):
        for key in self.catalog:
            for leased in (True, False):
                with self.subTest(npc=self.catalog[key].name, leased=leased):
                    health = self.fixture(key, "tank", distance=60)
                    case = self.case
                    initial = health.total
                    if leased:
                        case.step(1)
                    else:
                        with patch.object(case.fight.projectiles, "lease_id", return_value=None):
                            case.step(1)
                    self.assertEqual(len(case.fight.rockets), 1)
                    source = case.player.body
                    case.fight._damage(case.enemy, case.target(case.player), 10000,
                                       False, None, case.run.frame)
                    case.step(1 | 8 | 32, 90)
                    self.assertTrue(case.health(case.player).dead)
                    self.assertAlmostEqual(health.total, max(0, initial - 205), places=4)
                    if health.dead:
                        self.assertEqual(health.killer, source)
                    self.assertFalse(case.fight.rockets)

    def test_recon_sentry_melee_tank_damage_all_five_actual_bodies(self):
        for key in self.catalog:
            for mode, buttons, frames, expected in (("recon", 1, 10, 40),
                                                    ("sentry", 1, 10, 75),
                                                    ("melee", 0x800, 30, 30),
                                                    ("tank", 1, 1, 205)):
                with self.subTest(npc=self.catalog[key].name, mode=mode):
                    vitals = self.fixture(key, mode, 1.4 if mode == "melee" else 8)
                    initial = vitals.total
                    script = self.case.player.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    self.case.step(buttons, frames)
                    if mode == "tank":
                        self.case.step(0, 20)
                    self.assertAlmostEqual(initial - vitals.total, min(initial, expected), places=4)
                    self.assertAlmostEqual(script.component.entity_var(342).value(),
                                           0 if mode == "tank" else min(initial, expected), places=4)

    def test_all_four_weapons_kill_all_five_talon_targets_once(self):
        for key in self.catalog:
            for mode, buttons in (("recon", 1), ("sentry", 1), ("melee", 0x800), ("tank", 1)):
                with self.subTest(npc=self.catalog[key].name, mode=mode):
                    health = self.fixture(key, mode, 1.4 if mode == "melee" else 8)
                    case, bot = self.case, self.bot
                    health.damage(health.total - 1)
                    self.assertFalse(health.dead)
                    self.assertEqual(health.total, 1)
                    case.step(buttons)
                    for _ in range(40):
                        if health.dead:
                            break
                        case.step()
                    self.assertTrue(health.dead)
                    self.assertTrue(bot.dead)
                    self.assertEqual(health.killer, case.player.body)
                    self.assertEqual(case.player.stats.value(stats.ELIMINATIONS), 1)
                    self.assertEqual(case.player.stats.value(stats.HERO_DAMAGE), 1)
                    hits = case.player.client.messages(combat.HIT)
                    self.assertEqual(len(hits), 1)
                    self.assertTrue(hits[0]["+0x78"]["+0x8"] & combat.KILLING)
                    death_tick = health.death_tick
                    case.step(0, 70)
                    case.step(buttons)
                    case.step(0, 40)
                    self.assertEqual(health.total, 0)
                    self.assertEqual(health.death_tick, death_tick)
                    self.assertEqual(case.player.stats.value(stats.ELIMINATIONS), 1)
                    self.assertEqual(case.player.stats.value(stats.HERO_DAMAGE), 1)
                    self.assertEqual(case.player.client.messages(combat.HIT), hits)
                    self.assertIn(bot, case.run.match.bots)
                    self.assertTrue(bot.retain_corpse)

    def test_sentry_kills_all_five_and_retains_their_corpses(self):
        for key in self.catalog:
            with self.subTest(npc=self.catalog[key].name):
                vitals = self.fixture(key, "sentry")
                for _ in range(700):
                    self.case.step(1)
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertTrue(self.bot.dead)
                self.assertTrue(self.bot.retain_corpse)
                self.case.step(0, 220)
                self.assertIn(self.bot, self.case.run.match.bots)
                self.assertFalse(any(who is self.bot for _, who in self.case.fight.respawns))
                self.assertEqual(vitals.total, 0)
                entity = self.bot.entity
                self.assertEqual(npc.remove_spawned(self.case.run.match), (entity,))
                self.assertNotIn(entity, self.case.fight.vitals)

    def test_damage_earned_tank_spends_rejects_empty_and_reearns_on_actual_heavy(self):
        vitals = self.fixture("5", "sentry")
        case = self.case
        script = case.player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)  # clear initial passive points; never fill the meter
        cost = script.component.entity_var(338).value()

        def earn(target):
            initial = target.total
            for _ in range(700):
                case.step(1)
                if script.component.entity_var(342).value() >= cost:
                    break
            self.assertGreaterEqual(initial - target.total, cost)
            self.assertLess(initial - target.total, cost + 15)
            self.assertEqual(script.component.entity_var(342).value(), cost)

        earn(vitals)
        case.step(32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(script.component.entity_var(1216).value(), 5)
        case.step(0, 110)
        before = vitals.total
        case.step(1)
        case.step(0, 30)
        self.assertAlmostEqual(before - vitals.total, 205, places=4)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        case.step(0, 650)
        self.assertEqual(script.component.entity_var(1216).value(), 0)
        case.step(32)
        self.assertEqual(script.component.entity_var(1216).value(), 0)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        npc.remove_spawned(case.run.match)
        target = npc.spawn(case.run.match, self.catalog["5"], (0, 1, 8), team=1)
        case.configure()
        earn(case.fight.vitals[target.entity])
        case.step(32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(script.component.entity_var(1216).value(), 5)

    def test_heavy_exchange_and_default_repair_earn_tank_from_zero(self):
        target = self.fixture("5", "sentry")
        case = self.case
        script = case.player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        meter = script.component.entity_var(342)
        mode = script.component.entity_var(1216)
        cost = script.component.entity_var(338).value()
        before = target.total
        for _ in range(700):
            if meter.value() >= cost - 35:
                break
            case.step(1)
        self.assertGreaterEqual(meter.value(), cost - 35)
        self.assertLess(meter.value(), cost)
        self.assertAlmostEqual(before - target.total, meter.value(), places=4)
        case.step(32)
        self.assertEqual(mode.value(), 2)

        self.assertEqual(npc_control.start(case.run.match, case.player), self.bot.entity)
        own = case.health(case.player)
        before = own.total
        for _ in range(600):
            if before - own.total >= 40:
                break
            case.step()
        self.assertGreaterEqual(before - own.total, 40)
        self.assertFalse(own.dead)
        self.assertEqual(npc_control.stop(case.run.match), 1)
        before = own.total
        case.step(2, 45)
        self.assertGreater(own.total, before)
        self.assertEqual(meter.value(), cost)
        case.step(0, 20)
        case.step(32)
        self.assertEqual(mode.value(), 5)
        self.assertEqual(meter.value(), 0)
        case.step(0, 95)
        self.assertEqual(mode.value(), 6)
        before = target.total
        case.step(1)
        case.step(0, 25)
        self.assertAlmostEqual(before - target.total, 205, places=4)
        self.assertEqual(meter.value(), 0)
        case.step(0, 600)
        self.assertEqual(mode.value(), 0)
        self.assertEqual(meter.value(), 0)

    def test_manual_trooper_enforcer_sniper_heavy_weapons_damage_bastion(self):
        for key in ("1", "2", "3", "5"):
            for mode in ("recon", "sentry", "tank"):
                with self.subTest(npc=self.catalog[key].name, mode=mode):
                    own = self.fixture(key, mode)
                    initial = self.case.health(self.case.player).total
                    script = self.bot.body_script
                    self.assertEqual(npc_control.start(self.case.run.match, self.case.player),
                                     self.bot.entity)
                    for _ in range(10):
                        # Production manual engagement drives extracted logical input;
                        # no autonomous acquisition or special attack is claimed.
                        self.case.step(0, 30)
                        if self.case.health(self.case.player).total < initial:
                            break
                    # Heavy's sustained manual input yields seven authored 5-HP
                    # rounds in this first nonempty window; armour halves each.
                    expected = {"1": 45, "2": 25, "3": 95, "5": 17.5} if mode == "recon" else {
                        "1": 33, "2": 19.2 if mode == "sentry" else 20, "3": 75, "5": 14}
                    self.assertAlmostEqual(initial - self.case.health(self.case.player).total,
                                           expected[key], places=4)
                    self.assertIs(self.bot.body_script, script)
                    self.assertFalse(own.dead)
                    if key == "3":
                        self.assertTrue(any(i.graph.index == 0x1122
                                            for i in script.component.instances.values()))

    def test_repair_charge_after_actual_trooper_enforcer_sniper_heavy_incoming_damage(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                self.fixture(key)
                case = self.case
                script = case.player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                script.component.set(2426, True)  # declared eligibility profile only
                npc_control.start(case.run.match, case.player)
                self.wait_for_incoming_damage()
                npc_control.stop(case.run.match)
                before = case.health(case.player).total
                self.assertLess(before, 300)
                case.step(2, 80)
                restored = case.health(case.player).total - before
                self.assertGreater(restored, 0)
                self.assertAlmostEqual(script.component.entity_var(342).value(), restored, places=4)

    def test_horizontal_ray_misses_actual_assassin_but_center_aim_hits(self):
        health = self.fixture("4")
        initial = health.total
        self.case.run.step({self.case.player: (0, 0, 1)})
        self.case.step(0, 20)
        self.assertEqual(health.total, initial)
        self.case.step(1, 10)
        self.assertEqual(initial - health.total, 40)

    def test_all_four_attacks_preserve_friendly_npc_health(self):
        for key in self.catalog:
            for mode, buttons, frames in (("recon", 1, 10), ("sentry", 1, 10),
                                          ("melee", 0x800, 30), ("tank", 1, 1)):
                with self.subTest(npc=self.catalog[key].name, mode=mode):
                    vitals = self.fixture(key, mode, 1.4 if mode == "melee" else 8, team=0)
                    before = dict(vitals.now)
                    self.case.step(buttons, frames)
                    self.case.step(0, 20)
                    self.assertEqual(vitals.now, before)

    def wait_for_incoming_damage(self, limit=300):
        before = self.case.health(self.case.player).total
        for _ in range(limit):
            self.case.step()
            if self.case.health(self.case.player).total < before:
                return
        self.fail("actual NPC weapon did not damage Bastion through manual engagement")

    def test_actual_assassin_pin_interrupts_each_bastion_form_and_stop_releases(self):
        for mode, entry in (("recon", 20), ("sentry", 15), ("tank", 15)):
            with self.subTest(mode=mode):
                enemy = self.fixture("4", mode, 2)
                case, bot = self.case, self.bot
                own = case.health(case.player)
                initial, enemy_initial = own.total, enemy.total
                self.assertEqual(npc_control.start(case.run.match, case.player), bot.entity)
                case.step()
                self.assertAlmostEqual(initial - own.total, entry, places=5)
                self.assertEqual(case.fight.pins, {bot.entity: case.player.body})
                self.assertTrue(case.fight.is_stunned(case.player))
                case.step(1, 90)
                self.assertLess(own.total, initial - entry)
                self.assertEqual(enemy.total, enemy_initial)
                before = own.total
                npc_control.stop(case.run.match)
                self.assertFalse(case.fight.is_stunned(case.player))
                case.step(0, 8)
                self.assertEqual(own.total, before)
                case.step(1, 20)
                self.assertLess(enemy.total, enemy_initial)

    def test_assassin_ongoing_pin_kills_and_retires_control(self):
        self.fixture("4", distance=2)
        case, bot = self.case, self.bot
        health = case.health(case.player)
        health.damage(275)
        case.fight._sync_body_health(case.player, health)
        self.assertEqual(npc_control.start(case.run.match, case.player), bot.entity)
        case.step()
        self.assertEqual(health.total, 5)
        self.assertEqual(case.fight.pins, {bot.entity: case.player.body})
        for _ in range(200):
            case.step(1)
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(case.fight.pins)
        self.assertFalse(case.fight.is_stunned(case.player))
        case.step(1, 30)
        self.assertFalse(case.player.client.messages(combat.HIT))
        self.assertFalse(bot.attack_input)

    def test_sniper_reposition_waits_moves_and_resumes_bidirectional_fire(self):
        enemy = self.fixture("3", distance=2.5)
        case, bot = self.case, self.bot
        start = tuple(bot.position)
        health = case.health(case.player)
        self.assertEqual(npc_control.start(case.run.match, case.player, reposition=True), bot.entity)
        state = sniper_reposition.reposition_state(bot)
        wait = sniper_reposition.reposition_wait(state)
        case.step(0, 20)
        self.assertTrue(wait.active)
        self.assertFalse(state.active)
        self.assertEqual(tuple(bot.position), start)
        self.assertEqual(health.total, 300)
        for _ in range(30):
            case.step()
            if state.active:
                break
        destination, speed = state.move_request()
        self.assertEqual(speed, 20)
        self.assertAlmostEqual(destination.z - start[2], 4, places=4)
        for _ in range(45):
            case.step()
            if not state.active:
                break
        self.assertFalse(state.active)
        self.assertAlmostEqual(bot.position[2], destination.z, delta=0.1)
        self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, 300)
        for _ in range(190):
            case.step()
            if health.total < 300:
                break
        self.assertEqual(300 - health.total, 95)
        npc_control.stop(case.run.match)
        case.command_aim = aim(case.fight._eye(case.player), (0, bot.position[1] + 1, bot.position[2]))
        before = enemy.total
        case.step(1, 10)
        self.assertLess(enemy.total, before)

    def test_sniper_reposition_rejects_no_route_and_stop_aborts_windup(self):
        for phase in ("blocked", "windup", "moving"):
            with self.subTest(phase=phase):
                self.fixture("3", distance=2.5)
                case, bot = self.case, self.bot
                start = tuple(bot.position)
                if phase == "blocked":
                    case.wall(3.1)
                    with self.assertRaisesRegex(ValueError, "clear destination"):
                        npc_control.start(case.run.match, case.player, reposition=True)
                else:
                    selected = npc_control.start(case.run.match, case.player, reposition=True)
                    self.assertEqual(selected, bot.entity)
                    case.step(0, 15)
                    self.assertTrue(sniper_reposition.repositioning(bot))
                    if phase == "moving":
                        for _ in range(35):
                            case.step()
                            if bot.position[2] > start[2] + 0.5:
                                break
                        self.assertGreater(bot.position[2], start[2])
                        start = tuple(bot.position)
                    npc_control.stop(case.run.match)
                case.step(0, 100)
                self.assertFalse(sniper_reposition.repositioning(bot))
                self.assertEqual(tuple(bot.position), start)
                self.assertEqual(case.health(case.player).total, 300)

    def test_sniper_reposition_cooldown_rejects_recast_then_reuses_same_graph(self):
        self.fixture("3", distance=2.5)
        case, bot = self.case, self.bot
        npc_control.start(case.run.match, case.player, reposition=True)
        case.step(0, 65)
        state = sniper_reposition.reposition_state(bot)
        self.assertFalse(state.active)
        self.assertGreater(bot.position[2], 6)
        npc_control.stop(case.run.match)
        case.player.mover.position[:] = (0, 1, bot.position[2] - 2.5)
        case.player.mover.body = None
        case.step(0, 8)
        with self.assertRaisesRegex(ValueError, "ready state"):
            npc_control.start(case.run.match, case.player, reposition=True)
        case.step(0, 380)
        self.assertEqual(npc_control.start(case.run.match, case.player, reposition=True), bot.entity)
        case.step(0, 65)
        self.assertGreater(bot.position[2], 10)

    def test_heavy_charge_captures_and_wall_damage_uses_bastion_mitigation(self):
        for mode, loss in (("recon", 295), ("sentry", 235), ("tank", 235)):
            with self.subTest(mode=mode):
                self.fixture("5", mode, distance=9)
                case, bot = self.case, self.bot
                case.wall(-2.5)
                health = case.health(case.player)
                before = health.total
                self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
                case.step(0, 49)
                self.assertEqual(bot.position[2], 9)
                for _ in range(8):
                    case.step()
                    if bot.entity in case.fight.heavy_drives:
                        break
                self.assertAlmostEqual(bot.velocity[2], -15, delta=0.063)
                for _ in range(70):
                    case.step()
                    if bot.entity in case.fight.pins:
                        break
                self.assertEqual(case.fight.pins.get(bot.entity), case.player.body)
                self.assertEqual(health.total, before)
                for _ in range(50):
                    case.step(1)
                    if bot.entity not in case.fight.heavy_drives:
                        break
                self.assertNotIn(bot.entity, case.fight.pins)
                self.assertAlmostEqual(before - health.total, loss, places=4)
                self.assertLess(case.player.mover.position[2], 0)
                instances = bot.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x1408)
                self.assertGreater(instance.state(41).remaining(), 14)

    def test_heavy_charge_wall_kill_retires_capture_in_all_bastion_forms(self):
        for mode in ("recon", "sentry", "tank"):
            with self.subTest(mode=mode):
                self.fixture("5", mode, distance=9)
                case, bot = self.case, self.bot
                case.wall(-2.5)
                health = case.health(case.player)
                health.damage(105)
                case.fight._sync_body_health(case.player, health)
                self.assertEqual(health.total, 200)
                body = case.player.body
                self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
                for _ in range(135):
                    case.step()
                    if case.fight.pins.get(bot.entity) == body:
                        break
                self.assertEqual(case.fight.pins.get(bot.entity), body)
                self.assertEqual(health.total, 200)
                for _ in range(50):
                    case.step(1)
                    if health.dead:
                        break
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, bot.entity)
                self.assertNotIn(bot.entity, case.fight.heavy_drives)
                self.assertNotIn(bot.entity, case.fight.pins)
                self.assertFalse(case.fight.is_stunned(case.player))
                self.assertFalse(case.player.client.messages(combat.HIT))
                case.step(1, 30)
                self.assertEqual(case.player.body, body)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(bot.attack_input)

    def test_heavy_charge_stop_releases_without_wall_damage_and_rejects_immediate_recast(self):
        self.fixture("5", distance=8)
        case, bot = self.case, self.bot
        self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
        before = case.health(case.player).total
        for _ in range(115):
            case.step()
            if bot.entity in case.fight.pins:
                break
        self.assertEqual(case.fight.pins.get(bot.entity), case.player.body)
        self.assertEqual(npc_control.stop(case.run.match), 1)
        self.assertFalse(case.fight.is_stunned(case.player))
        self.assertFalse(case.fight.heavy_drives)
        after = tuple(bot.position)
        self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
        case.step(0, 65)
        self.assertEqual(tuple(bot.position), after)
        self.assertEqual(case.health(case.player).total, before)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        npc_control.stop(case.run.match)
        for _ in range(970):
            if instance.state(41).remaining() == 0:
                break
            case.step()
        self.assertEqual(instance.state(41).remaining(), 0)
        self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
        for _ in range(70):
            case.step()
            if bot.entity in case.fight.heavy_drives:
                break
        self.assertIn(bot.entity, case.fight.heavy_drives)

    def test_heavy_charge_grazes_a_side_target_once_and_stops_at_nearer_cover(self):
        self.fixture("5", distance=9)
        case, bot = self.case, self.bot
        side = case.bystander
        side.mover.position[:] = (1.3, 1, 8.0)
        side.mover.body = None
        case.step(0, 5)
        side_health = case.health(side)
        before = side_health.total
        self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
        for _ in range(65):
            case.step()
            if bot.entity in case.fight.heavy_drives:
                break
        case.wall(4)
        case.step(0, 50)
        self.assertAlmostEqual(before - side_health.total, 50, places=4)
        self.assertGreater(side.mover.position[0], 1.3)
        self.assertGreater(bot.position[2], 4)
        self.assertEqual(case.health(case.player).total, 300)
        self.assertFalse(case.fight.pins)
        self.assertFalse(case.fight.heavy_drives)

    def test_bastion_lethal_fire_retires_heavy_drive_and_prevents_contact(self):
        health = self.fixture("5", "tank", distance=12)
        case, bot = self.case, self.bot
        for pool in health.now:
            health.now[pool] = 0.0
        health.now[HEALTH_POOL] = 1.0
        self.assertEqual(npc_control.start(case.run.match, case.player, charge=True), bot.entity)
        for _ in range(70):
            case.step()
            if bot.entity in case.fight.heavy_drives:
                break
        self.assertIn(bot.entity, case.fight.heavy_drives)
        case.step(1)
        case.step(0, 30)
        self.assertTrue(bot.dead)
        self.assertFalse(case.fight.heavy_drives)
        self.assertFalse(case.fight.pins)
        self.assertEqual(case.health(case.player).total, 300)

    def test_trooper_and_heavy_melee_hit_each_bastion_form_once_per_press(self):
        for key, raw in (("1", 30), ("5", 80)):
            for form, scalar in (("recon", 1.0), ("sentry", 0.8), ("tank", 0.8)):
                with self.subTest(npc=key, form=form):
                    self.fixture(key, form, distance=2)
                    case, bot = self.case, self.bot
                    health = case.health(case.player)
                    before = health.total
                    position = tuple(case.player.mover.position)
                    self.assertEqual(npc_control.start(case.run.match, case.player, melee=True), bot.entity)
                    case.step(0, 65)
                    self.assertAlmostEqual(before - health.total, raw * scalar - 5, places=5)
                    if key == "5":
                        self.assertLess(case.player.mover.position[2], position[2])
                    else:
                        self.assertEqual(tuple(case.player.mover.position), position)
                    self.assertFalse(case.fight.rockets)
                    self.assertEqual(npc_control.stop(case.run.match), 1)
                    after = health.total
                    case.step(0, 30)
                    self.assertEqual(health.total, after)
                    self.assertEqual(bot.attack_mode, "primary")
                    if key == "1":
                        target = npc_control.start(case.run.match, case.player, melee=True)
                        self.assertEqual(target, bot.entity)
                        case.step(0, 45)
                        self.assertAlmostEqual(after - health.total, raw * scalar - 5, places=5)

    def test_npc_melee_wall_range_team_and_stop_prevent_contact(self):
        for key in ("1", "5"):
            for reason in ("wall", "range", "team", "stop"):
                with self.subTest(npc=key, reason=reason):
                    self.fixture(key, distance=8 if reason == "range" else 2)
                    case, bot = self.case, self.bot
                    health = case.health(case.player)
                    before = health.total
                    position = tuple(case.player.mover.position)
                    self.assertEqual(npc_control.start(case.run.match, case.player, melee=True), bot.entity)
                    if reason == "wall":
                        case.wall(1)
                    elif reason == "team":
                        case.run.match.game_map = replace(
                            case.run.match.game_map, free_for_all=False, team_sizes=(6, 6))
                        bot.team = case.player.team
                    elif reason == "stop":
                        case.step()
                        npc_control.stop(case.run.match)
                    case.step(0, 65)
                    self.assertEqual(health.total, before)
                    self.assertEqual(tuple(case.player.mover.position), position)
                    self.assertFalse(case.fight.rockets)

    def test_projected_barrier_rejects_assassin_pin_and_expires_back_to_normal_contact(self):
        for mode in ("recon", "sentry", "tank"):
            with self.subTest(mode=mode):
                self.fixture("4", mode, distance=2)
                case, bot = self.case, self.bot
                zarya = case.enemy
                case.run.match.game_map = replace(
                    case.run.match.game_map, free_for_all=False, team_sizes=(6, 6))
                self.assertTrue(case.run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
                case.player.team = zarya.team = 0
                case.bystander.team = bot.team = 1
                zarya.mover.position[:] = (4, 1, 0)
                zarya.mover.body = None
                case.step(0, 12)
                case.run.step({zarya: (-16384, 0, 0), case.player: (*case.command_aim, 0)})
                case.run.step({zarya: (-16384, 0, 16), case.player: (*case.command_aim, 0)})
                instances = zarya.body_script.component.instances.values()
                projected = next(i for i in instances if i.graph.index == 0x025C).state(10)
                self.assertTrue(projected.active)
                health = case.health(case.player)
                before = health.total
                self.assertEqual(npc_control.start(case.run.match, case.player), bot.entity)
                case.step()
                shield = case.fight.zarya_barriers.projected[zarya.body]
                self.assertEqual(shield.remaining, 175)
                self.assertEqual(health.total, before)
                self.assertFalse(case.fight.is_stunned(case.player))
                self.assertFalse(case.fight.pins)
                npc_control.stop(case.run.match)
                for _ in range(140):
                    case.step()
                    if not projected.active:
                        break
                self.assertFalse(projected.active)
                self.assertEqual(npc_control.start(case.run.match, case.player), bot.entity)
                for _ in range(45):
                    case.step()
                    if case.fight.is_stunned(case.player):
                        break
                self.assertEqual(case.fight.pins, {bot.entity: case.player.body})
                self.assertLess(health.total, before)
                npc_control.stop(case.run.match)
                self.assertFalse(case.fight.is_stunned(case.player))

    def test_actual_assassin_pin_releases_on_wall_range_team_and_death(self):
        for reason in ("wall", "range", "team", "death"):
            with self.subTest(reason=reason):
                self.fixture("4", distance=2)
                case, bot = self.case, self.bot
                npc_control.start(case.run.match, case.player)
                case.step()
                self.assertTrue(case.fight.is_stunned(case.player))
                if reason == "wall":
                    case.wall(1)
                elif reason == "range":
                    bot.position = (0, 1, 4)
                elif reason == "team":
                    case.run.match.game_map = replace(case.run.match.game_map,
                        free_for_all=False, team_sizes=(6, 6))
                    bot.team = case.player.team
                else:
                    case.fight._damage(case.enemy, case.target(bot), 10000, False, None, case.run.frame)
                    self.assertTrue(bot.dead)
                    self.assertIn(bot, case.run.match.bots)
                before = case.health(case.player).total
                case.step(0, 90)
                self.assertEqual(case.health(case.player).total, before)
                self.assertFalse(case.fight.pins)
                self.assertFalse(case.fight.is_stunned(case.player))

    def test_manual_engagement_obeys_walls_stop_range_and_friendly_rules(self):
        for key in ("1", "2", "3", "5"):
            for blocked in ("wall", "stop", "range", "friendly"):
                with self.subTest(npc=self.catalog[key].name, blocked=blocked):
                    self.fixture(key)
                    self.assertEqual(npc_control.start(self.case.run.match, self.case.player),
                                     self.bot.entity)
                    before = self.case.health(self.case.player).total
                    original_world = self.case.run.match.collision_world
                    if blocked == "wall":
                        self.case.wall(4)
                    elif blocked == "stop":
                        self.assertEqual(npc_control.stop(self.case.run.match), 1)
                    elif blocked == "range":
                        self.bot.position = (0, 1, 110)
                    else:
                        self.case.run.match.game_map = replace(self.case.run.match.game_map,
                            free_for_all=False, team_sizes=(6, 6))
                        self.bot.team = self.case.player.team
                    self.case.step(0, 90)
                    self.assertEqual(self.case.health(self.case.player).total, before)
                    self.assertFalse(self.bot.attack_input)
                    self.assertNotIn(20, self.bot.body_script.component.held)
                    self.case.run.match.collision_world = original_world
                    self.bot.position = (0, 1, 8)
                    self.bot.team = 1
                    if blocked == "stop":
                        npc_control.start(self.case.run.match, self.case.player)
                    self.wait_for_incoming_damage()

    def test_all_five_actual_talon_attacks_kill_bastion_and_release_control(self):
        for key in self.catalog:
            with self.subTest(npc=self.catalog[key].name):
                self.fixture(key, distance=2 if key == "4" else 8)
                case, bot = self.case, self.bot
                body = case.player.body
                health = case.health(case.player)
                health.damage(health.total - 1)
                case.fight._sync_body_health(case.player, health)
                self.assertFalse(health.dead)
                self.assertEqual(npc_control.start(case.run.match, case.player), bot.entity)
                for _ in range(300):
                    if health.dead:
                        break
                    case.step()
                self.assertTrue(health.dead)
                self.assertEqual(health.total, 0)
                self.assertEqual(health.killer, bot.entity)
                self.assertEqual(case.player.body, body)
                case.step(1, 90)
                self.assertEqual(case.player.body, body)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(bot.attack_input)
                self.assertNotIn(20, bot.body_script.component.held)
                self.assertFalse(case.fight.pins)

    def test_enforcer_actual_support_flashbang_interrupts_and_recovers_against_each_form(self):
        for mode in ('recon', 'sentry', 'tank'):
            with self.subTest(mode=mode):
                self.fixture('2', mode)
                case, bot = self.case, self.bot
                support = case.enemy
                self.assertTrue(case.run.match.switch_hero(support, heroes()[0x02E0000000000042]))
                support.mover.position[:] = (2, 1, 8)
                if support.mover.body is not None:
                    support.mover.body.teleport((2, 1 + support.mover._lift(), 8))
                case.step(0, 10)
                npc_control.start(case.run.match, case.player)
                self.wait_for_incoming_damage()
                case.step(0, 80)  # the next unstunned hit would land during this control window
                flashbang_tests.FlashbangTests().cast(case.run, support, bot)
                self.assertTrue(case.fight.is_stunned(bot))
                before = case.health(case.player).total
                end = case.fight.stuns[bot.entity]
                while case.run.frame < end - 1:
                    case.step()
                    self.assertEqual(case.health(case.player).total, before)
                    self.assertNotIn(20, bot.body_script.component.held)
                case.step()
                self.assertFalse(case.fight.is_stunned(bot))
                self.wait_for_incoming_damage()

    def test_enforcer_stops_after_actual_bastion_weapon_kill_and_retains_corpse(self):
        for mode in ('recon', 'sentry', 'tank'):
            with self.subTest(mode=mode):
                pools = self.fixture('2', mode)
                case, bot = self.case, self.bot
                npc_control.start(case.run.match, case.player)
                self.wait_for_incoming_damage()
                for _ in range(500):
                    case.step(1)
                    if pools.dead:
                        break
                self.assertTrue(pools.dead)
                self.assertTrue(bot.dead)
                self.assertIn(bot, case.run.match.bots)
                own = case.health(case.player).total
                case.step(0, 100)
                self.assertEqual(case.health(case.player).total, own)
                self.assertIn(bot, case.run.match.bots)
                self.assertEqual(npc.remove_spawned(case.run.match), (bot.entity,))
                self.assertNotIn(bot, case.run.match.bots)

    def test_all_four_attacks_miss_and_obey_range_and_walls_on_all_five_npcs(self):
        for key in self.catalog:
            for mode, buttons in (("recon", 1), ("sentry", 1), ("melee", 0x800), ("tank", 1)):
                for reason in ("aim", "range", "wall"):
                    with self.subTest(npc=self.catalog[key].name, mode=mode, reason=reason):
                        distance = 1.4 if mode == "melee" else 8
                        if reason == "range":
                            distance = 5 if mode == "melee" else 110
                        vitals = self.fixture(key, mode, distance)
                        initial = dict(vitals.now)
                        if reason == "wall":
                            self.case.wall(0.5 if mode == "melee" else 4)
                        yaw, pitch = self.case.command_aim
                        yaw = 16384 if reason == "aim" else yaw
                        self.case.run.step({self.case.player: (yaw, pitch, buttons)})
                        self.case.step(0, 150 if mode == "tank" else 30)
                        self.assertEqual(vitals.now, initial)


if __name__ == "__main__":
    unittest.main()
