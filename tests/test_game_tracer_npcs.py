"""Opt-in real NPC definitions/scripts and authored capsules; transport is an adapter.

Set ORPHEUS_PVE_NPC_DATA_DIR and ORPHEUS_NPC_XML_DIR to external read-only inputs.
These fixtures do not claim enemy navigation, special attacks or native transport.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_tracer as tracer_tests
import test_game_tracer_orisa_barrier as orisa_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import FakeClient, aim
from test_game_mover import box, wall

from ow174.game import (
    combat,
    deflection,
    dva,
    lucio_aura,
    lucio_barrier,
    lucio_soundwave,
    mercy,
    mercy_effects,
    mover,
    npc,
    npc_control,
    orisa_barrier,
    sigma_barrier,
    sigma_grasp,
    stats,
    tracer,
    tracer_pistol,
    zarya_barrier,
    zenyatta_orbs,
)
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr, graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from tools.extract_game_movers import mover_of


class NpcClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def bot_stream(self, entity):
        return None  # authoritative combat fixture omits NPC projectile observer transport

    def queue_bot_death(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.deaths.append((entity, snapshot))


class TracerNpcTests(unittest.TestCase):
    FIRST_PRIMARY_IMPACTS = (
        ("Trooper", 29, 20), ("Enforcer", 67, 2), ("Sniper", 128, 100),
        ("Assassin", 10, 25), ("Heavy Assault", 32, 10),
    )

    @classmethod
    def setUpClass(cls):
        data = os.environ.get("ORPHEUS_PVE_NPC_DATA_DIR")
        xml = os.environ.get("ORPHEUS_NPC_XML_DIR")
        if not data or not xml:
            raise unittest.SkipTest("external NPC catalog/graph bundle and body XML required")
        cls.definitions = npc.load_catalog(Path(data) / "pve_npcs_174.json")
        if {d.name for d in cls.definitions.values()} != {
                "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise ValueError("acceptance requires all five actual NPC classes")
        cls.original_graph_path = graph.GRAPHS_PATH
        cls.addClassCleanup(cls.restore_graphs)
        graph.GRAPHS_PATH = Path(data) / "game_graphs_174.json.gz"
        cls.clear_graphs()
        values = {}
        for definition in cls.definitions.values():
            path = Path(xml) / f"{definition.body & 0xFFFFFFFFFFFF:012X}.003.xml"
            fields = mover_of(path.read_text(encoding="utf-8"))
            if fields is None:
                raise ValueError(f"missing authored NPC capsule: {definition.name}")
            values[definition.body] = mover.MoverData(**fields)
        cls.movers_patch = patch.dict(mover._movers(), values)
        cls.movers_patch.start()
        cls.addClassCleanup(cls.movers_patch.stop)

    @classmethod
    def clear_graphs(cls):
        for function in (graph.graph, graph._records, graph._data):
            function.cache_clear()

    @classmethod
    def restore_graphs(cls):
        graph.GRAPHS_PATH = cls.original_graph_path
        cls.clear_graphs()

    def fight(self, definition, *, allied=False, distance=5):
        run, player, enemy, _ = tracer_tests.TracerPistolTests().fight()
        enemy.mover.position[0] += 100
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.client = NpcClient(player)
        enemy.client = NpcClient(enemy)
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + distance),
                        team=player.team if allied else 1 - player.team)
        self.assertIsNotNone(bot.body_script)
        self.assertIsNone(bot.body_script.component.fault)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        self.assertEqual(target.height, mover.mover_data(definition.body).stand_height)
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + distance))
        return run, player, bot, target, yaw, pitch

    def hook_scene(self, definition, *, allied=False):
        run, player, bot, target, _, _ = self.fight(definition, allied=allied, distance=8)
        hog, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000040)
        hog.client = NpcClient(hog)
        hog.team = player.team
        x, y, z = player.mover.position
        hog.mover.position[:] = (x, y, z - 2)
        hog.mover.body = None
        run.step({})
        angles = aim(run.match.combat._eye(hog), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        return run, player, bot, target, hog, angles

    def test_actual_whole_hog_moves_all_five_npcs_and_tracer_hits_displaced_capsule(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, hog, angles = self.hook_scene(definition)
                fight = run.match.combat
                health = fight.vitals_of(target)
                before = health.total
                start = bot.position
                script = hog.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({hog: (*angles, 32)})
                for _ in range(85):
                    run.step({hog: (*angles, 0)})
                    if health.total < before:
                        break
                self.assertLess(health.total, before)
                forced = bot.impulse_mover
                self.assertIsNotNone(forced)
                self.assertEqual(forced.data, mover.mover_data(definition.body))
                self.assertAlmostEqual(math.hypot(forced.velocity[0], forced.velocity[2]), 10, places=4)
                self.assertAlmostEqual(forced.velocity[1], 1.8, places=4)
                for _ in range(5):
                    run.step({hog: (-32768, 0, 0)})
                self.assertGreater(bot.position[2], start[2])
                before = health.total
                shot = aim(fight._eye(player), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                run.step({hog: (-32768, 0, 0), player: (*shot, FIRE)})
                self.assertLess(health.total, before)

    def test_actual_recon_and_sentry_rounds_hit_all_five_npc_capsules(self):
        for definition in self.definitions.values():
            for sentry, damage in ((False, 20), (True, 15)):
                with self.subTest(npc=definition.name, sentry=sentry):
                    run, player, _, target, _, _ = self.fight(definition, distance=8)
                    fight = run.match.combat
                    bastion, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000015,
                    )
                    bastion.client = NpcClient(bastion)
                    bastion.team = player.team
                    x, y, z = player.mover.position
                    bastion.mover.position[:] = (x, y, z - 2)
                    bastion.mover.body = None
                    run.step({})
                    x, y, z = target.feet
                    angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                    if sentry:
                        run.step({bastion: (*angles, 8)})
                        for _ in range(70):
                            run.step({bastion: (*angles, 0)})
                        self.assertEqual(bastion.body_script.component.entity_var(1216).value(), 2)
                    health = fight.vitals_of(target)
                    before = health.total
                    armor = health.now[combat.ARMOUR_POOL]
                    run.step({bastion: (*angles, FIRE)})
                    self.assertEqual(before - health.total, damage - (5 if armor else 0))
                    notice, = bastion.client.messages(combat.HIT)
                    self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], target.entity)

    def test_actual_tank_shell_and_tracer_pistols_hit_all_five_npc_capsules(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition, distance=8)
                fight = run.match.combat
                bastion, _ = support_tests.PulseDeflectTests().support(
                    run, player, hero=0x02E0000000000015,
                )
                bastion.client = NpcClient(bastion)
                bastion.team = player.team
                x, y, z = player.mover.position
                bastion.mover.position[:] = (x, y, z - 2)
                bastion.mover.body = None
                health = fight.vitals_of(target)
                before = health.total
                run.step({player: (yaw, pitch, FIRE)})
                self.assertLess(health.total, before)
                self.assertFalse(health.dead)
                x, y, z = target.feet
                angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                script = bastion.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({bastion: (*angles, 32)})
                for _ in range(100):
                    run.step({bastion: (*angles, 0)})
                self.assertEqual(script.component.entity_var(1216).value(), 6)
                wounded = health.total
                run.step({bastion: (*angles, FIRE)})
                shell, = fight.rockets
                self.assertEqual(shell.definition, combat.BASTION_TANK_SHELL)
                for _ in range(40):
                    run.step({bastion: (*angles, 0)})
                    if shell not in fight.rockets:
                        break
                self.assertNotIn(shell, fight.rockets)
                self.assertGreater(wounded - health.total, 0)
                self.assertLessEqual(wounded - health.total, 205)
                if health.dead:
                    self.assertEqual(health.killer, bastion.body)
                    self.assertIsNone(bot.impulse_mover)
                else:
                    self.assertIsNotNone(bot.impulse_mover)
                    self.assertAlmostEqual(bot.impulse_mover.velocity[2], 5, places=4)
                    before_push = bot.position[2]
                    for _ in range(12):
                        run.step({})
                    self.assertGreater(bot.position[2], before_push)

    def test_actual_tank_body_replacement_retires_flight_before_each_npc(self):
        for definition in self.definitions.values():
            for leased in (True, False):
                with self.subTest(npc=definition.name, leased=leased):
                    run, player, _, target, _, _ = self.fight(definition, distance=12)
                    fight = run.match.combat
                    bastion, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000015,
                    )
                    bastion.client = NpcClient(bastion)
                    bastion.team = player.team
                    x, y, z = player.mover.position
                    bastion.mover.position[:] = (x, y, z - 2)
                    bastion.mover.body = None
                    x, y, z = target.feet
                    angles = aim(fight._eye(bastion), (x, y + 0.85, z))
                    script = bastion.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({bastion: (*angles, 32)})
                    for _ in range(100):
                        run.step({})
                    self.assertEqual(script.component.entity_var(1216).value(), 6)
                    health = fight.vitals_of(target)
                    before = health.total
                    if leased:
                        run.step({bastion: (*angles, FIRE)})
                    else:
                        with patch.object(fight.projectiles, "lease_id", return_value=None):
                            run.step({bastion: (*angles, FIRE)})
                    shell, = fight.rockets
                    self.assertEqual(shell.definition, combat.BASTION_TANK_SHELL)
                    self.assertEqual(shell.shot is not None, leased)
                    old_body = bastion.body
                    self.assertTrue(run.match.switch_hero(bastion, heroes()[0x02E000000000006E]))
                    self.assertNotEqual(bastion.body, old_body)
                    self.assertNotIn(shell, fight.rockets)
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(health.total, before)
                    self.assertFalse(fight.rockets)

    def test_hook_pulls_all_five_actual_npcs_and_they_resume_attacking_tracer(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, hog, angles = self.hook_scene(definition)
                fight = run.match.combat
                health = fight.vitals_of(target)
                start = bot.position
                before = health.total
                run.step({hog: (*angles, 8)})
                for _ in range(45):
                    run.step({hog: (*angles, 0)})
                    if hog.body in fight.roadhog_hooks.flights:
                        break
                self.assertIn(hog.body, fight.roadhog_hooks.flights)
                self.start_npc_at_tracer(run, player, bot)
                run.step({hog: (*angles, 0)})
                self.assertTrue(bot.firing)
                if definition.name != "Assassin":
                    self.assertTrue(bot.attack_input)
                for _ in range(50):
                    run.step({hog: (*angles, 0)})
                    if fight.roadhog_hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.roadhog_hooks.controlled(bot.entity))
                damage = 30 - (combat.ARMOUR_BLOCK if definition.armour else 0)
                self.assertEqual(health.total, before - damage)
                self.assertLess(bot.position[2], start[2])
                self.assertFalse(bot.attack_input)
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                self.assertEqual(own.total, 150)
                shot = aim(fight._eye(player), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                run.step({player: (*shot, FIRE)})
                self.assertLess(health.total, before - damage)
                for _ in range(90):
                    if not fight.roadhog_hooks.controlled(bot.entity):
                        break
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(own.total, 150)
                    run.step({})
                self.assertFalse(fight.roadhog_hooks.controlled(bot.entity))
                self.assertAlmostEqual(math.dist(bot.position, hog.mover.position), 3, places=2)
                for _ in range(200):
                    if own.total < 150:
                        break
                    run.step({})
                self.assertLess(own.total, 150)
                self.assertIsNone(bot.body_script.component.fault)
                self.assertEqual(npc_control.stop(run.match), 1)

    def test_hook_cleans_up_all_five_npcs_on_team_change_kill_or_removal(self):
        for definition in self.definitions.values():
            for cause in ("allied", "team change", "killed", "removed"):
                with self.subTest(npc=definition.name, cause=cause):
                    run, player, bot, target, hog, angles = self.hook_scene(
                        definition, allied=cause == "allied",
                    )
                    fight = run.match.combat
                    health = fight.vitals_of(target)
                    if cause == "killed":
                        health.damage(health.total - 36, damage_over_time=True)
                    before = health.total
                    start = bot.position
                    run.step({hog: (*angles, 8)})
                    for _ in range(70):
                        run.step({hog: (*angles, 0)})
                        if fight.roadhog_hooks.controlled(bot.entity):
                            break
                    if cause == "allied":
                        self.assertEqual(health.total, before)
                        self.assertEqual(bot.position, start)
                    else:
                        self.assertTrue(fight.roadhog_hooks.controlled(bot.entity))
                        if cause == "team change":
                            bot.team = player.team
                            run.step({})
                        elif cause == "killed":
                            point = (bot.position[0], bot.position[1] + 1, bot.position[2])
                            shot = aim(fight._eye(player), point)
                            run.step({player: (*shot, FIRE)})
                            self.assertTrue(health.dead)
                            self.assertEqual(health.killer, player.body)
                            self.assertTrue(bot.retain_corpse)
                        else:
                            self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                            self.assertNotIn(bot, run.match.bots)
                    self.assertFalse(fight.roadhog_hooks.flights)
                    self.assertFalse(fight.roadhog_hooks.controlled(bot.entity))
                    self.assertEqual(bot.velocity, (0, 0, 0))

    def test_actual_pulse_attaches_to_hooked_trooper_and_follows_its_pull(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, player, bot, target, hog, angles = self.hook_scene(definition)
        fight = run.match.combat
        health = fight.vitals_of(target)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (*angles, 8)})
        for _ in range(60):
            run.step({hog: (*angles, 0)})
            if hog.body in fight.roadhog_hooks.flights:
                break
        self.assertIn(hog.body, fight.roadhog_hooks.flights)
        self.assertFalse(fight.roadhog_hooks.controlled(bot.entity))
        run.step({player: (0, 0, 32)})
        for _ in range(50):
            run.step({})
            if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == bot.entity:
                break
        bomb, = fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, bot.entity)
        self.assertTrue(fight.roadhog_hooks.controlled(bot.entity))
        attached_z = bomb.position[2]
        run.step({player: (16384, 0, 8)})
        for _ in range(25):
            run.step({})
            if not fight.roadhog_hooks.controlled(bot.entity):
                break
        self.assertLess(bomb.position[2], attached_z)
        self.assertEqual(bomb.attached, bot.entity)
        self.assertFalse(fight.roadhog_hooks.controlled(bot.entity))
        for _ in range(75):
            if not fight.pulse_bombs.bombs:
                break
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, player.body)
        self.assertFalse(fight.pulse_bombs.bombs)

    def valkyrie_npc_scene(self, definition, *, allied):
        if graph.effect_script(mercy_effects.CHAIN_HEAL) is None:
            self.skipTest("requires extracted Mercy effect bindings")
        run, player, bot, target, _, _ = self.fight(definition, allied=allied)
        caster, _ = support_tests.PulseDeflectTests().support(run, player, hero=mercy.HERO)
        caster.client = NpcClient(caster)
        caster.team = bot.team
        x, y, z = bot.position
        caster.mover.position[:] = (x - 4, y, z)
        caster.mover.body = None
        if not allied:
            anchor = run.players[1]
            anchor.team = bot.team
            anchor.mover.position[:] = (x, y, z + 3)
            anchor.mover.body = None
        run.step({})
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({caster: (0, 0, 32)})
        for _ in range(45):
            run.step({})
        return run, player, bot, target, caster

    def lucio_npc_scene(self, definition, *, allied, distance=5):
        run, player, bot, target, yaw, pitch = self.fight(definition, allied=allied, distance=distance)
        caster, _ = support_tests.PulseDeflectTests().support(run, player, hero=lucio_soundwave.HERO)
        caster.client = NpcClient(caster)
        caster.team = bot.team
        x, y, z = bot.position
        caster.mover.position[:] = (x - 4, y, z)
        caster.mover.body = None
        for _ in range(25):
            run.step({})
        return run, player, bot, run.match.combat.vitals_of(target), caster, yaw, pitch

    def zenyatta_npc_scene(self, definition, *, allied):
        run, player, bot, target, yaw, pitch = self.fight(definition, allied=allied)
        zen, _ = support_tests.PulseDeflectTests().support(run, player, hero=zenyatta_orbs.HERO)
        zen.client = NpcClient(zen)
        zen.team = player.team
        x, y, z = bot.position
        zen.mover.position[:] = (x - 4, y, z)
        zen.mover.body = None
        run.step({})
        zen.body_script.ult_seconds = 0
        orb_aim = aim(run.match.combat._eye(zen), (x, y + target.eye, z))
        return run, player, bot, run.match.combat.vitals_of(target), zen, (yaw, pitch), orb_aim

    def test_harmony_heals_pistol_damage_on_all_five_actual_npcs_and_credits_only_effective_healing(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health, zen, shot, orb = self.zenyatta_npc_scene(definition, allied=False)
                fight = run.match.combat
                key = zen.body, zenyatta_orbs.HARMONY
                meter = zen.body_script.component.entity_var(ULT_CHARGE)
                zen.body_script.set_ult_charge(0)
                run.step({player: (*shot, FIRE), zen: (*orb, 16)})
                self.assertEqual(health.total, health.maximum - 12)
                self.assertNotIn(key, fight.zenyatta_orbs.links)
                bot.team = zen.team
                run.step({zen: (*orb, 0)})
                run.step({zen: (*orb, 16)})
                self.assertEqual(fight.zenyatta_orbs.links[key].target, bot.entity)
                before = health.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(health.total - before, 4.8, places=4)
                for _ in range(25):
                    run.step({})
                self.assertEqual(health.total, health.maximum)
                self.assertAlmostEqual(meter.value(), 12, places=3)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertNotIn(key, fight.zenyatta_orbs.links)

    def test_ana_grenade_blocks_or_boosts_harmony_on_all_five_actual_npcs(self):
        for definition in self.definitions.values():
            for boost in (False, True):
                with self.subTest(npc=definition.name, boost=boost):
                    self.check_npc_grenade_harmony(definition, boost)

    def check_npc_grenade_harmony(self, definition, boost):
        run, player, bot, health, zen, shot, orb = self.zenyatta_npc_scene(definition, allied=False)
        fight = run.match.combat
        zen.team = bot.team
        script = player.body_script
        script.ult_seconds = 0
        before = health.total
        for _ in range(65 if boost else 1):
            run.step({player: (*shot, FIRE)})
            if before - health.total >= 130:
                break
        pistol_damage = before - health.total
        self.assertFalse(health.dead)
        self.assertGreaterEqual(pistol_damage, 130 if boost else 12)
        charge = script.component.entity_var(ULT_CHARGE)
        self.assertAlmostEqual(charge.value(), pistol_damage)

        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = bot.team if boost else player.team
        x, y, z = bot.position
        ana.mover.position[:] = (x + 4, y, z)
        ana.mover.body = None
        facing = aim(fight._eye(ana), (x, y + 1, z))
        before_grenade = health.total
        run.step({ana: (*facing, 16)})
        for _ in range(70):
            if health.healing_block_until or health.healing_boost_until:
                break
            run.step({ana: (*facing, 0)})
        deadline = health.healing_boost_until if boost else health.healing_block_until
        self.assertEqual(deadline, fight.tick + 250)
        self.assertEqual(health.healing_block_until if boost else health.healing_boost_until, 0)
        self.assertAlmostEqual(health.total - before_grenade, 100 if boost else -50)
        self.assertEqual(ana.stats.value(stats.HEALING), 100 if boost else 0)
        self.assertAlmostEqual(charge.value(), pistol_damage)

        run.step({zen: (*orb, 16)})
        key = zen.body, zenyatta_orbs.HARMONY
        self.assertEqual(fight.zenyatta_orbs.links[key].target, bot.entity)
        if not boost:
            blocked_health = health.total
            while run.frame + 1 < deadline:
                run.step({})
                self.assertEqual(health.total, blocked_health)
                self.assertEqual(zen.stats.value(stats.HEALING), 0)
            self.assertEqual(fight.zenyatta_orbs.links[key].target, bot.entity)
        before_heal = health.total
        healing = zen.stats.value(stats.HEALING)
        for _ in range(10):
            run.step({})
        expected = 7.2 if boost else 4.8
        self.assertAlmostEqual(health.total - before_heal, expected, places=4)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING) - healing, expected, places=4)
        self.assertAlmostEqual(zen.body_script.component.entity_var(ULT_CHARGE).value(), expected, places=4)
        self.assertAlmostEqual(charge.value(), pistol_damage)
        if boost:
            while run.frame + 2 < deadline:
                run.step({})
            self.assertEqual(health.total, health.maximum)
            run.step({player: (*shot, FIRE)})
            self.assertEqual(fight.tick, deadline - 1)
            self.assertEqual(health.total, health.maximum - 12)
            healing = zen.stats.value(stats.HEALING)
            before_heal = health.total
            run.step({})
            self.assertEqual(fight.tick, deadline)
            self.assertAlmostEqual(health.total - before_heal, 0.48, places=4)
            self.assertAlmostEqual(zen.stats.value(stats.HEALING) - healing, 0.48, places=4)
            self.assertAlmostEqual(charge.value(), pistol_damage + 12)

    def test_discord_amplifies_incoming_but_not_outgoing_attacks_on_all_five_actual_npcs(self):
        normal = {"Trooper": 20, "Enforcer": 2, "Sniper": 100, "Assassin": 25, "Heavy Assault": 10}
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health, zen, shot, orb = self.zenyatta_npc_scene(definition, allied=False)
                fight = run.match.combat
                key = zen.body, zenyatta_orbs.DISCORD
                run.step({zen: (*orb, 8)})
                self.assertEqual(fight.zenyatta_orbs.links[key].target, bot.entity)
                run.step({player: (*shot, FIRE)})
                self.assertEqual(health.total, health.maximum - 15)
                bot.team = zen.team
                run.step({zen: (*orb, 0)})
                self.assertNotIn(key, fight.zenyatta_orbs.links)
                for _ in range(48):
                    run.step({})
                run.step({zen: (*orb, 8)})
                self.assertNotIn(key, fight.zenyatta_orbs.links)
                bot.team = 1 - zen.team
                run.step({player: (*shot, FIRE), zen: (*orb, 0)})
                self.assertEqual(health.total, health.maximum - 27)
                run.step({zen: (*orb, 8)})
                self.assertEqual(fight.zenyatta_orbs.links[key].target, bot.entity)
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                self.start_npc_at_tracer(run, player, bot)
                for _ in range(200):
                    run.step({})
                    if own.total < own.maximum:
                        break
                self.assertEqual(own.maximum - own.total, normal[definition.name])
                self.assertIn(key, fight.zenyatta_orbs.links)
                self.assertIsNone(bot.body_script.component.fault)
                self.assertEqual(npc_control.stop(run.match), 1)

    def test_discord_amplifies_actual_pulse_attachment_and_blast_on_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health, zen, shot, orb = self.zenyatta_npc_scene(definition, allied=False)
                fight = run.match.combat
                key = zen.body, zenyatta_orbs.DISCORD
                run.step({zen: (*orb, 8)})
                self.assertEqual(fight.zenyatta_orbs.links[key].target, bot.entity)
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({player: (shot[0], 0, 32)})
                for _ in range(55):
                    run.step({})
                    if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].stuck:
                        break
                bomb, = fight.pulse_bombs.bombs
                self.assertEqual(bomb.attached, bot.entity)
                self.assertAlmostEqual(health.total, health.maximum - 6.25)
                for _ in range(75):
                    run.step({})
                    if not fight.pulse_bombs.bombs:
                        break
                self.assertFalse(fight.pulse_bombs.bombs)
                self.assertAlmostEqual(health.total, max(0, health.maximum - 443.75))
                if health.dead:
                    self.assertEqual(health.killer, player.body)
                    self.assertNotIn(key, fight.zenyatta_orbs.links)

    def test_transcendence_heals_all_five_actual_npcs_only_while_allied_and_active(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health, zen, shot, _ = self.zenyatta_npc_scene(definition, allied=False)
                fight = run.match.combat
                script = zen.body_script
                meter = script.component.entity_var(ULT_CHARGE)
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({player: (*shot, FIRE), zen: (0, 0, 32)})
                self.assertEqual(meter.value(), 0)
                effect = fight.zenyatta_orbs.transcendence[zen.body]
                hostile = health.maximum - 12
                run.step({})
                self.assertEqual(health.total, hostile)
                bot.team = zen.team
                run.step({})
                self.assertAlmostEqual(health.total, hostile + 4.8, places=4)
                bot.team = 1 - zen.team
                while fight.tick < effect.last - 1:
                    run.step({})
                self.assertAlmostEqual(health.total, hostile + 4.8, places=4)
                bot.team = zen.team
                run.step({})
                self.assertAlmostEqual(health.total, hostile + 9.6, places=4)
                run.step({})
                self.assertAlmostEqual(health.total, hostile + 9.6, places=4)
                self.assertNotIn(zen.body, fight.zenyatta_orbs.transcendence)
                self.assertEqual(meter.value(), 0)

    def test_crossfade_and_amp_heal_all_five_actual_npcs_and_retire(self):
        if lucio_aura.model() is None:
            self.skipTest("requires extracted Crossfade status and speed controller")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals, caster, _, _ = self.lucio_npc_scene(definition, allied=True)
                fight = run.match.combat
                health = fight.vitals[player.body]
                caster.body_script.ult_seconds = 0
                meter = caster.body_script.component.entity_var(ULT_CHARGE)
                vitals.damage(100)
                health.damage(100)
                run.step({caster: (0, 0, 8)})
                for _ in range(35):
                    run.step({})
                for button, expected in ((0, 6.4), (16, 20.8)):
                    if button:
                        run.step({caster: (0, 0, button)})
                        for _ in range(28):
                            run.step({})
                    before, tracer_before, charge_before = vitals.total, health.total, meter.value()
                    for _ in range(25):
                        run.step({})
                    self.assertAlmostEqual(vitals.total - before, expected, places=4)
                    self.assertAlmostEqual(health.total - tracer_before, expected, places=4)
                    self.assertAlmostEqual(meter.value() - charge_before, 2 * expected, places=3)
                bot.team = 1 - caster.team
                before = vitals.total
                for _ in range(25):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertFalse(fight.lucio_auras.receivers[bot.entity].sources)
                bot.team = caster.team
                run.step({})
                self.assertIn(caster.body, fight.lucio_auras.receivers[bot.entity].sources)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertNotIn(bot.entity, fight.lucio_auras.receivers)

    def test_sound_barrier_absorbs_tracer_pistols_and_pulse_on_all_five_actual_npcs(self):
        if lucio_barrier.model() is None:
            self.skipTest("requires extracted Sound Barrier status")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals, caster, yaw, pitch = self.lucio_npc_scene(definition, allied=False)
                fight = run.match.combat
                base = vitals.now.copy()
                script = caster.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({caster: (0, 0, 32)})
                for _ in range(41):
                    run.step({})
                shield = vitals.temporary_shields[lucio_barrier.EFFECT]
                self.assertEqual(shield.now, 750)
                self.assertNotIn(player.body, fight.lucio_barriers.leases)
                begin = run.frame
                run.step({player: (yaw, pitch, FIRE)})
                self.assertAlmostEqual(shield.now, 736)
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({player: (0, 0, 32)})
                for _ in range(55):
                    run.step({})
                    if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].stuck:
                        break
                bomb, = fight.pulse_bombs.bombs
                self.assertEqual(bomb.attached, bot.entity)
                self.assertAlmostEqual(shield.now, 750 - 17 - 2 * (run.frame - begin))
                for _ in range(75):
                    run.step({})
                    if not fight.pulse_bombs.bombs:
                        break
                self.assertFalse(fight.pulse_bombs.bombs)
                self.assertAlmostEqual(shield.now, 750 - 367 - 2 * (run.frame - begin))
                self.assertEqual(vitals.now, base)
                self.assertFalse(bot.dead)
                npc.remove_spawned(run.match)
                self.assertNotIn(bot.entity, fight.lucio_barriers.leases)

    def test_crossfade_and_amp_change_actual_assassin_approach_to_tracer(self):
        if lucio_aura.model() is None:
            self.skipTest("requires extracted Crossfade status and speed controller")
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, player, bot, _, caster, _, _ = self.lucio_npc_scene(definition, allied=False, distance=10)
        start = bot.position

        def approach():
            self.start_npc_at_tracer(run, player, bot)
            for _ in range(12):
                run.step({})
            before = bot.position
            for _ in range(4):
                run.step({})
            speed = -bot.velocity[2]
            distance = before[2] - bot.position[2]
            self.assertGreater(distance, 0)
            npc_control.stop(run.match)
            bot.position = start
            bot.navigation = None
            return speed, distance

        caster.team = player.team
        run.step({})
        speed, distance = approach()
        self.assertAlmostEqual(speed, bot.speed, delta=0.05)
        caster.team = bot.team
        run.step({})
        faster, ordinary_distance = approach()
        self.assertAlmostEqual(faster, speed * 1.25, delta=0.08)
        self.assertGreater(ordinary_distance, distance)
        run.step({caster: (0, 0, 16)})
        for _ in range(32):
            run.step({})
        faster, farther = approach()
        self.assertAlmostEqual(faster, speed * 1.6, delta=0.08)
        self.assertGreater(farther, ordinary_distance)
        for _ in range(190):
            run.step({})
        faster, _ = approach()
        self.assertAlmostEqual(faster, speed * 1.25, delta=0.08)
        self.start_npc_at_tracer(run, player, bot)
        for _ in range(200):
            run.step({})
            if bot.entity in run.match.combat.pins:
                break
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)

    def test_actual_soundwave_damages_and_pushes_all_five_npc_capsules(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals, caster, _, _ = self.lucio_npc_scene(definition, allied=False)
                caster.team = player.team
                start = bot.position
                caster.body_script.ult_seconds = 0
                meter = caster.body_script.component.entity_var(ULT_CHARGE)
                charge = meter.value()
                x, y, z = start
                caster.mover.position[:] = (x, y, z - 4)
                caster.mover.body = None
                run.step({})
                before = vitals.total
                expected = 25 - (combat.ARMOUR_BLOCK if definition.armour else 0)
                run.step({caster: (0, 0, SECONDARY)})
                self.assertEqual(vitals.total, before - expected)
                self.assertEqual(meter.value() - charge, expected)
                self.assertEqual(bot.position, start)
                forced = bot.impulse_mover
                self.assertIs(forced.data, mover.mover_data(definition.body))
                self.assertAlmostEqual(math.hypot(*forced.velocity), 17, delta=0.05)
                for _ in range(8):
                    run.step({caster: (0, 0, SECONDARY)})
                self.assertEqual(vitals.total, before - expected)
                self.assertEqual(meter.value() - charge, expected)
                self.assertGreater(bot.position[2], z + 1)
                self.assertGreater(bot.position[1], y)
                self.assertEqual(bot.movement(run.frame).position, bot.position)
                self.assertTrue(bot.movement(run.frame).flags & mover.IN_AIR)
                aim_at_bot = aim(run.match.combat._eye(player), (x, bot.position[1] + 1, bot.position[2]))
                run.step({player: (*aim_at_bot, FIRE)})
                self.assertLess(vitals.total, before - expected)
                npc.remove_spawned(run.match)
                self.assertIsNone(bot.impulse_mover)

    def test_actual_soundwave_does_not_push_allied_or_lethally_hit_npc(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        for allied in (True, False):
            with self.subTest(allied=allied):
                run, player, bot, vitals, caster, _, _ = self.lucio_npc_scene(definition, allied=allied)
                caster.team = player.team
                start = bot.position
                x, y, z = start
                caster.mover.position[:] = (x, y, z - 4)
                caster.mover.body = None
                run.step({})
                if not allied:
                    vitals.damage(vitals.total - 20)
                before = vitals.total
                run.step({caster: (0, 0, SECONDARY)})
                for _ in range(8):
                    run.step({})
                self.assertEqual(vitals.total, before if allied else 0)
                self.assertEqual(bot.dead, not allied)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(bot.position, start)
                self.assertIsNone(bot.impulse_mover)

    def test_ally_soundwave_pushes_assassin_out_of_pin_and_tracer_can_blink(self):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, player, bot, vitals, caster, _, _ = self.lucio_npc_scene(definition, allied=False, distance=2)
        fight = run.match.combat
        caster.team = player.team
        x, y, z = player.mover.position
        caster.mover.position[:] = (x, y, z - 2)
        caster.mover.body = None
        self.start_npc_at_tracer(run, player, bot)
        for _ in range(60):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        before = vitals.total
        run.step({caster: (0, 0, SECONDARY)})
        self.assertEqual(vitals.total, before - 25)
        for _ in range(14):
            run.step({})
            if bot.entity not in fight.pins:
                break
        self.assertNotIn(bot.entity, fight.pins)
        self.assertTrue(bot.impulse_mover.airborne)
        run.step({player: (16384, 0, 8)})
        self.assertGreater(player.mover.position[0], x + 3)

    def test_actual_soundwave_capsule_stops_at_wall_then_assassin_resumes_approach(self):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, player, bot, _, caster, _, _ = self.lucio_npc_scene(definition, allied=False)
        fight = run.match.combat
        caster.team = player.team
        x, y, z = bot.position
        floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
        behind = z + 1
        run.match.collision_world = World(floor + wall(x - 5, behind, x + 5, behind, y, y + 6))
        for _ in range(3):
            run.step({})
        self.start_npc_at_tracer(run, player, bot)
        caster.mover.position[:] = (x, y, z - 4)
        caster.mover.body = None
        run.step({caster: (0, 0, SECONDARY)})
        self.assertIsNotNone(bot.impulse_mover)
        for _ in range(10):
            run.step({})
        self.assertGreater(bot.position[2], z + 0.2)
        self.assertLessEqual(bot.position[2], behind - mover.mover_data(bot.body).stand_radius + 0.02)
        self.assertTrue(bot.impulse_mover.airborne)
        self.assertNotIn(bot.entity, fight.pins)
        for _ in range(200):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertIsNone(bot.impulse_mover)
        self.assertIsNotNone(bot.navigation)
        self.assertEqual(fight.pins.get(bot.entity), player.body)

    @staticmethod
    def staff_npc_step(run, caster, anchor, buttons):
        x, y, z = anchor.mover.position if hasattr(anchor, "mover") else anchor.position
        angles = aim(run.match.combat._eye(caster), (x, y + 1, z))
        run.step({caster: (*angles, buttons)})

    def test_each_friendly_npc_anchors_chain_heal_to_tracer_and_removal_retires_it(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, caster = self.valkyrie_npc_scene(definition, allied=True)
                fight = run.match.combat
                bot_health = fight.vitals_of(target)
                own = fight.vitals_of(mercy.body_target(fight, player.body))
                bot_health.damage(80)
                own.damage(60)
                for _ in range(25):
                    self.staff_npc_step(run, caster, bot, FIRE)
                self.assertAlmostEqual(own.total, 114, places=4)
                self.assertAlmostEqual(bot_health.total, bot_health.maximum - 56, places=4)
                attachments = list(fight.mercy_effects.attachments.values())
                child, = (item for item in attachments if item.definition == mercy_effects.CHAIN_HEAL)
                self.assertEqual((child.anchor, child.target), (bot.entity, player.body))
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertFalse(fight.mercy_effects.attachments)
                self.assertTrue(all(not item.component.instances for item in attachments))
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(own.total, 114, places=4)

    def test_tracer_anchors_chain_heal_to_all_five_npcs_and_hostile_switch_breaks_it(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, caster = self.valkyrie_npc_scene(definition, allied=True)
                fight = run.match.combat
                health = fight.vitals_of(target)
                health.damage(80)
                for _ in range(25):
                    self.staff_npc_step(run, caster, player, FIRE)
                self.assertAlmostEqual(health.total, health.maximum - 56, places=4)
                child, = (item for item in fight.mercy_effects.attachments.values()
                          if item.definition == mercy_effects.CHAIN_HEAL)
                self.assertEqual((child.anchor, child.target), (player.body, bot.entity))
                bot.team = 1 - player.team
                for _ in range(12):
                    self.staff_npc_step(run, caster, player, FIRE)
                self.assertAlmostEqual(health.total, health.maximum - 56, places=4)
                self.assertEqual({item.definition for item in fight.mercy_effects.attachments.values()},
                                 {mercy_effects.HEAL})

    def test_all_five_npc_attacks_hit_tracer_with_actual_valkyrie_chain_boost(self):
        expected = {"Trooper": 26, "Enforcer": 2.6, "Sniper": 130, "Assassin": 32.5, "Heavy Assault": 13}
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _, caster = self.valkyrie_npc_scene(definition, allied=False)
                fight = run.match.combat
                anchor = run.players[1]
                for _ in range(12):
                    self.staff_npc_step(run, caster, anchor, SECONDARY)
                child, = (item for item in fight.mercy_effects.attachments.values()
                          if item.definition == mercy_effects.CHAIN_BOOST)
                self.assertEqual((child.anchor, child.target), (anchor.body, bot.entity))
                own = fight.vitals_of(mercy.body_target(fight, player.body))
                self.start_npc_at_tracer(run, player, bot)
                for _ in range(200):
                    self.staff_npc_step(run, caster, anchor, SECONDARY)
                    if own.total < 150:
                        break
                self.assertAlmostEqual(150 - own.total, expected[definition.name], places=4)
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)
                self.assertEqual(npc_control.stop(run.match), 1)

    def start_npc_at_tracer(self, run, player, bot):
        x, y, z = bot.position
        player.mover.yaw, player.mover.pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)

    def test_launched_chain_boosted_round_keeps_damage_or_is_avoided_by_recall(self):
        for name, damage in (("Enforcer", 2.6), ("Sniper", 130)):
            definition = next(d for d in self.definitions.values() if d.name == name)
            for recall in (False, True):
                with self.subTest(npc=name, recall=recall):
                    run, player, bot, _, caster = self.valkyrie_npc_scene(definition, allied=False)
                    fight = run.match.combat
                    anchor = run.players[1]
                    own = fight.vitals_of(mercy.body_target(fight, player.body))
                    for _ in range(12):
                        self.staff_npc_step(run, caster, anchor, SECONDARY)
                    self.start_npc_at_tracer(run, player, bot)
                    first = None
                    for _ in range(150):
                        self.staff_npc_step(run, caster, anchor, SECONDARY)
                        first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                        if first is not None:
                            break
                    self.assertIsNotNone(first)
                    self.assertEqual(own.total, 150)
                    self.assertAlmostEqual(first.direct.outgoing_scalar, 1.3, places=4)
                    run.step({player: (0, 0, 16)} if recall else {})
                    self.assertFalse(fight.mercy_effects.attachments)
                    self.assertIn(first, fight.rockets)
                    if recall:
                        self.assertTrue(tracer.recalling(player))
                    for _ in range(24):
                        run.step({})
                        if first not in fight.rockets:
                            break
                    self.assertNotIn(first, fight.rockets)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    self.assertAlmostEqual(own.total, 150 if recall else 150 - damage, places=4)
                    for _ in range(85):
                        run.step({})
                    self.assertAlmostEqual(own.total, 150 if recall else 150 - damage, places=4)
                    self.assertFalse(tracer.recalling(player))

    def test_assassin_pin_damage_uses_chain_boost_per_tick_and_release_keeps_the_pin(self):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, player, bot, _, caster = self.valkyrie_npc_scene(definition, allied=False)
        fight = run.match.combat
        anchor = run.players[1]
        own = fight.vitals_of(mercy.body_target(fight, player.body))
        for _ in range(12):
            self.staff_npc_step(run, caster, anchor, SECONDARY)
        self.start_npc_at_tracer(run, player, bot)
        for _ in range(200):
            self.staff_npc_step(run, caster, anchor, SECONDARY)
            if own.total < 150:
                break
        self.assertAlmostEqual(own.total, 117.5, places=4)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        for _ in range(100):
            state = instance.states.get(instance.graph.node(15).state)
            if state is not None and state.active:
                break
            self.staff_npc_step(run, caster, anchor, SECONDARY)
        self.assertTrue(state is not None and state.active)
        before = own.total
        for _ in range(4):
            self.staff_npc_step(run, caster, anchor, SECONDARY)
        self.assertAlmostEqual(before - own.total, 8.32, places=4)
        run.step({})
        self.assertFalse(fight.mercy_effects.attachments)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        before = own.total
        for _ in range(4):
            run.step({})
        self.assertAlmostEqual(before - own.total, 6.4, places=4)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        self.assertNotIn(bot.entity, fight.pins)

    def test_actual_npc_damage_funds_pulse_but_miss_and_ally_do_not(self):
        for definition in self.definitions.values():
            self.check_npc_pulse_charge_gate(definition)

    def check_npc_pulse_charge_gate(self, definition):
        gates = ("hostile", "miss", "ally") if definition.name == "Trooper" else ("miss", "ally")
        for gate in gates:
            with self.subTest(gate=gate, npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition, allied=gate == "ally")
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost - 6)
                run.step({player: (16384 if gate == "miss" else yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, before - (12 if gate == "hostile" else 0))
                self.assertEqual(charge.value(), cost if gate == "hostile" else cost - 6)
                run.step({player: (yaw, 0, 32)})
                self.assertEqual(charge.value(), 0 if gate == "hostile" else cost - 6)
                for _ in range(45):
                    run.step({player: (yaw, 0, 0)})
                    if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == bot.entity:
                        break
                if gate != "hostile":
                    self.assertFalse(fight.pulse_bombs.bombs)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(player.client.messages(combat.HIT))
                    continue
                bomb, = fight.pulse_bombs.bombs
                self.assertEqual(bomb.attached, bot.entity)
                for _ in range(80):
                    run.step({})
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, player.body)
                self.assertFalse(fight.pulse_bombs.bombs)

    def test_actual_pistol_damage_funds_two_lethal_pulses_for_each_npc_class(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                cost = script.component.entity_var(ULT_COST).value()
                for _ in range(2):
                    self.assertEqual(charge.value(), 0)
                    run.step({player: (yaw, 0, 32)})
                    self.assertEqual(charge.value(), 0)
                    self.assertFalse(fight.pulse_bombs.bombs)
                    bot, health = self.earn_pulse_on_npc_class(
                        run, player, definition, bot, target, (yaw, pitch),
                    )
                    self.assertEqual(charge.value(), cost)
                    self.assertTrue(5 < health.total <= 350)
                    run.step({player: (yaw, 0, 32)})
                    self.assertEqual(charge.value(), 0)
                    for _ in range(55):
                        run.step({player: (yaw, 0, 0)})
                        if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].stuck:
                            break
                    bomb, = fight.pulse_bombs.bombs
                    self.assertEqual(bomb.attached, bot.entity)
                    self.assertFalse(health.dead)
                    for _ in range(75):
                        run.step({})
                        if not fight.pulse_bombs.bombs:
                            break
                    self.assertFalse(fight.pulse_bombs.bombs)
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, player.body)
                    self.assertEqual(charge.value(), 0)
                    target = next(t for t in fight._targets() if t.owner is bot)

    def earn_pulse_on_npc_class(self, run, player, definition, bot, target, angles):
        fight = run.match.combat
        component = player.body_script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        pistol = next(i for i in component.instances.values() if i.graph.index == 0x16)
        ammo = pistol.vars[53]
        position, team = tuple(bot.position), bot.team
        health = fight.vitals_of(target)
        earned = 0
        reloads = 0
        for _ in range(3000):
            if health.dead:
                bot = npc.spawn(run.match, definition, position, team=team)
                target = next(t for t in fight._targets() if t.owner is bot)
                health = fight.vitals_of(target)
            before, rounds = health.total, ammo.value()
            run.step({player: (*angles, FIRE if rounds else 0x400)})
            earned += before - health.total
            reloads += int(not rounds and ammo.value() > 0)
            self.assertAlmostEqual(charge.value(), min(earned, cost), places=4)
            if charge.value() == cost and 5 < health.total <= 350:
                break
        self.assertGreaterEqual(earned, cost)
        self.assertGreater(reloads, 0)
        return bot, health

    def test_actual_npc_damage_recharges_pulse_after_previous_bomb_ends(self):
        trooper = next(d for d in self.definitions.values() if d.name == "Trooper")
        heavy = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, player, first_target, _, _, _ = self.fight(trooper)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        for _ in range(120):
            run.step({})
        self.assertTrue(first_target.dead)
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertEqual(charge.value(), 0)

        second_target = npc.spawn(run.match, heavy, first_target.position, team=first_target.team)
        target = next(t for t in fight._targets() if t.owner is second_target)
        vitals = fight.vitals_of(target)
        before = vitals.total
        x, y, z = second_target.position
        aim_body = aim(fight._eye(player), (x, y + 1, z))
        pistol = next(i for i in script.component.instances.values() if i.graph.index == 0x0016)
        ammo = pistol.vars[53]
        reloads = 0
        for _ in range(1800):
            run.step({player: (*aim_body, FIRE)})
            if charge.value() == cost:
                break
            if ammo.value() == 0:
                run.step({player: (*aim_body, 0x400)})
                for _ in range(80):
                    if ammo.value() > 0:
                        break
                    run.step({})
                self.assertEqual(ammo.value(), 40)
                reloads += 1
        self.assertGreater(reloads, 0)
        self.assertEqual(charge.value(), cost)
        self.assertGreaterEqual(before - vitals.total, cost)
        self.assertFalse(vitals.dead)
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        for _ in range(120):
            run.step({})
            if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == second_target.entity:
                break
        bomb, = fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, second_target.entity)
        before_blast = vitals.total
        for _ in range(80):
            if not fight.pulse_bombs.bombs:
                break
            run.step({})
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertEqual(before_blast - vitals.total, 350)
        self.assertEqual(charge.value(), 0)

    def test_actual_pistols_gain_charge_during_pulse_flight_and_fuse(self):
        heavy = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        for stage in ("flight", "fuse"):
            with self.subTest(stage=stage):
                run, player, bot, target, yaw, pitch = self.fight(heavy)
                fight = run.match.combat
                bombs = fight.pulse_bombs.bombs
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                bomb_yaw = 16384 if stage == "flight" else 0
                run.step({player: (bomb_yaw, 0, 32)})
                for _ in range(60):
                    if bombs and (stage == "flight" or bombs[0].attached == bot.entity):
                        break
                    run.step({player: (bomb_yaw, 0, 0)})
                bomb, = bombs
                self.assertEqual(bomb.stuck, stage == "fuse")
                self.assertEqual(bomb.attached, bot.entity if stage == "fuse" else None)
                self.assertEqual(charge.value(), 0)
                vitals = fight.vitals_of(target)
                before = vitals.total
                for _ in range(35):
                    run.step({player: (yaw, pitch, FIRE)})
                    if vitals.total < before:
                        break
                during = before - vitals.total
                self.assertEqual(during, 12)
                self.assertIn(bomb, bombs)
                self.assertEqual(bomb.stuck, stage == "fuse")
                self.assertEqual(charge.value(), during)
                before_blast = vitals.total
                for _ in range(350):
                    if not bombs:
                        break
                    run.step({})
                self.assertFalse(bombs)
                self.assertEqual(before_blast - vitals.total, 350 if stage == "fuse" else 0)
                self.assertEqual(charge.value(), during)
                before = vitals.total
                run.step({player: (yaw, pitch, FIRE)})
                damage = before - vitals.total
                self.assertGreater(damage, 0)
                self.assertEqual(charge.value(), during + damage)

    def test_actual_npc_overkill_credits_only_remaining_health(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, player, bot, target, yaw, pitch = self.fight(definition)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 4)
        self.assertEqual(vitals.total, 4)
        script = player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 6)
        run.step({player: (yaw, pitch, FIRE)})
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, player.body)
        self.assertEqual(charge.value(), cost - 2)
        notices = player.client.messages(combat.HIT)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["+0x78"]["+0x4"], 4)
        for _ in range(20):
            run.step({})
        pistol = next(i for i in script.component.instances.values() if i.graph.index == 0x0016)
        ammo = pistol.vars[53]
        before = ammo.value()
        run.step({player: (yaw, pitch, FIRE)})
        self.assertLess(ammo.value(), before)
        self.assertEqual(player.client.messages(combat.HIT), notices)
        self.assertEqual(charge.value(), cost - 2)
        run.step({player: (yaw, 0, 32)})
        for _ in range(16):
            run.step({})
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertEqual(charge.value(), cost - 2)

    def test_primary_damages_authored_base_pools_of_all_five_classes(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                run.step({player: (yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, before - 12)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 12)

    def test_pistol_critical_height_fallback_against_all_five_actual_classes(self):
        for definition in self.definitions.values():
            for case in ("body", "high", "lethal"):
                with self.subTest(npc=definition.name, case=case):
                    self.check_npc_pistol_critical_fallback(definition, case)

    def check_npc_pistol_critical_fallback(self, definition, case):
        run, player, bot, target, _, _ = self.fight(definition)
        fight = run.match.combat
        player.body_script.ult_seconds = 0
        self.assertIsNone(target.head_offset)
        vitals = fight.vitals_of(target)
        if case == "lethal":
            vitals.now.update({combat.HEALTH_POOL: 17, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
        before = vitals.total
        x, y, z = bot.position
        height = 1.0 if case == "body" else 1.46
        angles = aim(fight._eye(player), (x, y + height, z))
        run.step({player: (*angles, FIRE)})
        expected = {"body": (6, 6), "high": (12, 12), "lethal": (12, 5)}[case]
        hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
        self.assertEqual([hit["+0x4"] for hit in hits], list(expected))
        flags = [0 if case == "body" else combat.CRITICAL] * 2
        if case == "lethal":
            flags[-1] |= combat.KILLING
        self.assertEqual([hit["+0x8"] for hit in hits], flags)
        damage = sum(expected)
        self.assertEqual(vitals.total, before - damage)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)
        self.assertEqual(bot.dead, case == "lethal")
        self.assertEqual(vitals.killer, player.body if case == "lethal" else None)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), int(case == "lethal"))

    def test_distant_pistol_hits_credit_only_falloff_damage_on_all_five_classes(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition, distance=25)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                run.step({player: (16384, 0, FIRE)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(player.client.messages(combat.HIT))

                run.step({player: (yaw, pitch, FIRE)})
                self.assertAlmostEqual(before - vitals.total, 3.6, places=5)
                self.assertAlmostEqual(charge.value(), before - vitals.total, places=5)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_actual_pistol_bloom_splits_pairs_on_all_five_distant_npc_capsules(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition, distance=25)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.ult_seconds = 0
                pistol = next(i for i in script.component.instances.values() if i.graph.index == 0x16)
                ammo = pistol.vars[53]
                counts = []
                for _ in range(61):
                    rounds = ammo.value()
                    hits = len(player.client.messages(combat.HIT))
                    run.step({player: (yaw, pitch, FIRE)})
                    landed = len(player.client.messages(combat.HIT)) - hits
                    if ammo.value() != rounds:
                        self.assertEqual(rounds - ammo.value(), 2)
                        counts.append(landed)
                    else:
                        self.assertEqual(landed, 0)
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(len(counts), 20)
                self.assertEqual(counts[0], 2)
                self.assertEqual(set(counts), {0, 1, 2})
                self.assertEqual(len(player.client.messages(combat.HIT)), sum(counts))
                damage = before - vitals.total
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), damage, places=4)
                self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), damage, places=4)

    def test_actual_wall_splits_full_bloom_pistol_pair_against_each_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_split_pistol_wall(definition)

    def check_split_pistol_wall(self, definition):
        run, player, _, target, yaw, pitch = self.fight(definition, distance=4)
        fight = run.match.combat
        pistol = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x16)
        volley = pistol.state(8)
        inputs = {player: (yaw, pitch, FIRE)}
        for _ in range(8):
            run.step(inputs)
        self.assertEqual(pistol.vars[53].value(), 34)
        rays = tracer_pistol.rays(
            volley, 3, volley.start + 150, (volley.counter, volley.start), combat.aim(yaw, pitch), 2,
        )
        origin = fight._eye(player)
        for ray in rays:
            self.assertIsNotNone(combat.ray_capsule(origin, ray, *target.segment(), target.radius))
        x, floor, z = player.mover.position
        cross = [x + ray[0] * 1.5 / ray[2] for ray in rays]
        self.assertGreater(abs(cross[0] - cross[1]), 1e-6)
        cut = sum(cross) / 2
        face = z + 1.5
        triangles = box(x - 15, x + 15, floor - 1, floor, z - 10, z + 12)
        triangles += wall(x - 5, face, cut, face, floor, floor + 4)
        run.match.collision_world = World(triangles)
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        vitals = fight.vitals_of(target)
        before = vitals.total
        hits = len(player.client.messages(combat.HIT))
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        before_charge = charge.value()
        for _ in range(3):
            run.step(inputs)
        self.assertEqual(pistol.vars[53].value(), 32)
        self.assertEqual(len(player.client.messages(combat.HIT)) - hits, 1)
        self.assertEqual(before - vitals.total, 6)
        self.assertEqual(charge.value() - before_charge, 6)

    def test_actual_reload_restores_damage_and_charge_against_all_five_classes(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                pistol = next(i for i in script.component.instances.values() if i.graph.index == 0x16)
                ammo = pistol.vars[53]
                for _ in range(110):
                    run.step({player: (16384, 0, FIRE)})
                    if ammo.value() == 0:
                        break
                self.assertEqual(ammo.value(), 0)
                run.step({})
                for _ in range(20):
                    run.step({player: (yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, before)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(player.client.messages(combat.HIT))

                run.step({player: (yaw, pitch, 0x400)})
                self.assertEqual(ammo.value(), 0)
                for _ in range(85):
                    run.step({})
                    if ammo.value():
                        break
                self.assertEqual(ammo.value(), 40)
                self.assertEqual(vitals.total, before)
                run.step({player: (yaw, pitch, FIRE)})
                self.assertEqual(ammo.value(), 40)
                self.assertEqual(vitals.total, before)
                for _ in range(40):
                    run.step({player: (yaw, pitch, FIRE)})
                    if ammo.value() < 40:
                        break
                self.assertEqual(ammo.value(), 38)
                self.assertEqual(vitals.total, before - 12)
                self.assertEqual(charge.value(), 12)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_owner_health_query_tracks_initial_pools_damage_and_death(self):
        query = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                component = bot.body_script.component
                instance = next(iter(component.instances.values()))
                vitals = run.match.combat.vitals_of(target)
                self.assertEqual(instance.evaluate(query), vitals.total)
                run.step({player: (yaw, pitch, FIRE)})
                self.assertEqual(instance.evaluate(query), vitals.total)
                self.assertEqual(
                    vitals.total, definition.health + definition.armour + definition.shields - 12)
                # Release, then finish this actual body through the normal attack path.
                for _ in range(4):
                    run.step({})
                vitals.now[combat.HEALTH_POOL] = 11
                vitals.now[combat.SHIELDS_POOL] = 0
                for _ in range(15):
                    run.step({player: (yaw, pitch, FIRE)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertEqual(component.world.native(query, instance), 0.0)
                foreign = {"$": "STU_9B7648C3", "m_entity": expr.Entity(player.entity)}
                self.assertIsNone(component.world.native(foreign, instance))
                absent = {"$": "STU_9B7648C3", "m_entity": expr.Entity(0)}
                self.assertEqual(component.world.native(absent, instance), 0.0)

    def test_melee_damages_all_five_classes_once(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition, distance=1.5)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                run.step({player: (yaw, pitch, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, before - 30)

    def test_team_change_gates_both_attack_directions_for_all_five_classes(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                bot_health = fight.vitals_of(target)
                before = bot_health.total
                script = player.body_script
                script.ult_seconds = 0
                for _ in range(40):
                    run.step({})
                    if bot.attack_input:
                        break
                self.assertTrue(bot.attack_input)
                player_health = health.total
                x, y, z = bot.position
                angles = aim(fight._eye(player), (x, y + 1, z))

                hostile_team = bot.team
                bot.team = player.team
                run.step({player: (*angles, FIRE)})
                self.assertIsNone(npc_control.selected(run.match, player))
                self.assertFalse(bot.attack_input)
                for _ in range(150):
                    run.step({})
                self.assertEqual((health.total, bot_health.total), (player_health, before))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

                self.assertFalse(fight.is_stunned(player))

                bot.team = hostile_team
                for _ in range(200):
                    run.step({})
                    if health.total < player_health:
                        break
                self.assertLess(health.total, player_health)
                self.assertIsNone(bot.body_script.component.fault)

                # End the resumed attack before testing return fire. An Assassin
                # can pin Tracer on the first hostile frame.
                self.assertEqual(npc_control.stop(run.match), 1)
                run.step({})
                x, y, z = bot.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*angles, FIRE)})
                self.assertLess(bot_health.total, before)
                self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(),
                                       before - bot_health.total)

    def test_each_actual_body_can_die_and_keeps_its_corpse_past_respawn(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                # Finish a wounded NPC through the actual input damage path.
                vitals.now[combat.HEALTH_POOL] = 11
                vitals.now[combat.SHIELDS_POOL] = 0
                run.step({player: (yaw, pitch, FIRE)})
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertTrue(player.client.deaths)
                self.assertIsNotNone(bot.body_script)
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))

    def engage(self, definition):
        run, player, bot, _, yaw, pitch = self.fight(definition)
        player.mover.yaw, player.mover.pitch = yaw, pitch
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        victim = next(t for t in run.match.combat._targets() if t.owner is player)
        return run, player, bot, run.match.combat.vitals_of(victim)

    def test_all_five_npc_engagements_recover_after_range_rejection(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                bot_health = fight.vitals_of(target)
                before = bot_health.total
                script = player.body_script
                script.ult_seconds = 0
                start = bot.position
                x, y, z = player.mover.position
                bot.position = (x, y, z + 105)
                angles = aim(fight._eye(player), (x, y + 1, z + 105))
                run.step({player: (*angles, FIRE)})
                self.assertIsNone(npc_control.selected(run.match, player))
                for _ in range(30):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                self.assertEqual((health.total, bot_health.total), (150, before))
                self.assertFalse(player.client.messages(combat.HIT))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

                bot.position = start
                angles = aim(fight._eye(player), (start[0], start[1] + 1, start[2]))
                run.step({player: (*angles, FIRE)})
                self.assertLess(bot_health.total, before)
                self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(),
                                       before - bot_health.total)
                self.assertIs(npc_control.selected(run.match, player), bot)
                if definition.name == "Assassin":
                    # The retained target is visible at five metres but outside pin reach.
                    self.assertFalse(bot.attack_input)
                    self.assertFalse(fight.pins)
                for _ in range(200):
                    run.step({})
                    if health.total < 150:
                        break
                self.assertLess(health.total, 150)
                if definition.name == "Assassin":
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    self.assertLess(bot.position[2], start[2] - 2)
                self.assertIsNone(bot.body_script.component.fault)

    def test_actual_trooper_and_tracer_exchange_fire_after_concussion(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, player, bot, target, _, _ = self.fight(definition, distance=9)
        pharah = next(p for p in run.players if p is not player)
        self.assertTrue(run.match.switch_hero(pharah, heroes()[0x02E0000000000008]))
        pharah.team = bot.team
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        x, y, z = player.mover.position
        pharah.mover.position[:] = (x, y, z - 8)
        pharah.mover.body = None
        incoming = aim(fight._eye(pharah), (x, y + 1, z))
        run.step({pharah: (*incoming, 16)})
        observed = False
        for _ in range(70):
            run.step({pharah: (*incoming, 0)})
            observed |= bool(fight.rockets)
        self.assertTrue(observed)
        self.assertFalse(fight.rockets)
        self.assertGreater(player.mover.position[2], z + 3)
        victim = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(victim)
        self.assertEqual(health.total, 150)
        target_health = fight.vitals_of(target)
        before = target_health.total
        outgoing = aim(fight._eye(player), (bot.position[0], bot.position[1] + 1, bot.position[2]))
        run.step({player: (*outgoing, FIRE)})
        self.assertEqual(before - target_health.total, 12)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(120):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 130)
        run.step({player: (0, 0, 16)})
        self.assertTrue(tracer.recalling(player))
        for _ in range(85):
            if not tracer.recalling(player):
                break
            self.assertGreaterEqual(health.total, 130)
            run.step({})
        self.assertFalse(tracer.recalling(player))
        self.assertEqual(health.total, 150)
        self.assertAlmostEqual(player.mover.position[2], z, delta=0.02)
        for _ in range(120):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 130)
        self.assertIsNone(bot.body_script.component.fault)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "verified Supercharger supplement required",
    )
    def test_tracer_destroys_supercharger_and_removes_trooper_damage_boost(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, player, bot, _, _, _ = self.fight(definition)
        fight = run.match.combat
        orisa = next(p for p in run.players if p is not player)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[orisa_barrier.HERO]))
        orisa.team = bot.team
        x, y, z = player.mover.position
        orisa.mover.position[:] = (x - 6, y, z)
        orisa.mover.body = None
        for _ in range(20):
            run.step({})
        script = orisa.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({orisa: (0, 0, 32)})
        manager = fight.superchargers
        for _ in range(100):
            run.step({})
            if manager.active:
                break
        device, = manager.active.values()
        self.assertEqual(manager.scale(bot), 1.5)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))

        def receive_trooper_hit(amount):
            before = health.total
            self.assertEqual(npc_control.start(run.match, player), bot.entity)
            for _ in range(120):
                run.step({})
                if health.total < before:
                    break
            self.assertEqual(before - health.total, amount)
            self.assertEqual(npc_control.stop(run.match), 1)

        receive_trooper_hit(30)
        dx, dy, dz = device.position
        player.mover.position[:] = (dx, dy, dz + 4)
        player.mover.body = None
        run.step({})
        facing = aim(fight._eye(player), (dx, dy + 0.5, dz))
        for _ in range(17):
            run.step({player: (*facing, FIRE)})
            for _ in range(12):
                run.step({})
        self.assertEqual(device.health, 0)
        self.assertEqual(manager.scale(bot), 1)
        player.mover.position[:] = (x, y, z)
        player.mover.body = None
        run.step({})
        receive_trooper_hit(20)

    def test_five_actual_primary_attacks_damage_tracer_and_stop_without_late_damage(self):
        # Heavy Assault's first volley resolves two authored 5-point rays.
        expected = {"Trooper": (20, 29), "Enforcer": (2, 67),
                    "Sniper": (100, 128), "Assassin": (25, 10), "Heavy Assault": (10, 32)}
        for definition in self.definitions.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, _, bot, vitals = self.engage(definition)
                start_frame = run.frame
                for _ in range(200):
                    run.step({})
                    if vitals.total < 150:
                        break
                damage, first_frame = expected[definition.name]
                self.assertEqual((150 - vitals.total, run.frame - start_frame), (damage, first_frame))
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(80):
                    run.step({})
                self.assertEqual(vitals.total, 150 - damage)
                self.assertFalse(bot.attack_input)
                self.assertFalse(bot.body_script.component.shots)

    def test_actual_npc_lethal_attacks_credit_one_death_and_resume_on_respawn(self):
        for name, wound, impact in (
            ("Trooper", 20, 29), ("Enforcer", 2, 67), ("Sniper", 100, 128),
            ("Assassin", 25, 10), ("Heavy Assault", 5, 32), ("Heavy Assault", 7, 32),
        ):
            with self.subTest(npc=name, wound=wound):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                health.damage(health.total - wound)
                fight._sync_body_health(player, health)
                body, start = player.body, run.frame
                for _ in range(impact - 1):
                    run.step({})
                    self.assertEqual(health.total, wound)
                run.step({})
                self.assertTrue(health.dead)
                self.assertEqual((health.death_tick, health.killer), (start + impact, bot.entity))
                self.assertNotIn(body, fight.pins.values())
                notice, = player.client.messages(combat.DEATH_NOTICE)
                self.assertEqual(notice["+0x78"], combat.KILLED_BY)
                self.assertEqual(notice["+0x7C"]["+0x0"], bot.entity)
                deadline, = (when for when, owner in fight.respawns if owner is player)
                self.assertAlmostEqual(deadline - fight.now, combat.respawn_seconds(run.match))
                for _ in range(30):
                    run.step({player: (0, 0, FIRE)})
                    self.assertEqual(health.total, 0)
                    self.assertFalse(bot.attack_input)
                self.assertEqual(player.body, body)
                self.assertEqual(player.stats.value(stats.DEATHS), 1)
                self.assertEqual(player.client.messages(combat.DEATH_NOTICE), [notice])
                self.assertEqual([when for when, owner in fight.respawns if owner is player], [deadline])
                self.assertFalse(player.client.messages(combat.HIT))

                # Let the normal timer allocate a new body at a different spawn.
                x, y, z = player.mover.position
                player.spawn = ((x + 10, y, z), player.spawn[1])
                for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 2):
                    run.step({})
                    if player.body != body:
                        break
                self.assertNotEqual(player.body, body)
                self.assertGreaterEqual(fight.now, deadline)
                self.assertNotIn(body, fight.vitals)
                self.assertFalse(any(owner is player for _, owner in fight.respawns))
                replacement = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                self.assertEqual(replacement.total, 150)
                self.assertIsNone(replacement.killer)
                self.assertEqual(bot.attack_target, player.entity)
                for _ in range(200):
                    run.step({})
                    if replacement.total < 150:
                        break
                self.assertLess(replacement.total, 150)
                self.assertFalse(replacement.dead)
                self.assertEqual(player.stats.value(stats.DEATHS), 1)
                self.assertEqual(player.client.messages(combat.DEATH_NOTICE), [notice])

    def test_blink_passes_through_each_npc_and_both_sides_can_attack_after_landing(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _, _ = self.fight(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                victim = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(victim)
                script = player.body_script
                script.ult_seconds = 0
                component = script.component
                blink = next(i for i in component.instances.values() if i.graph.index == 0x24)
                start = tuple(player.mover.position)
                self.assertGreater(bot.position[2], start[2])
                run.step({player: (0, 0, 8)})
                self.assertAlmostEqual(player.mover.position[2] - start[2], 7.5, places=2)
                self.assertLess(bot.position[2], player.mover.position[2])
                self.assertEqual(tuple(player.mover.position), player.mover.history[-1].position)
                self.assertEqual(blink.vars[11].value(), 2)
                self.assertEqual((health.total, vitals.total), (150, before))
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)

                x, y, z = bot.position
                facing = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*facing, FIRE)})
                self.assertEqual(vitals.total, before - 12)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 12)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(200):
                    run.step({})
                    if health.total < 150:
                        break
                self.assertLess(health.total, 150)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(200):
                    run.step({})
                    if blink.vars[11].value() == 3:
                        break
                self.assertEqual(blink.vars[11].value(), 3)
                self.assertFalse(fight.is_stunned(player))

                run.step({player: (-32768, 0, 2)})
                self.assertAlmostEqual(player.mover.position[2], start[2], places=2)
                self.assertEqual(blink.vars[11].value(), 2)
                self.assertEqual(vitals.total, before - 12)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 12)
                self.assertIsNone(bot.body_script.component.fault)

    def test_static_wall_clips_blink_before_each_npc_capsule(self):
        from test_game_mover import box, wall

        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _, _ = self.fight(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.ult_seconds = 0
                component = script.component
                blink = next(i for i in component.instances.values() if i.graph.index == 0x24)
                match, mover = run.match, player.mover
                x, y, z = mover.position
                face = z + 3
                original_world = match.collision_world
                floor = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                cover = World(floor + wall(x - 5, face, x + 5, face, y, y + 4))
                match.collision_world = cover
                mover.use(world=cover)

                run.step({player: (0, 0, 8)})
                clipped = tuple(mover.position)
                self.assertGreater(clipped[2], z)
                self.assertLess(clipped[2], bot.position[2])
                self.assertAlmostEqual(face - clipped[2], mover.data.stand_radius, delta=0.03)
                self.assertEqual(mover.history[-1].position, clipped)
                self.assertEqual(blink.vars[11].value(), 2)
                self.assertIsNone(npc_control.selected(match, player))
                for _ in range(15):
                    run.step({player: (0, 0, 8)})
                settled_z = mover.position[2]
                self.assertAlmostEqual(settled_z, clipped[2], delta=0.01)
                self.assertEqual(blink.vars[11].value(), 2)

                match.collision_world = original_world
                mover.use(world=original_world)
                run.step({player: (0, 0, 8)})
                self.assertAlmostEqual(mover.position[2], settled_z, delta=0.01)
                self.assertEqual(blink.vars[11].value(), 2)
                run.step({})
                before_second = mover.position[2]
                run.step({player: (0, 0, 8)})
                self.assertAlmostEqual(mover.position[2] - before_second, 7.5, places=2)
                self.assertGreater(mover.position[2], bot.position[2])
                self.assertEqual(blink.vars[11].value(), 1)
                self.assertEqual(vitals.total, before)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)

    def test_blink_evades_launched_npc_round_then_attack_reaims(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                first = None
                for _ in range(150):
                    run.step({})
                    first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                    if first is not None:
                        break
                self.assertIsNotNone(first)
                self.assertEqual(health.total, 150)
                before = player.mover.position[0]
                old_z = player.mover.position[2]
                run.step({player: (16384, 0, 8)})
                self.assertGreater(player.mover.position[0], before + 6)
                for _ in range(7):
                    run.step({})
                self.assertEqual(health.total, 150)
                self.assertTrue(first not in fight.rockets or first.position[2] < old_z)
                later_launch = False
                for _ in range(350):
                    run.step({})
                    later_launch |= any(
                        rocket.shooter is bot and rocket is not first for rocket in fight.rockets
                    )
                    if health.total < 150:
                        break
                self.assertTrue(later_launch)
                self.assertEqual(health.total, 150 - damage)
                target = next(t for t in fight._targets() if t.owner is bot)
                vitals = fight.vitals_of(target)
                before = vitals.total
                facing = aim(fight._eye(player), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                run.step({player: (*facing, FIRE)})
                self.assertEqual(before - vitals.total, 12)
                self.assertIsNone(bot.body_script.component.fault)

    def test_blink_at_first_npc_impact_boundary(self):
        for name, impact, damage in self.FIRST_PRIMARY_IMPACTS:
            for early in (False, True):
                with self.subTest(npc=name, early=early):
                    self.check_npc_attack_blink_boundary(name, impact, damage, early)

    def check_npc_attack_blink_boundary(self, name, impact, damage, early):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, health = self.engage(definition)
        fight = run.match.combat
        blink = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x24)
        start = run.frame
        for _ in range(impact - (2 if early else 1)):
            run.step({})
            self.assertEqual(health.total, 150)
        x, _, z = player.mover.position
        hitscan = name in ("Trooper", "Heavy Assault")
        first = None
        if name in ("Enforcer", "Sniper"):
            first, = (shot for shot in fight.rockets if shot.shooter is bot)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rays:
            run.step({player: (16384, 0, 8)})
            if early:
                self.assertEqual(health.total, 150)
                run.step({})
        self.assertEqual(run.frame, start + impact)
        blocked = name == "Assassin" and not early
        self.assertAlmostEqual(player.mover.position[0] - x, 0 if blocked else 7.5, places=2)
        self.assertEqual(blink.vars[11].value(), 3 if blocked else 2)
        if hitscan:
            self.assertEqual(rays.call_count, 1 if name == "Trooper" else 2)
            for ray in rays.call_args_list:
                self.assertIs(ray.args[0], bot)
                if early:
                    self.assertGreater(ray.args[3][0], 0.5)
                else:
                    self.assertAlmostEqual(ray.args[3][0], 0, delta=0.1)
        evaded = early and not hitscan
        self.assertEqual(health.total, 150 if evaded else 150 - damage)
        if name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), player.body if blocked else None)
        if evaded:
            later_launch = False
            for _ in range(350):
                run.step({})
                later_launch |= any(shot.shooter is bot and shot is not first for shot in fight.rockets)
                if health.total < 150:
                    break
            self.assertEqual(health.total, 150 - damage)
            if first is not None:
                self.assertTrue(later_launch)
                self.assertTrue(first not in fight.rockets or first.position[2] < z)
            else:
                self.assertEqual(fight.pins.get(bot.entity), player.body)

    def test_actual_npc_round_hits_after_tracer_kills_shooter(self):
        for name, damage, lethal in (("Enforcer", 2, False), ("Sniper", 100, False), ("Sniper", 100, True)):
            with self.subTest(npc=name, lethal=lethal):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                first = None
                for _ in range(150):
                    run.step({})
                    first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                    if first is not None:
                        break
                self.assertIsNotNone(first)
                self.assertEqual(health.total, 150)
                if lethal:
                    health.damage(50)
                before = health.total
                target = next(t for t in fight._targets() if t.owner is bot)
                vitals = fight.vitals_of(target)
                # Seed only the health precondition. Both attacks and the death are real.
                vitals.damage(vitals.total - 1)
                self.assertEqual(vitals.total, 1)
                facing = aim(fight._eye(player), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                run.step({player: (*facing, FIRE)})
                self.assertTrue(vitals.dead)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.killer, player.body)
                self.assertIn(first, fight.rockets)
                self.assertEqual(health.total, before)
                released = tuple(fight.rockets)
                for _ in range(30):
                    run.step({})
                    self.assertTrue(all(any(rocket is old for old in released) for rocket in fight.rockets))
                self.assertNotIn(first, fight.rockets)
                self.assertEqual(health.total, before - damage)
                self.assertEqual(health.dead, lethal)
                if lethal:
                    self.assertEqual(health.killer, bot.entity)
                self.assertFalse(bot.attack_input)
                self.assertIn(bot, run.match.bots)

    def test_genji_deflect_protects_tracer_and_returns_authored_npc_rounds(self):
        for name, damage, lethal in (("Enforcer", 2, False), ("Sniper", 100, False), ("Sniper", 100, True)):
            with self.subTest(npc=name, lethal=lethal):
                self.check_npc_round_deflect(name, damage, lethal)

    def test_npc_round_deflect_respects_genji_facing_and_team(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            for gate in ("rear", "allied"):
                with self.subTest(npc=name, gate=gate):
                    self.check_npc_round_deflect(name, damage, False, gate=gate)

    def test_sniper_round_checks_winston_dome_before_and_after_deflect(self):
        for gate in ("dome_before", "dome_after"):
            with self.subTest(gate=gate):
                self.check_npc_round_deflect("Sniper", 100, False, gate=gate)

    def check_npc_round_deflect(self, name, damage, lethal, *, gate=None):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, target, _, _ = self.fight(definition, distance=10)
        fight = run.match.combat
        self.start_npc_at_tracer(run, player, bot)
        genji, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000029)
        genji.client = NpcClient(genji)
        genji.team = bot.team if gate == "allied" else player.team
        x, y, z = bot.position
        dome = None
        if gate in ("dome_before", "dome_after"):
            winston = run.players[1]
            self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
            winston.team = player.team if gate == "dome_before" else bot.team
            dome_offset = 5 if gate == "dome_before" else 1
            bubble_tests.TracerWinstonBarrierTests.move(winston, (x + 2.5, y, z - dome_offset))
            dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
        facing = (-32768, 0) if gate == "rear" else aim(fight._eye(genji), (x, y + 1.1, z))
        vitals = fight.vitals_of(target)
        if lethal:
            vitals.damage(vitals.total - damage)
        before = vitals.total
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        protected = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))

        for _ in range(100 if name == "Sniper" else 42):
            run.step({genji: (*facing, 0)})
        run.step({genji: (*facing, 16)})
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(instance.state(10).active)
        first = None
        for _ in range(65):
            run.step({genji: (*facing, 0)})
            if first is None:
                first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
            if first is not None and (
                first not in fight.rockets or (dome is None and first.shooter is genji)
            ):
                break
        self.assertIsNotNone(first)
        if dome is not None:
            self.assertIs(first.shooter, genji if gate == "dome_after" else bot)
            self.assertNotIn(first, fight.rockets)
            self.assertEqual(dome.health, 600)
            self.assertEqual((own.total, protected.total, vitals.total), (150, 200, before))
            self.assertEqual(genji.client.messages(combat.HIT), [])
            return
        if gate is not None:
            self.assertIs(first.shooter, bot)
            self.assertNotIn(first, fight.rockets)
            self.assertEqual(own.total, 150 - damage if gate == "allied" else 150)
            self.assertEqual(protected.total, 200 - damage if gate == "rear" else 200)
            self.assertEqual(vitals.total, before)
            self.assertEqual(genji.client.messages(combat.HIT), [])
            return
        self.assertIs(first.shooter, genji)
        self.assertEqual(first.team, player.team)
        self.assertGreater(first.direction[2], 0)
        for _ in range(30):
            run.step({genji: (*facing, 0)})
            if vitals.total < before:
                break
        self.assertEqual((own.total, protected.total, vitals.total), (150, 200, before - damage))
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
        hit, = genji.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
        self.assertEqual(vitals.dead, lethal)
        if lethal:
            self.assertEqual(vitals.killer, genji.body)

    def test_sigma_grasp_absorbs_authored_npc_rounds_and_grants_shields(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            for frontal in (True, False):
                with self.subTest(npc=name, frontal=frontal):
                    self.check_npc_round_grasp(name, damage, frontal=frontal)

    def check_npc_round_grasp(self, name, damage, *, frontal):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, target, _, _ = self.fight(definition, distance=10)
        fight = run.match.combat
        self.start_npc_at_tracer(run, player, bot)
        sigma, _ = support_tests.PulseDeflectTests().support(run, player, hero=sigma_grasp.HERO)
        sigma.client = NpcClient(sigma)
        sigma.team = player.team
        x, y, z = bot.position
        facing = aim(fight._eye(sigma), (x, y + 1.1, z)) if frontal else (-32768, 0)
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        before = health[bot].total

        for _ in range(100 if name == "Sniper" else 42):
            run.step({sigma: (*facing, 0)})
        run.step({sigma: (*facing, 8)})
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(instance.state(1).active)
        first = None
        for _ in range(65):
            run.step({sigma: (*facing, 0)})
            if first is None:
                first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
            elif first not in fight.rockets:
                break
        self.assertIsNotNone(first)
        self.assertNotIn(first, fight.rockets)
        self.assertEqual(instance.vars[21312].value(), damage if frontal else 0)
        self.assertEqual(health[sigma].total, 400 if frontal else 400 - damage)
        self.assertEqual(health[player].total, 150)
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertEqual(sigma.client.messages(combat.HIT), [])
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(150):
            run.step({sigma: (*facing, 0)})
        self.assertFalse(instance.state(1).active)
        shields = health[sigma].temporary_shields
        if frontal:
            self.assertAlmostEqual(shields[sigma_grasp.SHIELD_TAG].now, 0.6 * damage, places=5)
            self.assertEqual(health[sigma].now[combat.SHIELDS_POOL], 100)
        else:
            self.assertNotIn(sigma_grasp.SHIELD_TAG, shields)

    def test_sniper_round_resolves_at_nearer_grasp_or_deflect(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        for genji_first in (False, True):
            with self.subTest(genji_first=genji_first):
                run, player, bot, target, _, _ = self.fight(definition, distance=12)
                fight = run.match.combat
                self.start_npc_at_tracer(run, player, bot)
                sigma, _ = support_tests.PulseDeflectTests().support(run, player, hero=sigma_grasp.HERO)
                sigma.client = NpcClient(sigma)
                sigma.team = player.team
                genji = run.players[1]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.team = player.team
                x, y, z = player.mover.position
                sigma.mover.position[:] = (x, y, z + 5)
                genji.mover.position[:] = (x, y, z + (9 if genji_first else 1))
                sigma.mover.body = genji.mover.body = None
                bx, by, bz = bot.position
                sigma_aim = aim(fight._eye(sigma), (bx, by + 1.1, bz))
                genji_aim = aim(fight._eye(genji), (bx, by + 1.1, bz))
                for _ in range(100):
                    run.step({})
                run.step({sigma: (*sigma_aim, 8), genji: (*genji_aim, 16)})
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                deflect = next(i for i in genji.body_script.component.instances.values()
                               if i.graph.index == 0x03A3)
                self.assertTrue(grasp.state(1).active)
                self.assertTrue(deflect.state(10).active)
                health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
                before = health[bot].total
                first = None
                for _ in range(65):
                    run.step({sigma: (*sigma_aim, 0), genji: (*genji_aim, 0)})
                    if first is None:
                        first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                    elif first not in fight.rockets:
                        break
                self.assertIsNotNone(first)
                self.assertNotIn(first, fight.rockets)
                self.assertIs(first.shooter, genji if genji_first else bot)
                self.assertEqual(grasp.vars[21312].value(), 0 if genji_first else 100)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 100 if genji_first else 0)
                self.assertEqual(fight.vitals_of(target).total, before - (100 if genji_first else 0))
                self.assertEqual(
                    (health[player].total, health[sigma].total, health[genji].total),
                    (150, 400, 200),
                )

    def test_winston_dome_stops_hostile_npc_rounds_and_allows_allied_rounds(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            for allied in (False, True):
                with self.subTest(npc=name, allied=allied):
                    self.check_npc_round_dome(name, damage, allied=allied)

    def check_npc_round_dome(self, name, damage, *, allied):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, target, _, _ = self.fight(definition, distance=14)
        fight = run.match.combat
        winston, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000009)
        winston.client = NpcClient(winston)
        winston.team = bot.team if allied else player.team
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(winston, (x + 2.5, y, z))
        dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
        self.assertEqual(dome.health, 700)
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        before = fight.vitals_of(target).total
        self.start_npc_at_tracer(run, player, bot)
        first = None
        for _ in range(160):
            run.step({})
            if first is None:
                first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
            elif first not in fight.rockets:
                break
        self.assertIsNotNone(first)
        self.assertNotIn(first, fight.rockets)
        self.assertEqual(dome.health, 700 if allied else 700 - damage)
        self.assertEqual(health[player].total, 150 - damage if allied else 150)
        self.assertEqual(health[winston].total, 500)
        self.assertEqual(health[bot].total, before)
        if allied:
            return

        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(math.ceil((dome.expires - fight.now) / combat.TICK) + 1):
            run.step({})
            if winston.body not in fight.winston_barriers.actors:
                break
        self.assertNotIn(winston.body, fight.winston_barriers.actors)
        self.assertGreaterEqual(fight.now, dome.expires)
        self.assertEqual(health[player].total, 150)
        self.start_npc_at_tracer(run, player, bot)
        for _ in range(160):
            run.step({})
            if health[player].total < 150:
                break
        self.assertEqual(health[player].total, 150 - damage)
        self.assertEqual(dome.health, 700 - damage)

    def test_deployed_mesh_barriers_stop_both_authored_npc_rounds(self):
        for hero in (0x02E0000000000007, sigma_barrier.HERO, orisa_barrier.HERO):
            for name, damage in (("Enforcer", 2), ("Sniper", 100)):
                with self.subTest(hero=hero, npc=name):
                    if hero == orisa_barrier.HERO and not os.environ.get("ORPHEUS_ORISA_GEOMETRY"):
                        self.skipTest("verified external Orisa mesh required")
                    self.check_npc_round_mesh(hero, name, damage)

    def test_mesh_barriers_resolve_trooper_and_heavy_hitscan_volleys(self):
        for hero in (0x02E0000000000007, sigma_barrier.HERO, orisa_barrier.HERO):
            for name, damage, rays, breaks in (
                ("Trooper", 20, 1, False), ("Heavy Assault", 10, 2, False), ("Heavy Assault", 10, 2, True),
            ):
                with self.subTest(hero=hero, npc=name, breaks=breaks):
                    if hero == orisa_barrier.HERO and not os.environ.get("ORPHEUS_ORISA_GEOMETRY"):
                        self.skipTest("verified external Orisa mesh required")
                    self.check_npc_round_mesh(hero, name, damage, rays=rays, breaks=breaks)

    def check_npc_round_mesh(self, hero, name, damage, *, rays=0, breaks=False):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, _, _, _ = self.fight(definition, distance=14)
        fight = run.match.combat
        defender = run.players[1]
        self.assertTrue(run.match.switch_hero(defender, heroes()[hero]))
        defender.team = player.team
        held = {}
        if hero == orisa_barrier.HERO:
            bx, by, bz = bot.position
            bot.position = (bx + 20, by, bz)
            fixture = orisa_tests.TracerOrisaBarrierTests()
            fixture.run, fixture.fight, fixture.tracer, fixture.orisa = run, fight, player, defender
            fixture.deploy()
            shield, field = fixture.barrier, "health"
            x, y, z = shield.position
            fixture.move(player, (x, y, z - 1))
            fixture.move(defender, (x + 3, y, z - 2))
            bot.position = (x, y, z + 14)
            run.step({})
        else:
            for _ in range(30):
                run.step({})
            if not rays:
                self.start_npc_at_tracer(run, player, bot)
            x, y, z = player.mover.position
            bubble_tests.TracerWinstonBarrierTests.move(defender, (x + (1.3 if rays else 0), y, z + 0.5))
            guard = {defender: (0, 0, SECONDARY)}
            for _ in range(8 if hero == sigma_barrier.HERO else 18):
                run.step(guard)
            if hero == sigma_barrier.HERO:
                for _ in range(27):
                    run.step({})
                barrier, = fight.sigma_barriers.values()
                self.assertTrue(barrier.active and barrier.child_request_seen)
                shield, field = barrier.vitals, "total"
            else:
                shield, field = fight.reinhardt_barriers.states[defender.body], "current"
                self.assertTrue(shield.active)
                held = guard

        if rays or hero == orisa_barrier.HERO:
            self.start_npc_at_tracer(run, player, bot)
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        if breaks:
            # Seed the live barrier so the first five-point ray breaks it.
            if hero == sigma_barrier.HERO:
                shield.damage(shield.total - 4)
            else:
                setattr(shield, field, 4)
        shield_before, defender_before = getattr(shield, field), health[defender].total
        if rays:
            with patch.object(fight, "_hitscan", wraps=fight._hitscan) as scan:
                for _ in range(75):
                    run.step(held)
                    if scan.called:
                        break
            self.assertEqual(scan.call_count, rays)
            self.assertTrue(all(call.args[0] is bot for call in scan.call_args_list))
            self.assertAlmostEqual(shield_before - getattr(shield, field), 4 if breaks else damage, places=4)
            self.assertEqual(health[player].total, 145 if breaks else 150)
            self.assertEqual(health[defender].total, defender_before)
            return
        first = None
        for _ in range(160):
            run.step(held)
            if first is None:
                first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
            elif first not in fight.rockets:
                break
        self.assertIsNotNone(first)
        self.assertNotIn(first, fight.rockets)
        self.assertAlmostEqual(shield_before - getattr(shield, field), damage, places=4)
        self.assertEqual((health[player].total, health[defender].total), (150, defender_before))

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile definition tags required")
    def test_matrix_absorbs_hostile_npc_rounds_and_allows_allied_rounds(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            for allied in (False, True):
                with self.subTest(npc=name, allied=allied):
                    self.check_npc_round_matrix(name, damage, allied=allied)

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile definition tags required")
    def test_enforcer_round_checks_matrix_before_projected_particle_barrier(self):
        self.check_npc_round_matrix("Enforcer", 2, allied=False, projected=True)

    def check_npc_round_matrix(self, name, damage, *, allied, projected=False):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, _, _, _ = self.fight(definition, distance=14)
        fight = run.match.combat
        mech = run.players[1]
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[dva.HERO], body=dva.MECH)))
        mech.team = bot.team if allied else player.team
        for _ in range(100):
            run.step({})
        self.start_npc_at_tracer(run, player, bot)
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(mech, (x, y, z + 3))
        guard = {mech: (0, 0, SECONDARY)}
        if projected:
            zarya, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000068)
            zarya.client = NpcClient(zarya)
            zarya.team = player.team
            bubble_tests.TracerWinstonBarrierTests.move(zarya, (x + 5, y, z))
            facing = aim(fight._eye(zarya), fight._eye(player))
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        if name == "Sniper":
            for _ in range(95):
                run.step({})
        if projected:
            for _ in range(42):
                run.step({})
            run.step({**guard, zarya: (*facing, 0)})
            run.step({**guard, zarya: (*facing, 16)})
            pet = zarya_barrier.active(player)
            self.assertIsNotNone(pet)
        first = self.wait_for_npc_round(run, bot, guard)
        self.assertTrue(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 100)
        for _ in range(30):
            run.step(guard)
            if first not in fight.rockets:
                break
        self.assertNotIn(first, fight.rockets)
        self.assertEqual(health[player].total, 150 - damage if allied else 150)
        self.assertEqual(health[mech].total, 600)
        self.assertIs(first.shooter, bot)
        self.assertEqual(mech.client.messages(combat.HIT), [])
        if allied:
            return

        if projected:
            self.assertIs(zarya_barrier.active(player), pet)
            self.assertEqual(zarya_barrier.barrier_health(pet, health[player]), 200)
            self.assertEqual(zarya.body_script.component.entity_var(87).value() or 0, 0)
            return
        bubble_tests.TracerWinstonBarrierTests.move(mech, (x + 5, y, z + 3))
        run.step({})
        self.assertFalse(matrix.state(3).active)
        for _ in range(160):
            run.step({})
            if health[player].total < 150:
                break
        self.assertEqual(health[player].total, 150 - damage)

    def wait_for_npc_round(self, run, bot, inputs=None):
        for _ in range(160):
            run.step(inputs or {})
            shot = next((rocket for rocket in run.match.combat.rockets if rocket.shooter is bot), None)
            if shot is not None:
                return shot
        self.fail("manual NPC did not launch an authored round")

    def test_projected_particle_barrier_absorbs_npc_rounds_and_credits_zarya(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            for breaks in (False, True):
                with self.subTest(npc=name, breaks=breaks):
                    self.check_npc_round_particle(name, damage, breaks=breaks)

    def npc_particle_scene(self, name):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, _, _, _ = self.fight(definition, distance=14)
        fight = run.match.combat
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.team = player.team
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(zarya, (x + 5, y, z))
        guard = aim(fight._eye(zarya), fight._eye(player))
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        return run, player, bot, zarya, guard, health

    def check_npc_round_particle(self, name, damage, *, breaks):
        run, player, bot, zarya, guard, health = self.npc_particle_scene(name)
        fight = run.match.combat
        self.start_npc_at_tracer(run, player, bot)
        shot = self.wait_for_npc_round(run, bot)
        run.step({zarya: (*guard, 0)})
        run.step({zarya: (*guard, 16)})
        pet = zarya_barrier.active(player)
        self.assertIsNotNone(pet)
        self.assertEqual(pet.instance.component.entity, zarya.body)
        self.assertIn(shot, fight.rockets)
        shield = damage / 2 if breaks else 200
        if breaks:
            health[player].particle_barriers[zarya_barrier.particle_barrier_key(pet)] = shield
        for _ in range(30):
            run.step({})
            if shot not in fight.rockets:
                break
        self.assertNotIn(shot, fight.rockets)
        self.assertEqual((health[player].total, health[zarya].total), (150, 400))
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), min(shield, damage) / 5,
                               places=5)
        if breaks:
            self.assertIsNone(zarya_barrier.active(player))
            for _ in range(160):
                run.step({})
                if health[player].total < 150:
                    break
            self.assertEqual(health[player].total, 150 - damage)
        else:
            self.assertEqual(zarya_barrier.barrier_health(pet, health[player]), shield - damage)

    def test_projected_particle_barrier_resolves_trooper_and_heavy_hitscan_volleys(self):
        for name, damage, rays, weak_pool in (("Trooper", 20, 1, 10), ("Heavy Assault", 10, 2, 4)):
            for breaks in (False, True):
                with self.subTest(npc=name, breaks=breaks):
                    self.check_npc_hitscan_particle(name, damage, rays, weak_pool if breaks else None)

    def check_npc_hitscan_particle(self, name, damage, rays, weak_pool):
        run, player, bot, zarya, guard, health = self.npc_particle_scene(name)
        fight = run.match.combat
        run.step({zarya: (*guard, 0)})
        run.step({zarya: (*guard, 16)})
        pet = zarya_barrier.active(player)
        self.assertIsNotNone(pet)
        self.assertEqual(pet.instance.component.entity, zarya.body)
        self.assertEqual(zarya_barrier.barrier_health(pet, health[player]), 200)
        if weak_pool is not None:
            health[player].particle_barriers[zarya_barrier.particle_barrier_key(pet)] = weak_pool
        self.start_npc_at_tracer(run, player, bot)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as scan:
            for _ in range(75):
                run.step({})
                if scan.called:
                    break
        self.assertEqual(scan.call_count, rays)
        self.assertTrue(all(call.args[0] is bot for call in scan.call_args_list))
        self.assertEqual(health[zarya].total, 400)
        energy = zarya.body_script.component.entity_var(87)
        if weak_pool is None:
            self.assertEqual(health[player].total, 150)
            self.assertEqual(zarya_barrier.barrier_health(pet, health[player]), 200 - damage)
            self.assertAlmostEqual(energy.value(), damage / 5, places=5)
            return
        self.assertIsNone(zarya_barrier.active(player))
        self.assertAlmostEqual(energy.value(), weak_pool / 5, places=5)
        self.assertEqual(health[player].total, 145 if rays == 2 else 150)
        if rays == 1:
            for _ in range(75):
                run.step({})
                if health[player].total < 150:
                    break
            self.assertEqual(health[player].total, 130)

    def test_heavy_paired_rays_break_dome_then_spend_projected_particle_barrier(self):
        run, player, bot, zarya, guard, health = self.npc_particle_scene("Heavy Assault")
        fight = run.match.combat
        winston, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000009)
        winston.client = NpcClient(winston)
        winston.team = player.team
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(winston, (x + 2.5, y, z - 1))
        dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
        self.assertEqual(dome.health, 700)
        run.step({zarya: (*guard, 0)})
        run.step({zarya: (*guard, 16)})
        pet = zarya_barrier.active(player)
        self.assertIsNotNone(pet)
        self.assertEqual(zarya_barrier.barrier_health(pet, health[player]), 200)
        dome.health = 4
        self.start_npc_at_tracer(run, player, bot)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as scan:
            for _ in range(75):
                run.step({})
                if scan.called:
                    break
        self.assertEqual(scan.call_count, 2)
        self.assertTrue(all(call.args[0] is bot for call in scan.call_args_list))
        self.assertEqual(dome.health, 0)
        self.assertEqual(zarya_barrier.barrier_health(pet, health[player]), 195)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 1)
        self.assertEqual((health[player].total, health[zarya].total), (150, 400))

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile definition tags required")
    def test_trooper_and_heavy_rays_resolve_against_live_player_defenses(self):
        for name, damage, count in (("Trooper", 20, 1), ("Heavy Assault", 10, 2)):
            for defense in ("dome", "deflect", "grasp", "matrix"):
                with self.subTest(npc=name, defense=defense):
                    self.check_npc_hitscan_defense(name, damage, count, defense)

    def check_npc_hitscan_defense(self, name, damage, count, defense):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, target, _, _ = self.fight(definition)
        fight = run.match.combat
        defender = run.players[1]
        hero = {
            "dome": heroes()[0x02E0000000000009],
            "deflect": heroes()[0x02E0000000000029],
            "grasp": heroes()[sigma_grasp.HERO],
            "matrix": replace(heroes()[dva.HERO], body=dva.MECH),
        }[defense]
        self.assertTrue(run.match.switch_hero(defender, hero))
        defender.team = player.team
        for _ in range(100 if defense == "matrix" else 30):
            run.step({})
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        vitals = fight.vitals_of(target)
        if defense == "deflect" and name == "Heavy Assault":
            vitals.now.update({combat.HEALTH_POOL: 7, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
        before = vitals.total
        defender_before = health[defender].total
        x, y, z = player.mover.position
        if defense == "dome":
            bubble_tests.TracerWinstonBarrierTests.move(defender, (x + 2.5, y, z - 1))
            dome = bubble_tests.TracerWinstonBarrierTests().cast(run, defender)
            self.start_npc_at_tracer(run, player, bot)
            held = {}
        else:
            self.start_npc_at_tracer(run, player, bot)
            bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z + 2))
            bx, by, bz = bot.position
            facing = aim(fight._eye(defender), (bx, by + 1, bz))
            graph_index, node, button = {
                "deflect": (0x03A3, 10, 16), "grasp": (sigma_grasp.GRASP, 1, 8), "matrix": (0x02CC, 3, 2),
            }[defense]
            run.step({defender: (*facing, button)})
            instance = next(i for i in defender.body_script.component.instances.values()
                            if i.graph.index == graph_index)
            self.assertTrue(instance.state(node).active)
            held = {defender: (*facing, button if defense == "matrix" else 0)}
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as scan:
            for _ in range(75):
                run.step(held)
                if scan.called:
                    break
            self.assertEqual(scan.call_count, count)
            self.assertTrue(all(call.args[0] is bot for call in scan.call_args_list))
        self.assertEqual((health[player].total, health[defender].total), (150, defender_before))
        if defense == "dome":
            self.assertEqual(dome.health, 700 - damage)
        elif defense == "grasp":
            self.assertEqual(instance.vars[21312].value(), damage)
            self.assertEqual(instance.vars[6584].value(), damage / count)
            self.assertEqual(npc_control.stop(run.match), 1)
            for _ in range(130):
                run.step(held)
            self.assertFalse(instance.state(1).active)
            shield = health[defender].temporary_shields[sigma_grasp.SHIELD_TAG]
            self.assertAlmostEqual(shield.now, 0.6 * damage, places=5)
        elif defense == "deflect":
            returned = min(before, damage)
            self.assertEqual(vitals.total, before - returned)
            self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), returned)
            self.assertEqual(len(defender.client.messages(combat.HIT)), count)
            if name == "Heavy Assault":
                self.assertEqual(vitals.killer, defender.body)
        else:
            self.assertTrue(instance.state(3).active)
            self.assertLess(instance.vars[87].value(), 100)
        if defense != "deflect":
            self.assertEqual(vitals.total, before)

    def test_actual_pulse_kills_sniper_after_tracer_dies(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, player, bot, health = self.engage(definition)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        vitals = fight.vitals_of(target)
        source_body = player.body
        # Arrange for the Sniper's actual first round to be lethal.
        health.damage(50)
        for _ in range(110):
            run.step({})
        self.assertEqual(health.total, 100)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 2059, 32)})
        for _ in range(14):
            if fight.pulse_bombs.bombs:
                break
            run.step({player: (0, 2059, 0)})
        bomb, = fight.pulse_bombs.bombs
        self.assertEqual(bomb.source, source_body)
        self.assertFalse(health.dead)
        for _ in range(20):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertTrue(any(active is bomb for active in fight.pulse_bombs.bombs))
        self.assertFalse(vitals.dead)
        for _ in range(120):
            run.step({})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, source_body)
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertIn(bot, run.match.bots)

    def test_actual_sniper_kill_cancels_unreleased_pulse(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, player, bot, health = self.engage(definition)
        fight = run.match.combat
        health.damage(50)
        for _ in range(124):
            run.step({})
        self.assertEqual(health.total, 100)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 2059, 32)})
        self.assertFalse(health.dead)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(30):
            run.step({})
            self.assertFalse(fight.pulse_bombs.bombs)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(bot.dead)

    def test_actual_npc_lethal_attacks_respect_pulse_release_boundary(self):
        for name, wound, impact in (
            ("Trooper", 20, 29), ("Enforcer", 2, 67), ("Sniper", 100, 128),
            ("Assassin", 25, 10), ("Heavy Assault", 7, 32),
        ):
            definition = next(d for d in self.definitions.values() if d.name == name)
            for released in (False, True):
                with self.subTest(npc=name, released=released):
                    self.check_lethal_npc_pulse_boundary(definition, wound, impact, released)

    def check_lethal_npc_pulse_boundary(self, definition, wound, impact, released):
        run, player, bot, target, yaw, pitch = self.fight(definition)
        fight = run.match.combat
        source = player.body
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        npc_health = fight.vitals_of(target)
        health.damage(health.total - wound)
        fight._sync_body_health(player, health)
        if npc_health.total > 300:
            npc_health.damage(npc_health.total - 300)
        npc_start_after_release = released and definition.name == "Assassin"
        if not npc_start_after_release:
            player.mover.yaw, player.mover.pitch = yaw, pitch
            self.assertEqual(npc_control.start(run.match, player), bot.entity)
            for _ in range(impact - (26 if released else 4)):
                run.step({})
        script = player.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (yaw, 0, 32)})
        self.assertEqual(charge.value(), 0)
        self.assertEqual(health.total, wound)

        if released:
            for _ in range(24):
                self.assertFalse(health.dead)
                if fight.pulse_bombs.bombs:
                    break
                run.step({})
            bomb, = fight.pulse_bombs.bombs
            self.assertEqual(bomb.source, source)
            if npc_start_after_release:
                player.mover.yaw, player.mover.pitch = yaw, pitch
                self.assertEqual(npc_control.start(run.match, player), bot.entity)

        for _ in range(28):
            if not released:
                self.assertFalse(fight.pulse_bombs.bombs)
            if health.dead:
                break
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(npc_health.dead)
        self.assertEqual(player.stats.value(stats.DEATHS), 1)
        if released:
            self.assertEqual(fight.pulse_bombs.bombs, [bomb])
        attached = released and bomb.attached == bot.entity
        for _ in range(90):
            if not released:
                self.assertFalse(fight.pulse_bombs.bombs)
            elif npc_health.dead:
                break
            run.step({})
            if released and bomb.attached == bot.entity:
                attached = True
        self.assertEqual(attached, released)
        self.assertEqual(npc_health.dead, released)
        self.assertEqual(npc_health.killer, source if released else None)
        self.assertFalse(fight.pulse_bombs.bombs)

    def test_recall_protects_from_already_launched_enforcer_and_sniper_rounds(self):
        for name, damage in (("Enforcer", 2), ("Sniper", 100)):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                first = None
                for _ in range(150):
                    run.step({})
                    first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                    if first is not None:
                        break
                self.assertIsNotNone(first)
                position = tuple(player.mover.position)
                self.assertEqual(health.total, 150)
                run.step({player: (0, 0, 16)})
                self.assertTrue(tracer.recalling(player))
                for _ in range(7):
                    run.step({})
                self.assertTrue(tracer.recalling(player))
                self.assertTrue(first not in fight.rockets or first.position[2] < position[2])
                self.assertEqual(health.total, 150)
                for _ in range(85):
                    if not tracer.recalling(player):
                        break
                    run.step({})
                    self.assertEqual(health.total, 150)
                self.assertFalse(tracer.recalling(player))
                self.assertEqual(tuple(player.mover.position), position)
                later_launch = False
                for _ in range(350):
                    run.step({})
                    later_launch |= any(
                        rocket.shooter is bot and rocket is not first for rocket in fight.rockets
                    )
                    if health.total < 150:
                        break
                self.assertTrue(later_launch)
                self.assertEqual(health.total, 150 - damage)

    def test_recall_restores_position_health_and_ammo_after_each_npc_attack(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                fight = run.match.combat
                victim = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(victim)
                script = player.body_script
                script.ult_seconds = 0
                component = script.component
                pistol = next(i for i in component.instances.values() if i.graph.index == 0x16)
                recall = next(i for i in component.instances.values() if i.graph.index == 0x1A)
                ammo = pistol.vars[53]
                run.step({player: (16384, 0, FIRE)})
                self.assertEqual(ammo.value(), 38)
                for _ in range(195):
                    run.step({})
                start = tuple(player.mover.position)

                player.mover.yaw, player.mover.pitch = yaw, pitch
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(200):
                    run.step({})
                    if health.total < 150:
                        break
                self.assertLess(health.total, 150)
                self.assertFalse(health.dead)
                self.assertEqual(npc_control.stop(run.match), 1)
                run.step({})
                self.assertFalse(fight.is_stunned(player))

                run.step({player: (16384, 0, 8)})
                self.assertAlmostEqual(player.mover.position[0] - start[0], 7.5, places=2)
                run.step({player: (0, 0, 16)})
                self.assertTrue(tracer.recalling(player))
                for _ in range(100):
                    if not tracer.recalling(player):
                        break
                    run.step({})
                self.assertFalse(tracer.recalling(player))
                self.assertEqual(tuple(player.mover.position), start)
                self.assertEqual(player.mover.history[-1].position, start)
                self.assertEqual(health.total, 150)
                self.assertEqual(ammo.value(), 40)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                self.assertGreater(recall.vars[61].value(), 11.9)
                run.step({player: (0, 0, 16)})
                self.assertFalse(tracer.recalling(player))

                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = bot.position
                facing = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*facing, FIRE)})
                self.assertEqual(ammo.value(), 38)
                self.assertEqual(vitals.total, before - 12)
                self.check_second_recall_after_npc_attack(run, player, bot, health, recall, ammo, start)

    def check_second_recall_after_npc_attack(self, run, player, bot, health, recall, ammo, first_start):
        fight = run.match.combat
        cooldown = recall.vars[61]
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        run.step({player: (16384, 0, 8)})
        start = tuple(player.mover.position)
        self.assertAlmostEqual(start[0] - first_start[0], 7.5, places=2)
        x, y, z = start
        bot.position = (x, y, z + 5)
        # Allow the Sniper's first round while keeping it inside the lookback.
        for _ in range(750):
            if cooldown.value() <= 2.7:
                break
            run.step({})
        self.assertTrue(2.6 < cooldown.value() <= 2.7)
        self.assertEqual(health.total, 150)
        self.assertEqual(ammo.value(), 38)

        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (x, y + 1, z + 5))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(170):
            run.step({})
            if health.total < 150:
                break
        wounded = health.total
        self.assertTrue(0 < wounded < 150)
        self.assertIsNone(bot.body_script.component.fault)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        run.step({player: (16384, 0, 8)})
        displaced = tuple(player.mover.position)
        self.assertAlmostEqual(displaced[0] - start[0], 7.5, places=2)

        warmup = recall.state(recall.graph.node(4).state)
        activation = warmup.counter
        run.step({player: (0, 0, 16)})
        self.assertGreater(cooldown.value(), 0)
        self.assertEqual(warmup.counter, activation)
        for _ in range(170):
            run.step({})
            if cooldown.value() == 0:
                break
        self.assertEqual(cooldown.value(), 0)
        self.assertEqual(warmup.counter, activation)
        self.assertEqual(health.total, wounded)
        self.assertEqual(ammo.value(), 38)
        self.assertEqual(tuple(player.mover.position), displaced)

        run.step({player: (0, 0, 16)})
        self.assertTrue(tracer.recalling(player))
        self.assertEqual(warmup.counter, activation + 1)
        for _ in range(100):
            if not tracer.recalling(player):
                break
            run.step({})
        self.assertFalse(tracer.recalling(player))
        self.assertEqual(tuple(player.mover.position), start)
        self.assertEqual(player.mover.history[-1].position, start)
        self.assertEqual(health.total, 150)
        self.assertEqual(ammo.value(), 40)
        self.assertEqual(charge.value(), 12)
        self.assertGreater(cooldown.value(), 11.9)

    def test_recall_protects_from_five_actual_npc_attacks_then_damage_resumes(self):
        first_frames = {"Trooper": 29, "Enforcer": 67, "Sniper": 128, "Assassin": 10, "Heavy Assault": 32}
        for definition in self.definitions.values():
            if definition.name not in first_frames:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.engage(definition)
                for _ in range(first_frames[definition.name] - 10):
                    run.step({})
                run.step({player: (0, 0, 16)})
                self.assertTrue(tracer.recalling(player))
                protected_frames = 0
                for _ in range(100):
                    if not tracer.recalling(player):
                        break
                    self.assertEqual(vitals.total, 150)
                    run.step({})
                    protected_frames += 1
                self.assertGreater(protected_frames, 60)
                self.assertFalse(tracer.recalling(player))
                for _ in range(500):
                    run.step({})
                    if vitals.total < 150:
                        break
                self.assertLess(vitals.total, 150)
                self.assertIsNotNone(bot.body_script)
                npc_control.stop(run.match)

    def test_recall_at_first_npc_impact_boundary(self):
        for name, impact, damage in self.FIRST_PRIMARY_IMPACTS:
            for early in (False, True):
                for lethal in (False, True):
                    with self.subTest(npc=name, early=early, lethal=lethal):
                        self.check_npc_attack_recall_boundary(name, impact, damage, early, lethal)

    def check_npc_attack_recall_boundary(self, name, impact, damage, early, lethal):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, health = self.engage(definition)
        fight = run.match.combat
        recall = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1A)
        first_frame = run.frame + impact
        while run.frame < first_frame - (2 if early else 1):
            run.step({})
            self.assertEqual(health.total, 150)
        if lethal:
            health.damage(150 - damage)
            fight._sync_body_health(player, health)
        before = health.total
        if name in ("Enforcer", "Sniper"):
            first, = (shot for shot in fight.rockets if shot.shooter is bot)
        else:
            first = None
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rays:
            run.step({player: (0, 0, 16)})
            if early:
                self.assertTrue(tracer.recalling(player))
                run.step({})
        self.assertEqual(run.frame, first_frame)
        self.assertEqual(health.total, before if early else before - damage)
        if name in ("Trooper", "Heavy Assault"):
            self.assertEqual(rays.call_count, 1 if name == "Trooper" else 2)
            self.assertTrue(all(ray.args[0] is bot for ray in rays.call_args_list))

        if lethal and not early:
            self.assertTrue(health.dead)
            self.assertEqual((health.death_tick, health.killer), (first_frame, bot.entity))
            self.assertFalse(tracer.recalling(player))
            self.assertNotIn(bot.entity, fight.pins)
            for _ in range(100):
                run.step({})
            self.assertTrue(health.dead)
            self.assertEqual(player.stats.value(stats.DEATHS), 1)
            self.assertEqual(len(player.client.messages(combat.DEATH_NOTICE)), 1)
            self.assertFalse(tracer.recalling(player))
            return

        if name == "Assassin":
            if early:
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(bot.attack_input)
            else:
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertFalse(tracer.recalling(player))
                self.assertEqual(recall.vars[61].value(), 0)
                self.assertEqual(npc_control.stop(run.match), 1)
                run.step({})
                run.step({player: (0, 0, 16)})
        self.assertTrue(tracer.recalling(player))
        protected = health.total
        phases = set()
        for _ in range(100):
            if not tracer.recalling(player):
                break
            phases.add(player.body_script.world.recall_session.state.node.pos)
            self.assertEqual(health.total, protected)
            run.step({})
        self.assertFalse(tracer.recalling(player))
        self.assertEqual(phases, {4, 1})
        self.assertEqual(health.total, 150)
        self.assertGreater(recall.vars[61].value(), 11.9)
        if first is not None:
            self.assertTrue(first not in fight.rockets or first.position[2] < player.mover.position[2])

    def test_enforcer_actual_fire_stops_on_death_and_retains_its_corpse(self):
        definition = next(d for d in self.definitions.values() if d.name == "Enforcer")
        run, player, bot, health = self.engage(definition)
        for _ in range(100):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 148)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        vitals = run.match.combat.vitals_of(target)
        vitals.now.update({combat.HEALTH_POOL: 11, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
        yaw, pitch = aim(run.match.combat._eye(player),
                         (bot.position[0], bot.position[1] + 1, bot.position[2]))
        run.step({player: (yaw, pitch, FIRE)})
        self.assertTrue(bot.dead)
        at_death = health.total
        for _ in range(250):
            run.step({})
        self.assertEqual(health.total, at_death)
        self.assertIn(bot, run.match.bots)
        self.assertTrue(bot.body_script.component.owner_dead)
        self.assertFalse(bot.body_script.component.shots)

    def test_all_five_npc_attacks_are_interrupted_by_actual_flashbang_and_resume(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_flashbang_interrupt(definition)

    def check_npc_flashbang_interrupt(self, definition):
        run, player, bot, health = self.engage(definition)
        fight = run.match.combat
        if definition.name == "Assassin":
            # Flashbang an active pin. The Assassin approaches before pinning,
            # so its spawn point is already stale by grenade contact.
            for _ in range(60):
                run.step({})
                if fight.pins.get(bot.entity) == player.body:
                    break
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            self.assertEqual(health.total, 125)
        caster = next(p for p in run.players if p is not player)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.team = player.team
        x, y, z = bot.position
        caster.mover.position[:] = (x - 3, y, z)
        caster.mover.body = None
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(50):
            if fight.is_stunned(bot):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(bot))
        during = health.total
        if definition.name == "Assassin":
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(during, 125)
        else:
            self.assertEqual(during, 150)
        self.assertTrue(bot.firing)
        deadline = fight.stuns[bot.entity]
        primary = npc_control.button(bot)
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(health.total, during)
            self.assertFalse(bot.attack_input)
            self.assertNotIn(primary, bot.body_script.component.held)
        for _ in range(200):
            run.step({})
            if health.total < during:
                break
        self.assertFalse(fight.is_stunned(bot))
        self.assertLess(health.total, during)
        if definition.name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertTrue(bot.attack_input)
        self.assertIsNone(bot.body_script.component.fault)

    def test_launched_npc_round_hits_tracer_while_flashbang_stuns_shooter(self):
        for name, damage, lethal in (("Enforcer", 2, False), ("Sniper", 100, False), ("Sniper", 100, True)):
            with self.subTest(npc=name, lethal=lethal):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, player, bot, target, _, _ = self.fight(definition, distance=24)
                fight = run.match.combat
                self.start_npc_at_tracer(run, player, bot)
                caster = run.players[1]
                self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
                caster.team = player.team
                x, y, z = bot.position
                caster.mover.position[:] = (x - 3, y, z)
                caster.mover.body = None
                aim_flashbang = aim(fight._eye(caster), (x, y + 1, z))
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                first = None
                for _ in range(150):
                    run.step({})
                    first = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                    if first is not None:
                        break
                self.assertIsNotNone(first)
                self.assertEqual(health.total, 150)
                if lethal:
                    health.damage(50)
                    fight._sync_body_health(player, health)
                before = health.total
                run.step({caster: (*aim_flashbang, 16)})
                for _ in range(25):
                    run.step({})
                    if fight.is_stunned(bot):
                        break
                self.assertTrue(fight.is_stunned(bot))
                self.assertIn(first, fight.rockets)
                self.assertEqual(health.total, before)
                self.assertGreater(fight.vitals_of(target).total, 0)
                released = tuple(rocket for rocket in fight.rockets if rocket.shooter is bot)
                while fight.is_stunned(bot, tick=run.frame + 1):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertTrue(all(
                        any(rocket is old for old in released)
                        for rocket in fight.rockets if rocket.shooter is bot
                    ))
                self.assertNotIn(first, fight.rockets)
                self.assertEqual(health.total, before - damage)
                self.assertEqual(health.dead, lethal)
                if lethal:
                    self.assertEqual(health.killer, bot.entity)

    def test_static_wall_blocks_both_attack_directions_for_all_five_classes(self):
        from test_game_mover import wall

        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, health = self.engage(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                bot_health = fight.vitals_of(target)
                before = bot_health.total
                script = player.body_script
                script.ult_seconds = 0
                angles = player.mover.yaw, player.mover.pitch
                previous = run.match.collision_world
                x, floor, z = player.mover.position
                face = z + 2.5
                # Span the whole floor so a pursuing Assassin cannot go around the cover.
                left, right = x - 20, x + 20
                near, far = z - 20, z + 20
                triangles = [
                    (left, floor, near, left, floor, far, right, floor, far),
                    (left, floor, near, right, floor, far, right, floor, near),
                ]
                cover = World(triangles + wall(left - 1, face, right + 1, face, floor, floor + 4))
                run.match.collision_world = cover
                for other in run.players:
                    other.mover.use(world=cover)
                self.assertIsNone(npc_control.selected(run.match, player))
                run.step({player: (*angles, FIRE)})
                for _ in range(200):
                    run.step({})
                self.assertEqual((health.total, bot_health.total), (150, before))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(bot.attack_input)

                run.match.collision_world = previous
                for other in run.players:
                    other.mover.use(world=previous)
                self.assertIs(npc_control.selected(run.match, player), bot)
                run.step({player: (*angles, FIRE)})
                self.assertLess(bot_health.total, before)
                self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(),
                                       before - bot_health.total)
                for _ in range(200):
                    if health.total < 150:
                        break
                    run.step({})
                self.assertLess(health.total, 150)
                self.assertTrue(bot.attack_input)
                self.assertIsNone(bot.body_script.component.fault)

    def test_actual_ice_wall_occludes_enforcer_selection_and_both_fire_directions(self):
        definition = next(d for d in self.definitions.values() if d.name == "Enforcer")
        run, player, bot, health = self.engage(definition)
        fight = run.match.combat
        mei, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E00000000000DD)
        mei.team = player.team
        mei.mover.position[0] += 1.5
        mei.mover.position[2] += 4
        mei.mover.body = None
        x, y, z = player.mover.position
        angles = aim(fight._eye(mei), (x, y, z + 2.5))
        for buttons in (16, 0, FIRE, 0):
            run.step({mei: (*angles, buttons)})
        self.assertEqual(len(fight.walls.pieces), 5)
        wall = list(fight.walls.pieces.values())[2]
        self.assertIsNone(npc_control.selected(run.match, player))
        target = next(t for t in fight._targets() if t.owner is bot)
        bot_health = fight.vitals_of(target)
        initial = bot_health.total
        run.step({player: (0, 0, FIRE)})
        for _ in range(150):
            run.step({})
        self.assertEqual((health.total, bot_health.total, wall.health), (150, initial, 400))
        self.assertFalse(bot.attack_input)

        run.step({mei: (*angles, 16)})
        self.assertFalse(fight.walls.pieces)
        self.assertIs(npc_control.selected(run.match, player), bot)
        run.step({player: (0, 0, FIRE)})
        self.assertEqual(bot_health.total, initial - 12)
        for _ in range(120):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 148)

    def test_melee_gates_and_reuse_against_all_five_actual_classes(self):
        for definition in self.definitions.values():
            for gate in ("aim", "range", "wall", "team"):
                with self.subTest(npc=definition.name, gate=gate):
                    self.check_npc_melee_gate(definition, gate)

    def check_npc_melee_gate(self, definition, gate):
        run, player, bot, target, yaw, pitch = self.fight(
            definition, allied=gate == "team", distance=5 if gate == "range" else 1.5,
        )
        match, fight = run.match, run.match.combat
        x, y, z = player.mover.position
        clear_world = match.collision_world
        if gate == "wall":
            floor = box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
            match.collision_world = World(floor + wall(x - 5, z + 0.7, x + 5, z + 0.7, y, y + 4))
            for actor in run.players:
                actor.mover.use(world=match.collision_world)
        player.body_script.ult_seconds = 0
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        vitals = fight.vitals_of(target)
        before = vitals.total

        def assert_swings(count):
            damage = 30 * count
            self.assertEqual(vitals.total, before - damage)
            self.assertEqual(len(player.client.messages(combat.HIT)), count)
            self.assertEqual(player.stats.value(stats.HERO_DAMAGE), damage)
            self.assertEqual(charge.value(), damage)

        blocked_yaw = -32768 if gate == "aim" else yaw
        for _ in range(40):
            run.step({player: (blocked_yaw, pitch, 0x800)})
        assert_swings(0)

        bot.position = (x, y, z + 1.5)
        bot.team = 1 - player.team
        if gate == "wall":
            match.collision_world = clear_world
            for actor in run.players:
                actor.mover.use(world=clear_world)
        clear = {player: (*aim(fight._eye(player), (x, y + 1, z + 1.5)), 0x800)}
        for _ in range(65):
            run.step(clear)
        assert_swings(0)
        tracer_tests.check_melee_reuse(run, clear, assert_swings)

    def stunned_npc(self, definition, *, distance=5, wounded=False):
        run, player, bot, target, yaw, pitch = self.fight(definition, distance=distance)
        caster = next(p for p in run.players if p is not player)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.team = player.team
        x, floor, z = player.mover.position
        caster.mover.position[:] = (x+1, floor, z)
        cyaw, cpitch = aim(run.match.combat._eye(caster),
                          (bot.position[0], bot.position[1]+1, bot.position[2]))
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        if wounded:
            # A wounded actual-class body, retaining its authored scripts/capsule.
            vitals.now.update({combat.HEALTH_POOL: 26, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
        before = vitals.total
        player.body_script.ult_seconds = 0
        run.step({caster: (cyaw, cpitch, 16)})
        for _ in range(50):
            if fight.is_stunned(bot):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual(vitals.total, before-25)
        self.assertIsNotNone(bot.body_script)
        self.assertIsNone(bot.body_script.component.fault)
        return run, player, bot, target, yaw, pitch, before

    def test_stunned_actual_npcs_remain_damageable_by_pistols_melee_and_pulse(self):
        for attack in ("primary", "melee", "pulse"):
            for definition in self.definitions.values():
                with self.subTest(attack=attack, npc=definition.name):
                    run, player, bot, target, yaw, pitch, before = self.stunned_npc(
                        definition, distance=1.5 if attack == "melee" else 5)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    if attack == "pulse":
                        script = player.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                    buttons = {"primary": FIRE, "melee": 0x800, "pulse": 32}[attack]
                    run.step({player: (yaw, pitch, buttons)})
                    self.assertTrue(fight.is_stunned(bot))
                    if attack == "pulse":
                        for _ in range(45):
                            run.step({})
                            if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == bot.entity:
                                break
                        self.assertEqual(fight.pulse_bombs.bombs[0].attached, bot.entity)
                        self.assertTrue(fight.is_stunned(bot))
                    for _ in range(120 if attack == "pulse" else 40):
                        run.step({})
                    damage = {"primary": 12, "melee": 30, "pulse": 355}[attack]
                    self.assertEqual(vitals.total, max(0, before-25-damage))
                    self.assertEqual(player.body_script.component.entity_var(342).value(),
                                     0 if attack == "pulse" else damage)

    def test_tracer_attacks_shorten_actual_npc_sleep_after_wake_grace(self):
        for definition in self.definitions.values():
            for attack, button, amount in (("primary", FIRE, 12), ("melee", 0x800, 30), ("pulse", 32, 5)):
                with self.subTest(npc=definition.name, attack=attack):
                    self.check_npc_sleep_followup(definition, attack, button, amount)

    def check_npc_sleep_followup(self, definition, attack, button, amount):
        run, player, bot, target, yaw, pitch = self.fight(definition, distance=1.5)
        fight = run.match.combat
        npc_health = fight.vitals_of(target)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        before = npc_health.total
        script = player.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        deadline = self.apply_npc_sleep_dart(run, player, bot, npc_health, offset=-4)
        self.assertEqual(health.total, 150)
        player.mover.yaw, player.mover.pitch = yaw, pitch
        self.assertEqual(npc_control.start(run.match, player), bot.entity)

        run.step({player: (yaw, pitch, FIRE)})
        self.assertEqual(npc_health.total, before - 17)
        self.assertEqual(npc_health.sleep_until, deadline)
        self.assertEqual(charge.value(), 12)
        while run.frame + 1 < npc_health.sleep_wake_after:
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertEqual(health.total, 150)
        if attack == "pulse":
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (yaw, pitch, button)})
        if attack == "pulse":
            for _ in range(45):
                if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == bot.entity:
                    break
                run.step({})
                self.assertFalse(bot.attack_input)
            bomb, = fight.pulse_bombs.bombs
            self.assertEqual(bomb.attached, bot.entity)
        self.assertEqual(npc_health.total, before - 17 - amount)
        shortened = npc_health.sleep_until
        self.assertEqual(shortened, fight.tick + combat.SLEEP_WAKE_TICKS)
        self.assertLess(shortened, deadline)
        self.assertEqual(charge.value(), 0 if attack == "pulse" else 12 + amount)
        while run.frame + 1 < shortened:
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertEqual(health.total, 150)
        run.step({})
        self.assertEqual(fight.tick, shortened)
        self.assertFalse(fight.is_stunned(bot))
        self.assertTrue(bot.attack_input)
        if attack == "pulse":
            self.assertIn(bomb, fight.pulse_bombs.bombs)
            self.assertFalse(npc_health.dead)
        else:
            for _ in range(170):
                if health.total < 150:
                    break
                run.step({})
            self.assertLess(health.total, 150)

    def apply_npc_sleep_dart(self, run, player, bot, health, *, offset):
        fight = run.match.combat
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = player.team
        x, y, z = bot.position
        ana.mover.position[:] = (x + offset, y, z)
        ana.mover.body = None
        dart = aim(fight._eye(ana), (x, y + 1, z))
        before = health.total
        run.step({ana: (*dart, 8)})
        for _ in range(55):
            if health.sleep_until:
                break
            run.step({ana: (*dart, 0)})
        self.assertEqual(health.sleep_until, fight.tick + combat.SLEEP_TICKS)
        self.assertEqual(health.total, before - 5)
        return health.sleep_until

    def test_pulse_blast_shortens_or_clears_sleep_on_all_five_actual_classes(self):
        for definition in self.definitions.values():
            for case in ("grace", "wake", "lethal"):
                with self.subTest(npc=definition.name, case=case):
                    self.check_npc_pulse_sleep_blast(definition, case)

    def check_npc_pulse_sleep_blast(self, definition, case):
        run, player, anchor, target, _, _ = self.fight(definition)
        fight = run.match.combat
        bomb = self.throw_pulse_for_npc_gates(run, player)
        self.assertEqual(bomb.attached, anchor.entity)
        x, y, z = bomb.position
        floor = player.mover.position[1]
        radius = target.radius
        dy = max(floor + radius - y, y - (floor + target.height - radius), 0)
        lateral = math.sqrt((3.875 + radius) ** 2 - dy ** 2)
        sleeper = npc.spawn(run.match, definition, (x + lateral, floor, z), team=anchor.team)
        health = fight.vitals[sleeper.entity]
        if case == "lethal":
            health.damage(health.total - 60)
        if case == "grace":
            for _ in range(45):
                if bomb.fuse_left <= 0.55:
                    break
                run.step({})
        deadline = self.apply_npc_sleep_dart(run, player, sleeper, health, offset=4)
        wake_after = health.sleep_wake_after
        before = health.total
        self.assertIn(bomb, fight.pulse_bombs.bombs)
        self.assertEqual(bomb.attached, anchor.entity)
        bubble_tests.TracerWinstonBarrierTests.move(player, (x + lateral + 1.75, floor, z))
        self.start_npc_at_tracer(run, player, sleeper)
        player_health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is player)
        while bomb in fight.pulse_bombs.bombs and run.frame < deadline:
            self.assertEqual(health.total, before)
            self.assertEqual(health.sleep_until, deadline)
            self.assertFalse(sleeper.attack_input)
            run.step({})
        self.assertNotIn(bomb, fight.pulse_bombs.bombs)
        if case == "grace":
            self.assertLess(fight.tick, wake_after)
        else:
            self.assertGreaterEqual(fight.tick, wake_after)
        self.assertLess(fight.tick, deadline)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(player_health.total, 150)
        hits = [n["+0x78"] for n in player.client.messages(combat.HIT)
                if n["+0x78"]["+0x0"]["+0x0"] == sleeper.entity]
        hit, = hits
        lethal = case == "lethal"
        self.assertEqual(hit["+0x8"], combat.KILLING if lethal else 0)
        self.assertEqual(health.dead, lethal)
        if lethal:
            self.assertEqual(health.killer, player.body)
            self.assertEqual((health.sleep_until, health.sleep_wake_after), (0, 0))
            self.assertFalse(sleeper.attack_input)
            return
        self.assertAlmostEqual(before - health.total, 87.5, places=5)
        expected_wake = deadline if case == "grace" else fight.tick + combat.SLEEP_WAKE_TICKS
        self.assertEqual(health.sleep_until, expected_wake)
        while run.frame + 1 < expected_wake:
            run.step({})
            self.assertFalse(sleeper.attack_input)
            self.assertEqual(player_health.total, 150)
        run.step({})
        self.assertTrue(sleeper.attack_input)
        for _ in range(170):
            if player_health.total < 150:
                break
            run.step({})
        self.assertLess(player_health.total, 150)

    def test_killing_each_stunned_actual_npc_clears_control_and_keeps_its_corpse(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch, _ = self.stunned_npc(definition, wounded=True)
                self.assertEqual(run.match.combat.vitals_of(target).total, 1)
                run.step({player: (yaw, pitch, FIRE)})
                self.assertTrue(bot.dead)
                self.assertNotIn(bot.entity, run.match.combat.stuns)
                self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertTrue(bot.dead)
                self.assertNotIn(bot.entity, run.match.combat.stuns)
                self.assertFalse(any(owner is bot for _, owner in run.match.combat.respawns))

    def test_pulse_bomb_deals_contact_and_blast_to_all_five_base_pools(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _, _ = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                pulse = run.match.combat.pulse_bombs
                run.step({player: (0, 0, 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(pulse.bombs)
                for _ in range(55):
                    run.step({})
                    if pulse.bombs and pulse.bombs[0].stuck:
                        break
                bomb, = pulse.bombs
                self.assertEqual(bomb.attached, bot.entity)
                self.assertEqual(vitals.total, before - 5)
                self.assertGreater(bomb.fuse_left, 0.98)
                for _ in range(75):
                    run.step({})
                    if not pulse.bombs:
                        break
                self.assertFalse(pulse.bombs)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(vitals.total, max(0, before - 355))
                self.assertEqual(bot.dead, before <= 355)

    def test_pulse_contact_rejects_static_cover_and_allies_for_each_npc(self):
        for definition in self.definitions.values():
            for gate in ("wall", "ally"):
                with self.subTest(npc=definition.name, gate=gate):
                    run, player, _, target, _, _ = self.fight(definition, allied=gate == "ally")
                    health = run.match.combat.vitals_of(target)
                    before = health.total
                    x, y, z = player.mover.position
                    if gate == "wall":
                        self.mount_pulse_wall(run, x, y, z + 3)
                    bomb = self.throw_pulse_for_npc_gates(run, player)
                    self.assertIsNone(bomb.attached)
                    if gate == "wall":
                        self.assertLess(bomb.position[2], z + 3)
                    self.assertEqual(health.total, before)
                    self.finish_pulse_for_npc_gates(run, player)
                    self.assertEqual(health.total, before)
                    self.assertFalse(player.client.messages(combat.HIT))

    def test_pulse_blast_checks_capsule_surface_cover_and_team_for_each_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_pulse_blast_gates(definition)

    def check_npc_pulse_blast_gates(self, definition):
        run, player, anchor, target, _, _ = self.fight(definition)
        fight = run.match.combat
        anchor_health = fight.vitals_of(target)
        base = anchor_health.total
        bomb = self.throw_pulse_for_npc_gates(run, player)
        self.assertEqual(bomb.attached, anchor.entity)
        self.assertEqual(anchor_health.total, base - 5)
        x, y, z = bomb.position
        floor = player.mover.position[1]
        radius = target.radius
        dy = max(floor + radius - y, y - (floor + target.height - radius), 0)
        cases = (
            ("falloff", 3.25, 1, 0, 175),
            ("edge", 3.875, -1, 0, 87.5),
            ("outside", 4.125, 0, 1, 0),
            ("covered", 3.25, 0, -1, 0),
            ("ally", 3.25, 0.6, 0.8, 0),
        )
        targets = {}
        for gate, distance, dx, dz, damage in cases:
            horizontal = math.sqrt((distance + radius) ** 2 - dy ** 2)
            position = (x + horizontal * dx, floor, z + horizontal * dz)
            team = player.team if gate == "ally" else anchor.team
            bot = npc.spawn(run.match, definition, position, team=team)
            body = next(t for t in fight._targets() if t.owner is bot)
            targets[gate] = bot.entity, fight.vitals_of(body), damage
        self.mount_pulse_wall(run, x, floor, z - 1.25, half_width=1.25)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
        self.finish_pulse_for_npc_gates(run, player)
        self.assertAlmostEqual(anchor_health.total, max(0, base - 355))
        hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
        for gate, (entity, health, damage) in targets.items():
            with self.subTest(blast_gate=gate):
                self.assertAlmostEqual(health.total, base - damage, delta=0.00001 if damage else 0)
                self.assertEqual(sum(hit["+0x0"]["+0x0"] == entity for hit in hits), int(damage > 0))
        self.assertEqual(len(hits), 4)
        resolved = base - anchor_health.total + sum(base - health.total for _, health, _ in targets.values())
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), resolved)

    def mount_pulse_wall(self, run, x, floor, z, *, half_width=5):
        ground = box(x - 20, x + 20, floor - 1, floor, z - 20, z + 20)
        mesh = World(ground + wall(x - half_width, z, x + half_width, z, floor, floor + 4))
        run.match.collision_world = mesh
        for actor in run.players:
            actor.mover.use(world=mesh)

    def throw_pulse_for_npc_gates(self, run, player):
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        bombs = run.match.combat.pulse_bombs.bombs
        run.step({player: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(bombs)
        for _ in range(80):
            run.step({})
            if bombs and bombs[0].stuck:
                break
        bomb, = bombs
        self.assertTrue(bomb.stuck)
        self.assertGreater(bomb.fuse_left, 0.98)
        return bomb

    def finish_pulse_for_npc_gates(self, run, player):
        bombs = run.match.combat.pulse_bombs.bombs
        for _ in range(75):
            run.step({})
            if not bombs:
                break
        self.assertFalse(bombs)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)


if __name__ == "__main__":
    unittest.main()
