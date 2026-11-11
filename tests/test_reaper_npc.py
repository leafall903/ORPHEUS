"""Actual five-body subset, requiring an explicit read-only external NPC pack.

Set ORPHEUS_NPC_RUNTIME_DIR to the sparse prepared pack. No catalog, game asset
or absolute machine path is embedded here. Permissive body VM and fixture
transport are explicit; these tests do not establish native enemy AI behavior.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_combat as combat_tests
import test_game_reaper as reaper_tests
import test_reaper_shadow as shadow_tests

from ow174.game import (
    ana,
    combat,
    graviton,
    mei,
    mercy_beam,
    npc,
    npc_context,
    npc_control,
    observers,
    orisa_halt,
    pools,
    reaper,
    sigma_flux,
    sigma_grasp,
    stats,
    winston_primal,
    zarya_barriers,
)
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.mover import loaded_mover_table, mover_data
from ow174.game.script import expr, graph, runtime
from ow174.game.script.decode import read_chunk
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, BodyStream
from ow174.game.statescript import chunk

PACK = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")


class NpcClient(combat_tests.FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def queue_bot_death(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def bot_stream(self, entity):
        return None  # no modeled projectile observer transport; authoritative damage still runs


@unittest.skipUnless(PACK, "set ORPHEUS_NPC_RUNTIME_DIR for actual five-NPC acceptance")
class ReaperNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(PACK) / "data"
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        for definition in cls.catalog.values():
            mover_data(definition.body)
            if definition.body not in loaded_mover_table():
                raise RuntimeError("Actual NPC mover row missing; use the prepared43-row navigation runtime")
        cls.original_graphs = graph.GRAPHS_PATH
        graph.GRAPHS_PATH = folder / "game_graphs_174.json.gz"
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.original_graphs
        cls.clear_graphs()

    def scene(self, definition, *, team_mode=False, autonomous=False, bot_team=1, names=("Reaper", "Target")):
        run, owner, victim, _ = reaper_tests.ReaperTests().scenario(team_mode=team_mode, names=names)
        victim.spawned = False
        owner.client = NpcClient(owner)
        victim.client = NpcClient(victim)
        bot = npc.spawn(run.match, definition, victim.mover.position, team=bot_team, autonomous=autonomous)
        self.assertIsNotNone(bot.body_script)
        self.assertFalse(bot.body_script.component.strict)
        for _ in range(2):
            run.step({})
        return run, owner, victim, bot

    def halt_npc_scene(self, key):
        if not orisa_halt.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Halt supplement")
        run, owner, orisa, bot = self.scene(self.catalog[key], team_mode=True)
        orisa.spawned = True
        owner.team = orisa.team = 0
        self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        orisa.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x + 4, y, z + 6)
        orisa.mover.body = owner.mover.body = None
        bot.position = (x + 1, y, z + 10)
        for _ in range(25):
            run.step({})
        return run, owner, orisa, bot

    def cast_flux_toward_talon(self, key, *, projected=False):
        names = ("Reaper", "Sigma", "Zarya") if projected else ("Reaper", "Sigma")
        run, owner, sigma, bot = self.scene(self.catalog[key], team_mode=True, names=names)
        sigma.spawned = True
        owner.team = sigma.team = 0
        self.assertTrue(run.match.switch_hero(sigma, find_hero("Sigma")))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        sigma.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x + 2.5, y, z + 5)
        owner.mover.body = sigma.mover.body = None
        bot.position = (x, y, z + 6)
        if projected:
            zarya = run.players[2]
            self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
            zarya.team = bot.team
            zarya.mover.position[:] = (x + 10, y, z + 6)
            zarya.mover.body = None
        fight = run.match.combat
        script = sigma.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(81):
            yaw, pitch = combat_tests.aim(fight._eye(sigma), bot.position)
            run.step({sigma: (yaw, pitch, 32 if frame == 0 else 1 if frame == 80 else 0)})
        instance = next(i for i in script.component.instances.values() if i.graph.index == sigma_flux.FLUX)
        self.assertEqual(instance.vars[59].value(), 2)
        return run, owner, sigma, bot, instance

    def whole_hog_talon_scene(self, key, *, extra=()):
        names = ("Reaper", "Roadhog", *extra)
        run, owner, hog, bot = self.scene(self.catalog[key], team_mode=True, names=names)
        hog.spawned = True
        owner.team = hog.team = 0
        self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        hog.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x + 2, y, z + 2.5)
        hog.mover.body = owner.mover.body = None
        bot.position = (x, y, z + 2.5)
        for _ in range(4):
            run.step({})
        return run, owner, hog, bot

    def winston_talon_scene(self, key):
        run, owner, winston, bot = self.scene(
            self.catalog[key], team_mode=True, names=("Reaper", "Winston"),
        )
        winston.spawned = True
        owner.team = winston.team = 0
        self.assertTrue(run.match.switch_hero(winston, find_hero("Winston")))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        winston.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x + 2, y, z + 1.5)
        winston.mover.body = owner.mover.body = None
        bot.position = (x, y, z + 1.5)
        for _ in range(4):
            run.step({})
        return run, owner, winston, bot

    def test_actual_tesla_hits_all_five_talon_bodies_and_reaper_follows_up(self):
        for key in ("1", "2", "3", "4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, winston, bot = self.winston_talon_scene(key)
                fight = run.match.combat
                health = fight.vitals[bot.entity]
                total = health.total
                script = winston.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                for _ in range(25):
                    run.step({winston: (0, 0, 1)})
                    if health.total < total:
                        break
                lost = total - health.total
                self.assertGreater(lost, 0)
                self.assertLessEqual(lost, 0.960001)
                self.assertNotIn(bot.entity, fight.npc_impulses)
                self.assertGreater(script.component.entity_var(ULT_CHARGE).value(), 0)
                x, y, z = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                before_shell = health.total
                owner.take_commands([combat_tests.Command(owner.body_script.frame + 1, yaw=yaw,
                                                           pitch=pitch, buttons=1)])
                self.assertLess(health.total, before_shell)

    def test_actual_winston_jump_lands_on_all_five_talon_bodies_then_reaper_hits(self):
        for key in ("1", "2", "3", "4", "5"):
            outcomes = {}
            for skip in (False, True):
                with self.subTest(npc=self.catalog[key].name, skip=skip):
                    run, owner, winston, bot = self.winston_talon_scene(key)
                    x, y, z = winston.mover.position
                    radius = mover_data(bot.body).stand_radius
                    separation = math.sqrt((4.25 + radius) ** 2 - radius ** 2)
                    bot.position = x, y, z + separation
                    fight = run.match.combat
                    health = fight.vitals[bot.entity]
                    total = health.total
                    run.step({winston: (0, -16384, 8)})
                    self.assertIn(winston.body, fight.winston_jumps)
                    self.assertEqual(health.total, total)
                    before = bot.position
                    self.advance_halt_world(run, 235, skip=skip)
                    self.assertNotIn(winston.body, fight.winston_jumps)
                    self.assertNotIn(bot.entity, fight.npc_impulses)
                    self.assertLess(health.total, total)
                    self.assertGreater(bot.position[2], before[2])
                    self.assertAlmostEqual(bot.position[1], y, delta=0.02)
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    bx, by, bz = bot.position
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                    before_shell = health.total
                    owner.take_commands([combat_tests.Command(owner.body_script.frame + 1, yaw=yaw,
                                                               pitch=pitch, buttons=1)])
                    self.assertLess(health.total, before_shell)
                    outcomes[skip] = (bot.position, bot.velocity, health.total)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[False], outcomes[True])

    def test_actual_primal_moves_all_five_talon_bodies_and_reaper_follows_up(self):
        for key in ("1", "2", "3", "4", "5"):
            outcomes = {}
            for skip in (False, True):
                with self.subTest(npc=self.catalog[key].name, skip=skip):
                    run, owner, winston, bot = self.winston_talon_scene(key)
                    before = bot.position
                    y = before[1]
                    fight = run.match.combat
                    health = fight.vitals[bot.entity]
                    total = health.total
                    script = winston.body_script
                    script.set_ult_charge(1540)
                    run.step({winston: (0, 0, 32)})
                    self.assertIsNotNone(winston_primal.pool(script.component))
                    for _ in range(70):
                        run.step({winston: (0, 0, 1)})
                        if bot.entity in fight.npc_impulses:
                            break
                    self.assertIn(bot.entity, fight.npc_impulses)
                    self.assertLess(health.total, total)
                    self.advance_halt_world(run, 85, skip=skip)
                    self.assertNotIn(bot.entity, fight.npc_impulses)
                    self.assertGreater(bot.position[2], before[2] + 0.2)
                    self.assertAlmostEqual(bot.position[1], y, delta=0.02)
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    bx, by, bz = bot.position
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                    before_shell = health.total
                    owner.take_commands([combat_tests.Command(owner.body_script.frame + 1, yaw=yaw,
                                                               pitch=pitch, buttons=1)])
                    self.assertLess(health.total, before_shell)
                    outcomes[skip] = (bot.position, bot.velocity, health.total)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[False], outcomes[True])

    def test_actual_whole_hog_moves_all_five_talon_bodies_and_reaper_follows_up(self):
        for key in ("1", "2", "3", "4", "5"):
            outcomes = []
            for skip in (False, True):
                with self.subTest(npc=self.catalog[key].name, skip=skip):
                    run, owner, hog, bot = self.whole_hog_talon_scene(key)
                    before = bot.position
                    x, y, z = before
                    fight = run.match.combat
                    health = fight.vitals[bot.entity]
                    total = health.total
                    hog.body_script.set_ult_charge(2240)
                    angles = combat_tests.aim(fight._eye(hog), (x, y + 0.9, z))
                    for frame in range(85):
                        run.step({hog: (*angles, 32 if frame == 0 else 0)})
                        if bot.entity in fight.npc_impulses:
                            break
                    self.assertIn(bot.entity, fight.npc_impulses)
                    self.assertLess(health.total, total)
                    self.advance_halt_world(run, 85, skip=skip)
                    self.assertNotIn(bot.entity, fight.npc_impulses)
                    self.assertGreater(bot.position[2], before[2] + 0.2)
                    self.assertAlmostEqual(bot.position[1], y, delta=0.02)
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    bx, by, bz = bot.position
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                    before_shell = health.total
                    owner.take_commands([combat_tests.Command(owner.body_script.frame + 1, yaw=yaw,
                                                               pitch=pitch, buttons=1)])
                    self.assertLess(health.total, before_shell)
                    outcomes.append((bot.position, bot.velocity, health.total))
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_whole_hog_pushes_all_five_talon_bodies_at_the_immortality_floor(self):
        for key in ("1", "2", "3", "4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, _, hog, bot = self.whole_hog_talon_scene(key, extra=("Baptiste",))
                fight = run.match.combat
                bap = run.players[2]
                self.assertTrue(run.match.switch_hero(bap, find_hero("Baptiste")))
                bap.team = bot.team
                x, y, z = bot.position
                bap.mover.position[:] = (x + 3, y, z + 0.5)
                bap.mover.body = None
                aim_lamp = combat_tests.aim(fight._eye(bap), (x + 3, y, z))
                run.step({bap: (*aim_lamp, 16)})
                for _ in range(90):
                    run.step({})
                    if bap.body in fight.baptiste_fields.devices:
                        break
                device = fight.baptiste_fields.devices[bap.body]
                target = next(t for t in fight._targets() if t.owner is bot)
                health = fight.vitals_of(target)
                floor = fight.baptiste_fields.health_floor(target, health)
                self.assertGreater(floor, 0)
                health.damage(health.total - floor)
                angles = combat_tests.aim(fight._eye(hog), (x, y + 0.9, z))
                hog.body_script.set_ult_charge(2240)
                for frame in range(80):
                    run.step({hog: (*angles, 32 if frame == 0 else 0)})
                    if bot.entity in fight.npc_impulses:
                        break
                self.assertIn(bot.entity, fight.npc_impulses)
                self.assertAlmostEqual(health.total, floor)
                self.assertFalse(hog.client.messages(combat.HIT))
                before = bot.position
                self.advance_halt_world(run, 40, skip=True)
                self.assertGreater(bot.position[2], before[2] + 0.2)
                self.assertAlmostEqual(health.total, floor)
                self.assertEqual(device.health, 150)

    def test_actual_flux_uses_each_talon_receiver_and_reaper_hits_landed_pose(self):
        for key in ("1", "2", "3", "4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, sigma, bot, instance = self.cast_flux_toward_talon(key)
                fight = run.match.combat
                before = bot.position
                health = fight.vitals[bot.entity]
                total = health.total
                peak = before[1]
                for _ in range(165):
                    run.step({})
                    peak = max(peak, bot.position[1])
                self.assertEqual(instance.vars[375].value(), (expr.Entity(bot.entity),))
                self.assertEqual(health.total, total - 50)
                if key == "5":
                    self.assertEqual(peak, before[1])
                else:
                    self.assertGreater(peak, before[1] + 5)
                    self.assertTrue(sigma_flux.receiving(bot))
                    self.assertGreater(bot.movement(run.frame).air_ticks, 0)
                for _ in range(230):
                    run.step({})
                self.assertFalse(sigma_flux.receiving(bot))
                self.assertAlmostEqual(bot.position[1], before[1], delta=0.01)
                self.assertEqual(bot.velocity, (0, 0, 0))
                expected = total - 50 - (0 if key == "5" else health.maximum / 2)
                self.assertEqual(health.total, expected)
                self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                self.assertEqual(sigma.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                x, y, z = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 1)})
                self.assertLess(health.total, expected)

    def test_actual_flux_talon_landing_matches_skipped_world_ticks(self):
        for key in ("1", "2", "3", "4", "5"):
            results = []
            for skip in (False, True):
                with self.subTest(npc=self.catalog[key].name, skip=skip):
                    run, _, _, bot, _ = self.cast_flux_toward_talon(key)
                    fight = run.match.combat
                    self.advance_halt_world(run, 395, skip=skip)
                    self.assertFalse(sigma_flux.receiving(bot))
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    results.append((bot.position, bot.velocity, fight.vitals[bot.entity].total))
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0], results[1])

    def test_actual_flux_resolves_projected_barrier_for_all_five_talon_bodies(self):
        for key in ("1", "2", "3", "4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, _, _, bot, _ = self.cast_flux_toward_talon(key, projected=True)
                fight = run.match.combat
                zarya = run.players[2]
                health = fight.vitals[bot.entity]
                total = health.total
                if key == "5":
                    self.advance_halt_world(run, 40, skip=False)
                    damage = 50
                else:
                    for _ in range(280):
                        run.step({})
                        if bot.velocity[1] < -1 and 3 < bot.position[1] < 8:
                            break
                    self.assertLess(bot.velocity[1], -1)
                    total -= 50
                    damage = health.maximum / 2
                self.assertEqual(health.total, total)
                x, y, z = bot.position
                angles = combat_tests.aim(fight._eye(zarya), (x, y + 1, z))
                run.step({zarya: (*angles, 16)})
                bubble = fight.zarya_bubbles.for_target(bot)
                self.assertIsNotNone(bubble)
                for _ in range(65):
                    run.step({})
                    if bubble.health < 200:
                        break
                self.assertEqual(bubble.health, max(0, 200 - damage))
                self.assertEqual(health.total, total)

    def test_actual_reaper_shell_kills_a_lifted_trooper_and_retires_flux(self):
        run, owner, _, bot, _ = self.cast_flux_toward_talon("1")
        fight = run.match.combat
        for _ in range(115):
            run.step({})
            if bot.position[1] > 2:
                break
        self.assertTrue(sigma_flux.receiving(bot))
        self.assertGreater(bot.position[1], 2)
        health = fight.vitals[bot.entity]
        health.damage(health.total - 25)
        x, y, z = bot.position
        angles = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (*angles, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(health.killer, owner.body)
        self.assertTrue(owner.client.deaths)
        self.assertFalse(any(i.graph.index == sigma_flux.FLUX_EFFECT
                             for i in bot.body_script.component.instances.values()))
        terminal = bot.position
        self.advance_halt_world(run, 100, skip=True)
        self.assertEqual(bot.position, terminal)

    def test_actual_flux_entry_and_slam_kills_retain_one_talon_death(self):
        for slam in (False, True):
            with self.subTest(slam=slam):
                run, owner, sigma, bot, _ = self.cast_flux_toward_talon("1")
                fight = run.match.combat
                health = fight.vitals[bot.entity]
                health.damage(health.total - (100 if slam else 40))
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    for _ in range(400):
                        run.step({})
                        if bot.dead:
                            break
                self.assertTrue(bot.dead)
                self.assertEqual(health.killer, sigma.body)
                self.assertEqual(len(owner.client.deaths), 1)
                self.assertFalse(any(i.graph.index == sigma_flux.FLUX_EFFECT
                                     for i in bot.body_script.component.instances.values()))

    def test_actual_graviton_pulls_laterally_during_trooper_flux(self):
        run, _, sigma, bot, _ = self.cast_flux_toward_talon("1", projected=True)
        fight = run.match.combat
        zarya = run.players[2]
        zarya.team = sigma.team
        x, y, z = bot.position
        zarya.mover.position[:] = (x + 2, y, z - 3)
        zarya.mover.body = None
        self.advance_halt_world(run, 44, skip=False)
        zarya.body_script.set_ult_charge(2100)
        yaw, pitch = combat_tests.aim(fight._eye(zarya), (x + 2, y, z))
        run.step({zarya: (yaw, pitch, 32)})
        health = fight.vitals[bot.entity]
        peak = y
        moved_while_lifting = False
        slammed = False
        for _ in range(345):
            old_position, old_health = bot.position, health.total
            run.step({})
            peak = max(peak, bot.position[1])
            if sigma_flux.receiving(bot) and fight.gravitons.fields:
                moved_while_lifting |= bot.position[0] > old_position[0] and bot.position[1] > old_position[1]
            if old_health - health.total > 90:
                self.assertAlmostEqual(old_health - health.total, health.maximum / 2, delta=0.081)
                slammed = True
        self.assertTrue(moved_while_lifting)
        self.assertGreater(peak, y + 10)
        self.assertTrue(slammed)
        self.assertAlmostEqual(bot.position[1], y, delta=0.01)
        self.assertFalse(sigma_flux.receiving(bot))

    def flux_halt_talon(self, key):
        if not orisa_halt.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Halt supplement")
        run, _, sigma, bot, _ = self.cast_flux_toward_talon(key, projected=True)
        fight = run.match.combat
        orisa = run.players[2]
        self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
        orisa.team = sigma.team
        x, y, z = bot.position
        orisa.mover.position[:] = (x + 2, y, z - 10)
        orisa.mover.body = None
        for _ in range(9):
            run.step({})
        self.detonate_npc_halt(run, orisa)
        self.assertEqual(bot.entity in fight.halts.pulls, key != "5")
        return run, bot, fight

    def test_actual_halt_and_flux_use_each_talon_control_branch(self):
        for key in ("1", "2", "3", "4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, bot, fight = self.flux_halt_talon(key)
                y = bot.position[1]
                health = fight.vitals[bot.entity]
                total = health.total
                moved_while_lifting = False
                peak = y
                for _ in range(340):
                    bx, by, _ = bot.position
                    run.step({})
                    px, py, _ = bot.position
                    peak = max(peak, py)
                    if sigma_flux.receiving(bot) and bot.entity in fight.halts.pulls:
                        moved_while_lifting |= px > bx and py > by
                self.assertEqual(moved_while_lifting, key != "5")
                if key == "5":
                    self.assertEqual(peak, y)
                else:
                    self.assertGreater(peak, y + 5)
                self.assertAlmostEqual(bot.position[1], y, delta=0.01)
                self.assertFalse(sigma_flux.receiving(bot))
                self.assertNotIn(bot.entity, fight.halts.pulls)
                self.assertEqual(health.total, total - 50 - (0 if key == "5" else health.maximum / 2))

    def test_actual_halt_and_flux_trooper_pose_survives_skipped_ticks(self):
        poses = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, bot, fight = self.flux_halt_talon("1")
                before = bot.position
                health = fight.vitals[bot.entity]
                expected = health.total - 50 - health.maximum / 2
                self.advance_halt_world(run, 340, skip=skip)
                self.assertGreater(bot.position[0], before[0] + 0.5)
                self.assertAlmostEqual(bot.position[1], before[1], delta=0.01)
                self.assertFalse(sigma_flux.receiving(bot))
                self.assertNotIn(bot.entity, fight.halts.pulls)
                self.assertEqual(health.total, expected)
                self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                poses.append((bot.position, bot.velocity))
        self.assertEqual(len(poses), 2)
        self.assertEqual(poses[0], poses[1])

    def launch_graviton_toward_talon(self, key, *, ally=False, cover=False):
        run, owner, zarya, bot = self.scene(self.catalog[key], team_mode=True, bot_team=0 if ally else 1)
        zarya.spawned = True
        owner.team = zarya.team = 0
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        if cover:
            side = x + 1
            run.match.collision_world = World([
                (x - 80, y, z - 80, x - 80, y, z + 80, x + 80, y, z + 80),
                (x - 80, y, z - 80, x + 80, y, z + 80, x + 80, y, z - 80),
                (side, y, z - 4, side, y + 8, z - 4, side, y + 8, z + 12),
                (side, y, z - 4, side, y + 8, z + 12, side, y, z + 12),
            ], name="Graviton Talon cover")
        zarya.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x - 2, y, z + 5)
        zarya.mover.body = owner.mover.body = None
        bot.position = (x + 2.8, y, z + 5)
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        zarya.body_script.ult_seconds = 0
        zarya.body_script.set_ult_charge(2100)
        yaw, pitch = combat_tests.aim(fight._eye(zarya), (x, y, z + 5))
        run.step({zarya: (yaw, pitch, 32)})
        self.assertEqual(fight.rockets[0].graviton_body, zarya.body)
        return run, owner, zarya, bot

    def test_actual_graviton_pulls_and_damages_all_five_talon_bodies(self):
        for key in ("1", "2", "3", "4", "5"):
            outcomes = []
            for skip in (False, True):
                with self.subTest(npc=self.catalog[key].name, skip=skip):
                    run, owner, zarya, bot = self.launch_graviton_toward_talon(key)
                    fight = run.match.combat
                    health = fight.vitals[bot.entity]
                    original = bot.position
                    total = health.total
                    self.advance_halt_world(run, 310, skip=skip)
                    self.assertFalse(fight.gravitons.fields)
                    self.assertAlmostEqual(health.total, total - 4 * graviton.DAMAGE_RATE)
                    self.assertGreater(math.dist(bot.position, original), 1)
                    self.assertAlmostEqual(bot.position[1], original[1], delta=0.02)
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    self.assertEqual(zarya.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                    x, y, z = bot.position
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                    owner.take_commands([combat_tests.Command(owner.body_script.frame + 1, yaw=yaw,
                                                               pitch=pitch, buttons=1)])
                    self.assertLess(health.total, total - 4 * graviton.DAMAGE_RATE)
                    outcomes.append((bot.position, health.total))
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_graviton_skips_allied_and_occluded_talon(self):
        for case in ("ally", "cover"):
            with self.subTest(case=case):
                run, _, _, bot = self.launch_graviton_toward_talon(
                    "1", ally=case == "ally", cover=case == "cover",
                )
                fight = run.match.combat
                before = bot.position
                health = fight.vitals[bot.entity]
                total = health.total
                self.advance_halt_world(run, 70, skip=True)
                self.assertTrue(fight.gravitons.fields)
                self.assertEqual(bot.position, before)
                self.assertEqual(health.total, total)

    def test_actual_trooper_fires_during_graviton_then_reaper_kills_it(self):
        run, owner, _, bot = self.launch_graviton_toward_talon("1")
        fight = run.match.combat
        health = fight.vitals[bot.entity]
        original = bot.position
        self.advance_halt_world(run, 24, skip=False)
        self.assertTrue(fight.gravitons.fields)
        self.engage(run, owner, bot)
        for _ in range(55):
            if fight.vitals[owner.body].total < 250:
                break
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 230)
        self.assertGreater(math.dist(bot.position, original), 0.5)
        health.damage(health.total - 30)
        x, y, z = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (yaw, pitch, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(health.killer, owner.body)
        self.assertTrue(owner.client.deaths)
        dead_position = bot.position
        for _ in range(10):
            run.step({})
        self.assertEqual(bot.position, dead_position)

    def test_actual_graviton_retains_trooper_fire_across_missing_world_ticks(self):
        for autonomous in (False, True):
            outcomes = []
            for skip in (False, True):
                with self.subTest(autonomous=autonomous, skip=skip):
                    run, owner, _, bot = self.launch_graviton_toward_talon("1")
                    fight = run.match.combat
                    if autonomous:
                        bot.autonomous = True
                    else:
                        self.engage(run, owner, bot)
                    self.advance_halt_world(run, 72, skip=skip)
                    health = fight.vitals[owner.body].total
                    self.assertEqual(bot.attack_target, owner.entity)
                    self.assertTrue(fight.gravitons.fields)
                    self.assertLess(health, 250)
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    outcomes.append((bot.position, bot.velocity, health, fight.vitals[bot.entity].total))
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_graviton_stops_and_gates_heavy_charge(self):
        from ow174.game import npc_navigation

        for windup in (False, True):
            with self.subTest(windup=windup):
                run, owner, _, bot = self.launch_graviton_toward_talon("5")
                fight = run.match.combat
                if windup:
                    self.engage_heavy_charge(run, owner, bot)
                for _ in range(36):
                    if fight.gravitons.fields:
                        break
                    run.step({})
                field, = fight.gravitons.fields
                instance = npc_navigation.charge_instance(bot)
                if not windup:
                    self.engage_heavy_charge(run, owner, bot)
                else:
                    self.assertTrue(instance.states[71].active)
                while run.frame < field.until - 1:
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    state = instance.states.get(71)
                    self.assertFalse(state is not None and state.active)
                    self.assertIsNone(npc_navigation.charge_motion(bot))
                self.assertEqual(getattr(instance.states.get(71), "counter", 0), int(windup))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                run.step({})
                self.assertFalse(fight.gravitons.fields)
                self.assertTrue(bot.attack_input)
                if windup:
                    self.assertGreater(instance.states[41].remaining(), 10)
                else:
                    self.assertTrue(instance.states[71].active)

    def test_actual_graviton_releases_heavy_capture_without_impact_damage(self):
        from ow174.game import npc_navigation

        run, owner, mech, bot, instance, pool, z = self.heavy_charge_mech(backstop=False, support="Zarya")
        zarya = run.players[2]
        zarya.team = owner.team
        zarya.spawned = True
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, _ = mech.mover.position
        zarya.mover.position[:] = (x + 2.5, y, z - 2)
        zarya.mover.body = None
        fight = run.match.combat
        before = pool.total
        for _ in range(100):
            run.step({})
            if npc_navigation.charge_carries(run.match, mech.body):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual(pool.total, before)
        zarya.body_script.set_ult_charge(2100)
        yaw, pitch = combat_tests.aim(fight._eye(zarya), (x, y, z - 2))
        run.step({zarya: (yaw, pitch, 32)})
        for _ in range(35):
            run.step({})
            if fight.gravitons.fields and not npc_navigation.charge_carries(run.match, mech.body):
                break
        self.assertTrue(fight.gravitons.fields)
        self.assertFalse(fight.is_stunned(mech))
        self.assertIsNone(npc_navigation.charge_motion(bot))
        self.assertGreater(instance.states[41].remaining(), 14)
        for _ in range(25):
            run.step({})
        self.assertEqual(pool.total, before)
        impact = instance.states.get(11)
        self.assertFalse(impact and impact.counter)

    @staticmethod
    def detonate_npc_halt(run, orisa):
        for frame in range(26):
            run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})

    def test_actual_halt_moves_four_talon_bodies_and_heavy_rejects_it(self):
        for key in ("1", "2", "3", "4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, _, orisa, bot = self.halt_npc_scene(key)
                fight = run.match.combat
                original = bot.position
                hp = fight.vitals[bot.entity].total
                self.detonate_npc_halt(run, orisa)
                if key == "5":
                    self.assertTrue(bot.body_script.component.entity_var(6601).value())
                    self.assertNotIn(bot.entity, fight.halts.pulls)
                else:
                    pull = fight.halts.pulls[bot.entity]
                    self.assertEqual(pull.script.graph.index, 0xCCE)
                maximum_step = 0.0
                for frame in range(75):
                    previous = bot.position
                    run.step({})
                    maximum_step = max(maximum_step, math.dist(previous, bot.position))
                    if frame < 24:
                        self.assertEqual(bot.position, original)
                self.assertEqual(fight.vitals[bot.entity].total, hp)
                if key == "5":
                    self.assertEqual(bot.position, original)
                else:
                    self.assertGreater(math.dist(bot.position, original), 0.5)
                    self.assertLessEqual(maximum_step, 20 * combat.TICK + 0.003)
                for _ in range(65):
                    run.step({})
                self.assertNotIn(bot.entity, fight.halts.pulls)

    def test_actual_halt_slow_keeps_trooper_fire_and_reaper_kill_clears_it(self):
        run, owner, orisa, bot = self.halt_npc_scene("1")
        fight = run.match.combat
        self.detonate_npc_halt(run, orisa)
        pull = fight.halts.pulls[bot.entity]
        for _ in range(65):
            run.step({})
            if fight.halts.npc_speed_scale(bot.entity, run.frame) < 1:
                break
        self.assertAlmostEqual(fight.halts.npc_speed_scale(bot.entity, run.frame), 0.35, places=5)
        self.engage(run, owner, bot)
        for _ in range(26):
            run.step({})
            if fight.vitals[owner.body].total < 250:
                break
        self.assertEqual(fight.vitals[owner.body].total, 230)
        self.assertIs(fight.halts.pulls[bot.entity], pull)
        npc_control.stop(run.match)
        x, y, z = bot.position
        owner.mover.position[:] = (x, y, z - 1.5)
        owner.mover.body = None
        run.step({})
        vitals = fight.vitals[bot.entity]
        vitals.damage(vitals.total - 30)
        yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
        with self.assertNoLogs("ow174.game", level="ERROR"):
            run.step({owner: (yaw, pitch, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, owner.body)
        self.assertNotIn(bot.entity, fight.halts.pulls)
        self.assertTrue(pull.script.stopped)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(owner.client.deaths)

    def test_actual_halt_slows_talon_pursuit_then_recovers(self):
        for key in ("1", "2", "3", "4"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, orisa, bot = self.halt_npc_scene(key)
                fight = run.match.combat
                self.detonate_npc_halt(run, orisa)
                for _ in range(65):
                    run.step({})
                    if fight.halts.npc_speed_scale(bot.entity, run.frame) < 1:
                        break
                self.assertIn(bot.entity, fight.halts.pulls)
                x, y, z = bot.position
                owner.mover.position[:] = (x + 16, y, z)
                owner.mover.body = None
                run.step({})
                self.engage(run, owner, bot)
                slowed = []
                recovered = []
                for _ in range(75):
                    scale = fight.halts.npc_speed_scale(bot.entity, run.frame + 1)
                    speeds = slowed if scale < 1 else recovered
                    run.step({})
                    speeds.append(math.hypot(bot.velocity[0], bot.velocity[2]))
                self.assertAlmostEqual(max(slowed), bot.speed * 0.35, delta=0.04)
                self.assertAlmostEqual(max(recovered), bot.speed, delta=0.04)
                self.assertNotIn(bot.entity, fight.halts.pulls)

    def launch_halt_toward_talon(self, run, orisa, fight):
        x, y, z = orisa.mover.position
        wall = z + 12
        run.match.collision_world = World([
            (x - 80, y, z - 80, x - 80, y, z + 80, x + 80, y, z + 80),
            (x - 80, y, z - 80, x + 80, y, z + 80, x + 80, y, z - 80),
            (x - 8, y, wall, x + 8, y + 8, wall, x + 8, y, wall),
            (x - 8, y, wall, x - 8, y + 8, wall, x + 8, y + 8, wall),
        ], name="Halt Talon world impact")
        run.step({orisa: (0, 0, 2)})
        for _ in range(20):
            if fight.halts.orbs:
                break
            run.step({})
        orb, = fight.halts.orbs.values()
        self.assertIsNone(orb.until)
        return orb

    @staticmethod
    def advance_halt_world(run, ticks, *, skip):
        for _ in range(1 if skip else ticks):
            run.frame += ticks if skip else 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)

    def test_actual_halt_flight_gap_settles_all_talon_recipients(self):
        for key in ("1", "2", "3", "4", "5"):
            poses = {}
            for mode in ("contiguous", "in flight", "after orb expiry"):
                with self.subTest(npc=self.catalog[key].name, mode=mode):
                    run, owner, orisa, bot = self.halt_npc_scene(key)
                    fight = run.match.combat
                    orb = self.launch_halt_toward_talon(run, orisa, fight)
                    before = bot.position
                    health = fight.vitals[bot.entity]
                    initial = health.total
                    if mode == "after orb expiry":
                        self.advance_halt_world(run, 66, skip=False)
                        self.assertFalse(fight.halts.orbs)
                        self.assertEqual(bot.entity in fight.halts.pulls, key != "5")
                        self.advance_halt_world(run, 89, skip=True)
                    else:
                        self.advance_halt_world(run, 155, skip=mode == "in flight")
                    self.assertFalse(fight.halts.orbs)
                    self.assertFalse(fight.halts.pulls)
                    self.assertEqual(health.total, initial)
                    if key == "5":
                        self.assertEqual(bot.position, before)
                    else:
                        self.assertGreater(math.dist(bot.position, before), 0.5)
                        self.assertAlmostEqual(bot.position[1], before[1], delta=0.02)
                    self.assertEqual(fight.history[bot.entity][-1][1], bot.position)
                    x, y, z = bot.position
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                    owner.take_commands([combat_tests.Command(owner.body_script.frame + 1, yaw=yaw,
                                                               pitch=pitch, buttons=1)])
                    self.assertLess(health.total, initial)
                    poses[mode] = bot.position, bot.velocity, orb.tick, health.total
            self.assertEqual(poses["contiguous"], poses["in flight"])
            self.assertEqual(poses["contiguous"], poses["after orb expiry"])

    def test_actual_trooper_keeps_firing_during_retained_halt_ticks(self):
        for autonomous in (False, True):
            outcomes = []
            for skip in (False, True):
                with self.subTest(autonomous=autonomous, skip=skip):
                    run, owner, orisa, bot = self.halt_npc_scene("1")
                    fight = run.match.combat
                    self.launch_halt_toward_talon(run, orisa, fight)
                    if autonomous:
                        bot.autonomous = True
                    else:
                        self.engage(run, owner, bot)
                    self.advance_halt_world(run, 72, skip=skip)
                    self.assertEqual(bot.attack_target, owner.entity)
                    self.assertIn(bot.entity, fight.halts.pulls)
                    self.assertLess(fight.vitals[owner.body].total, 250)
                    self.assertFalse(fight.is_stunned(bot))
                    outcomes.append((bot.position, fight.vitals[owner.body].total))
            self.assertEqual(outcomes[0], outcomes[1])

    def test_all_five_autonomously_acquire_reaper_and_run_authored_attacks(self):
        for key, damage in (("1", 20), ("2", 50), ("3", 100), ("4", 25), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, _, bot = self.scene(self.catalog[key], autonomous=True)
                fight = run.match.combat
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    for _ in range(200):
                        if fight.vitals[owner.body].total < 250:
                            break
                        run.step({})
                self.assertTrue(bot.autonomous)
                self.assertEqual(bot.attack_target, owner.entity)
                self.assertEqual(fight.vitals[owner.body].total, 250 - damage)
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), owner.body)
                npc_control.stop(run.match)
                for _ in range(30):
                    run.step({})
                self.assertFalse(bot.autonomous)
                self.assertIsNone(bot.attack_target)
                self.assertFalse(bot.attack_input)
                self.assertEqual(fight.vitals[owner.body].total, 250 - damage)

    def ice_wall_scene(self, key, *, wall_distance=1.5, bot_distance=6):
        run, owner, caster, bot = self.scene(self.catalog[key], team_mode=True)
        caster.spawned = True
        owner.team = caster.team = 0
        self.assertTrue(run.match.switch_hero(caster, combat_tests.heroes()[mei.HERO]))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        caster.mover.position[:] = (x, y, z - 3)
        caster.mover.body = None
        bot.position = (x, y, z + bot_distance)
        for _ in range(25):
            run.step({})
        angles = combat_tests.aim(run.match.combat._eye(caster), (x, y, z + wall_distance))
        return run, owner, caster, bot, angles

    def place_ice_wall(self, run, caster, angles):
        for button in (16, 0, 1, 0):
            run.step({caster: (*angles, button)})
        self.assertEqual(len(run.match.combat.walls.pieces), 5)

    def test_actual_four_npc_weapons_hit_pillar_then_exchange_damage_with_reaper(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, caster, bot, angles = self.ice_wall_scene(key)
                self.place_ice_wall(run, caster, angles)
                fight = run.match.combat
                pieces = tuple(fight.walls.pieces.values())
                self.engage(run, owner, bot)
                for _ in range(150):
                    run.step({})
                    if any(piece.health < 400 for piece in pieces):
                        break
                self.assertTrue(any(piece.health < 400 for piece in pieces))
                own = fight.vitals[owner.body]
                self.assertEqual(own.total, 250)
                run.step({caster: (*angles, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(150):
                    run.step({})
                    if own.total < 250:
                        break
                self.assertLess(own.total, 250)
                damaged = own.total
                npc_control.stop(run.match)
                before = fight.vitals[bot.entity].total
                bx, by, bz = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                run.step({owner: (yaw, pitch, 1)})
                self.assertLess(fight.vitals[bot.entity].total, before)
                self.assertGreater(own.total, damaged)

    def test_actual_assassin_pursuit_stops_at_pillar_then_pins_after_cancel(self):
        run, owner, caster, bot, angles = self.ice_wall_scene("4", wall_distance=4.5)
        self.place_ice_wall(run, caster, angles)
        fight = run.match.combat
        start = bot.position[2]
        self.engage(run, owner, bot)
        for _ in range(100):
            run.step({})
        self.assertLess(bot.position[2], start)
        self.assertGreater(bot.position[2], owner.mover.position[2] + 4.5)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values()))
        run.step({caster: (*angles, 16)})
        for _ in range(150):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(fight.vitals[owner.body].total, 225)
        npc_control.stop(run.match)
        before = fight.vitals[bot.entity].total
        bx, by, bz = bot.position
        heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (*heading, 1)})
        self.assertLess(fight.vitals[bot.entity].total, before)
        self.assertGreater(fight.vitals[owner.body].total, 225)

    def test_actual_trooper_and_heavy_melee_hit_pillar_without_damage_through_break(self):
        for key, damage in (("1", 30), ("5", 80)):
            for breaks in (False, True):
                with self.subTest(npc=self.catalog[key].name, breaks=breaks):
                    run, owner, caster, bot, angles = self.ice_wall_scene(
                        key, wall_distance=1.3, bot_distance=2.6,
                    )
                    self.place_ice_wall(run, caster, angles)
                    fight = run.match.combat
                    x, _, _ = owner.mover.position
                    piece = min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0] - x))
                    if breaks:
                        piece.health = damage - 1
                    before = piece.health
                    bx, by, bz = bot.position
                    owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                    self.assertEqual(npc_control.start(run.match, owner, melee=True), bot.entity)
                    for _ in range(140):
                        run.step({})
                        if piece.health < before:
                            break
                    self.assertEqual(piece.health, 0 if breaks else 400 - damage)
                    self.assertEqual(fight.vitals[owner.body].total, 250)
                    self.assertEqual(piece.entity in fight.walls.pieces, not breaks)
                    if not breaks:
                        npc_control.stop(run.match)
                        run.step({caster: (*angles, 16)})
                        for _ in range(330):
                            run.step({})
                        bx, by, bz = bot.position
                        owner.mover.yaw, owner.mover.pitch = combat_tests.aim(
                            fight._eye(owner), (bx, by + 1, bz),
                        )
                        self.assertEqual(npc_control.start(run.match, owner, melee=True), bot.entity)
                        for _ in range(160):
                            run.step({})
                            if fight.vitals[owner.body].total < 250:
                                break
                        self.assertEqual(fight.vitals[owner.body].total, 250 - damage)

    def test_actual_wall_raised_during_assassin_pin_releases_reaper(self):
        run, owner, caster, bot, angles = self.ice_wall_scene("4", wall_distance=0.95, bot_distance=1.9)
        fight = run.match.combat
        self.engage(run, owner, bot)
        for _ in range(90):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.place_ice_wall(run, caster, angles)
        self.assertNotIn(bot.entity, fight.pins)
        before = fight.vitals[owner.body].total
        for _ in range(30):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, before)

    def test_autonomous_trooper_refuses_ally_cover_and_wraith_then_reacquires(self):
        run, owner, _, bot = self.scene(self.catalog["1"], team_mode=True, autonomous=True, bot_team=0)
        fight = run.match.combat
        owner.team = 0
        self.assertIsNone(bot.attack_target)
        x, y, z = owner.mover.position
        mid = z + 0.7
        collision = run.match.collision_world
        wall = World([
            (x-5,y-2,mid,x+5,y-2,mid,x+5,y+5,mid),
            (x-5,y-2,mid,x+5,y+5,mid,x-5,y+5,mid),
            (x-5,y-2,mid,x+5,y+5,mid,x+5,y-2,mid),
            (x-5,y-2,mid,x-5,y+5,mid,x+5,y+5,mid),
        ], name="initial autonomous acquisition blocked")
        bot.team = 1
        run.match.collision_world = wall
        for _ in range(12):
            run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        run.match.collision_world = collision
        for _ in range(25):
            run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(240):
            run.step({})
            if fight.vitals[owner.body].total < 250:
                break
        self.assertEqual(bot.attack_target, owner.entity)
        self.assertEqual(fight.vitals[owner.body].total, 230)

    def test_actual_trooper_long_gap_preserves_primary_and_melee_contacts(self):
        for melee in (False, True):
            with self.subTest(melee=melee):
                contiguous = self.trooper_gap_trace(melee=melee, skipped=False)
                self.assertEqual(contiguous, self.trooper_gap_trace(melee=melee, skipped=True))

    def trooper_gap_trace(self, *, melee, skipped):
        run, owner, _, bot = self.scene(self.catalog["1"])
        fight = run.match.combat
        own = fight.vitals[owner.body] = combat.Vitals(owner.body, 1000)
        x, y, z = owner.mover.position
        distance = 1.25 if melee else 6
        bot.position = (x, y, z + distance)
        if melee:
            owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z + distance))
            self.assertEqual(npc_control.start(run.match, owner, melee=True), bot.entity)
        else:
            run.step({owner: (0, 0, 8)})
            self.assertTrue(reaper.intangible(owner))
            bot.autonomous = True
        damage = fight._damage
        hits = []

        def record_hit(shooter, *args, **kwargs):
            before = own.total
            result = damage(shooter, *args, **kwargs)
            if shooter is bot and own.total < before:
                self.assertFalse(reaper.intangible(owner))
                hits.append((fight.tick, before - own.total))
            return result

        with patch.object(fight, "_damage", side_effect=record_hit):
            for count in (270,) if skipped else (1,) * 270:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(hits)
        self.assertEqual({amount for _, amount in hits}, {30 if melee else 20})
        self.assertEqual(npc_control.stop(run.match), 1)
        health = own.total
        run.frame += 90
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(own.total, health)
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        bx, by, bz = bot.position
        heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (*heading, 1)})
        self.assertLess(enemy.total, before)
        self.assertAlmostEqual(own.total, min(1000, health + (before - enemy.total) * reaper.REAPING_RATIO))
        return hits, own.total, enemy.total, tuple(bot.position)

    def test_actual_heavy_primary_reacquires_reaper_after_shadow_step(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, _, bot = self.scene(self.catalog["5"], autonomous=True)
                fight = run.match.combat
                origin = tuple(owner.mover.position)
                x, y, z = origin
                bot.position = (x, y, z + 6)
                own = fight.vitals[owner.body]
                for _ in range(150):
                    run.step({})
                    if own.total < 250:
                        break
                self.assertEqual(own.total, 245)
                self.assertEqual(bot.attack_target, owner.entity)
                self.assertFalse(bot.attack_melee or bot.attack_charge)
                shadow = shadow_tests.ShadowStepTests()
                shadow.select(run, owner)
                shadow.confirm(run, owner)
                record = fight.reaper_shadows[owner.body]
                destination = record["target"]
                self.assertGreater(math.dist(origin, destination), 2)
                self.assertFalse(reaper.intangible(owner))
                health_before = own.total
                hits, targeting = [], []

                def record_hit(shooter, target, *args, world=fight, enemy=bot, recipient=owner,
                               pool=own, observed=hits, damage=fight._damage, **kwargs):
                    before = pool.total
                    result = damage(shooter, target, *args, **kwargs)
                    if shooter is enemy and target.owner is recipient and pool.total < before:
                        self.assertFalse(reaper.intangible(recipient))
                        observed.append((world.tick, tuple(recipient.mover.position), before - pool.total))
                    return result

                def record_input(world, actor, *, enemy=bot, recipient=owner, trace=targeting,
                                 update=npc_control.update_input):
                    enabled = update(world, actor)
                    if actor is enemy:
                        state = reaper.intangible(recipient), actor.attack_target, actor.attack_input
                        if not trace or trace[-1][1:] != state:
                            trace.append((world.tick, *state))
                    return enabled

                last_input = owner.commands.last_frame
                end = run.frame + 140
                with patch.object(fight, "_damage", side_effect=record_hit), patch.object(
                    npc_control, "update_input", side_effect=record_input,
                ):
                    while run.frame < end:
                        run.frame = end if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, last_input)
                self.assertFalse(reaper.intangible(owner))
                self.assertTrue(record["moved"])
                self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                self.assertEqual(bot.attack_target, owner.entity)
                self.assertTrue(any(position == origin for _, position, _ in hits), hits)
                self.assertTrue(
                    any(math.dist(position, destination) < 0.002 for _, position, _ in hits), hits,
                )
                self.assertEqual({amount for _, _, amount in hits}, {5})
                self.assertEqual(own.total, health_before - 5 * len(hits))
                self.assertIn((True, None, False), [state[1:] for state in targeting])
                self.assertEqual(targeting[-1][1:], (False, owner.entity, True))
                npc_control.stop(run.match)
                enemy = fight.vitals[bot.entity]
                before, health = enemy.total, own.total
                bx, by, bz = bot.position
                heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                run.step({owner: (*heading, 1)})
                dealt = before - enemy.total
                self.assertGreater(dealt, 0)
                self.assertAlmostEqual(own.total, min(250, health + dealt * reaper.REAPING_RATIO))
                outcomes.append((hits, targeting, dealt, own.total))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])

    def test_autonomous_sniper_retargets_after_killing_reaper(self):
        run, owner, other, bot = self.scene(self.catalog["3"], autonomous=True)
        fight = run.match.combat
        fight.vitals[owner.body].damage(180)
        other.spawned = True
        bx, by, bz = bot.position
        other.mover.position[:] = (bx + 4, by, bz)
        other.mover.body = None
        target = next(t for t in fight._targets() if t.owner is other)
        vitals = fight.vitals_of(target)
        for _ in range(380):
            run.step({})
            if vitals.total < 200:
                break
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(fight.vitals[owner.body].killer, bot.entity)
        self.assertEqual(bot.attack_target, other.entity)
        self.assertEqual(vitals.total, 100)

    def autonomous_sniper_projection_trace(self, *, skipped):
        run, owner, zarya, bot = self.scene(self.catalog["3"], team_mode=True)
        owner.team = zarya.team = 0
        zarya.spawned = True
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 10)
        zarya.mover.position[:] = (x + 8, y, z)
        zarya.mover.body = None
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        health = fight.vitals[owner.body]
        health.damage(180)
        self.assertIsNone(bot.attack_target)
        bot.autonomous = True
        start = run.frame
        windup = start + 80
        while run.frame < windup:
            run.frame = windup if skipped else run.frame + 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(bot.attack_target, owner.entity)
        self.assertFalse(fight.rockets)
        self.assertEqual(health.total, 70)
        guard = combat_tests.aim(fight._eye(zarya), fight._eye(owner))
        run.step({zarya: (*guard, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 80
        launches, contacts = [], []
        shoot, damage, shield_damage = fight._shoot, fight._damage, fight.zarya_bubbles.damage

        def observe_launch(shooter, *args, **kwargs):
            before = len(fight.rockets)
            shoot(shooter, *args, **kwargs)
            if shooter is bot and len(fight.rockets) > before:
                rocket = fight.rockets[-1]
                self.assertEqual(rocket.direct.amount, 100)
                launches.append((fight.tick, bot.attack_target, rocket.definition))

        def observe_damage(shooter, target, amount, *args, **kwargs):
            pool = fight.vitals_of(target)
            before = pool.total
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is bot:
                contacts.append((fight.tick, target.entity, amount, before - pool.total))
            return result

        def observe_shield(target, amount, shooter):
            result = shield_damage(target, amount, shooter)
            if shooter is bot:
                self.assertIs(target, bubble)
                self.assertEqual(health.total, 70)
                contacts.append((fight.tick, bubble.entity, amount, result))
            return result

        frames = owner.commands.last_frame, zarya.commands.last_frame
        end = start + 420
        with patch.object(fight, "_shoot", side_effect=observe_launch), patch.object(
            fight, "_damage", side_effect=observe_damage,
        ), patch.object(fight.zarya_bubbles, "damage", side_effect=observe_shield):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual([target for _, target, _ in launches],
                         [owner.entity, owner.entity, zarya.entity], (launches, contacts))
        self.assertEqual([contact[1:] for contact in contacts], [
            (bubble.entity, 100, 80), (owner.body, 100, 70), (zarya.body, 100, 100),
        ])
        for launch, impact in zip(launches, contacts, strict=True):
            self.assertLess(launch[0], impact[0])
        self.assertEqual((health.death_tick, health.killer), (contacts[1][0], bot.entity))
        self.assertEqual(len(owner.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(bot.attack_target, zarya.entity)
        self.assertEqual((owner.commands.last_frame, zarya.commands.last_frame), frames)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertFalse(fight.rockets)
        self.assertEqual(npc_control.stop(run.match), 1)
        return launches, contacts, fight.vitals[zarya.body].total, tuple(bot.position)

    def test_actual_autonomous_sniper_breaks_projection_then_retargets_after_lethal_shot(self):
        stepped = self.autonomous_sniper_projection_trace(skipped=False)
        skipped = self.autonomous_sniper_projection_trace(skipped=True)
        self.assertEqual(stepped, skipped)

    def test_actual_sniper_long_gap_keeps_lethal_retarget_reposition_and_wraith(self):
        for mode in ("retarget", "reposition", "wraith"):
            with self.subTest(mode=mode):
                contiguous = self.sniper_gap_trace(mode, skipped=False)
                self.assertEqual(contiguous, self.sniper_gap_trace(mode, skipped=True))

    def sniper_gap_trace(self, mode, *, skipped):
        from ow174.game import npc_navigation

        run, owner, other, bot = self.scene(self.catalog["3"], autonomous=mode == "retarget")
        fight = run.match.combat
        own = fight.vitals[owner.body]
        origin = tuple(bot.position)
        if mode == "retarget":
            own.damage(180)
            other.spawned = True
            bx, by, bz = origin
            other.mover.position[:] = (bx + 4, by, bz)
            other.mover.body = None
        elif mode == "reposition":
            run.match.game_map = replace(run.match.game_map, map_guid=npc_navigation.RIALTO_MAP)
            self.engage(run, owner, bot)
        else:
            run.step({owner: (0, 0, 8)})
            self.assertTrue(reaper.intangible(owner))
            bot.autonomous = True
        move = npc_navigation.reposition_state(bot)
        wait = npc_navigation.reposition_wait(move)
        shoot, damage = fight._shoot, fight._damage
        launches, hits = [], []

        def record_launch(shooter, *args, **kwargs):
            before = len(fight.rockets)
            shoot(shooter, *args, **kwargs)
            if shooter is bot and len(fight.rockets) > before:
                projectile = fight.rockets[-1]
                self.assertEqual(projectile.direct.amount, 100)
                self.assertFalse(reaper.intangible(owner))
                launches.append((fight.tick, bot.attack_target, projectile.definition))

        def record_hit(shooter, target, *args, **kwargs):
            pool = fight.vitals_of(target)
            before = pool.total
            result = damage(shooter, target, *args, **kwargs)
            if shooter is bot and pool.total < before:
                hits.append((fight.tick, target.entity, before - pool.total))
            return result

        elapsed = {"retarget": 300, "reposition": 235, "wraith": 350}[mode]
        with patch.object(fight, "_shoot", side_effect=record_launch), patch.object(
            fight, "_damage", side_effect=record_hit
        ):
            for count in (elapsed,) if skipped else (1,) * elapsed:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        if mode == "retarget":
            self.assertTrue(own.dead)
            self.assertEqual(own.killer, bot.entity)
            self.assertEqual(len(owner.client.messages(combat.DEATH_NOTICE)), 1)
            self.assertEqual(bot.attack_target, other.entity)
            self.assertEqual(fight.vitals[other.body].total, 100)
            self.assertEqual([target for _, target, _ in launches], [owner.entity, other.entity])
            self.assertEqual(
                [(body, amount) for _, body, amount in hits], [(owner.body, 70), (other.body, 100)],
            )
            self.assertEqual(own.death_tick, hits[0][0])
        elif mode == "reposition":
            self.assertGreater(math.dist(origin, bot.position), 3.8)
            self.assertFalse(wait.active or move.active)
            self.assertEqual(move.counter, 1)
            self.assertEqual(own.total, 150)
            self.assertEqual(len(launches), 1)
            before = fight.vitals[bot.entity].total
            npc_control.stop(run.match)
            x, y, z = bot.position
            shot = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
            run.step({owner: (*shot, 1)})
            self.assertLess(fight.vitals[bot.entity].total, before)
        else:
            self.assertFalse(reaper.intangible(owner))
            self.assertEqual(own.total, 150)
            self.assertEqual(len(launches), 1)
            self.assertEqual(bot.attack_target, owner.entity)
        return launches, hits, own.death_tick, own.total, tuple(bot.position)

    def test_actual_sniper_round_in_flight_passes_wraith_through_long_gap(self):
        outcomes = []
        for skipped in (False, True):
            run, owner, _, bot = self.scene(self.catalog["3"])
            fight = run.match.combat
            x, y, z = owner.mover.position
            bot.position = (x, y, z + 12)
            self.engage(run, owner, bot)
            for _ in range(150):
                run.step({})
                if fight.rockets:
                    break
            projectile, = fight.rockets
            self.assertIs(projectile.shooter, bot)
            self.assertEqual(projectile.direct.amount, 100)
            run.step({owner: (0, 0, 8)})
            self.assertTrue(reaper.intangible(owner))
            for count in (230,) if skipped else (1,) * 230:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertFalse(reaper.intangible(owner))
            self.assertNotIn(projectile, fight.rockets)
            self.assertLess(projectile.position[2], z)
            self.assertEqual(fight.vitals[owner.body].total, 250)
            outcomes.append((projectile.last_tick, projectile.position))
        self.assertEqual(*outcomes)

    def test_autonomous_special_policy_executes_melee_and_charge_by_range(self):
        for key, distance, damage, special in (
            ("1", 1.25, 30, "melee"), ("5", 1.5, 80, "melee"), ("5", 6, 50, "charge"),
        ):
            with self.subTest(npc=self.catalog[key].name, distance=distance):
                run, owner, _, _ = self.scene(self.catalog[key])
                npc.remove_spawned(run.match)
                x, y, z = owner.mover.position
                bot = npc.spawn(run.match, self.catalog[key], (x, y, z + distance), team=1,
                                autonomous=True, autonomous_specials=True)
                fight = run.match.combat
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    for _ in range(100):
                        run.step({})
                        if fight.vitals[owner.body].total < 250:
                            break
                self.assertEqual(bot.attack_target, owner.entity)
                self.assertEqual(bot.attack_melee, special == "melee")
                self.assertEqual(bot.attack_charge, special == "charge")
                self.assertEqual(fight.vitals[owner.body].total, 250 - damage)
                if special == "charge":
                    for _ in range(1100):
                        run.step({})
                        if fight.vitals[owner.body].total < 200:
                            break
                    self.assertFalse(bot.attack_charge)
                    self.assertEqual(fight.vitals[owner.body].total, 195)
                npc_control.stop(run.match)
                self.assertFalse(bot.autonomous)

    def retained_special_trace(self, key, reach, *, skipped):
        run, owner, _, _ = self.scene(self.catalog[key])
        npc.remove_spawned(run.match)
        x, y, z = owner.mover.position
        bot = npc.spawn(run.match, self.catalog[key], (x, y, z + 12), team=1,
                        autonomous=True, autonomous_specials=True)
        fight = run.match.combat
        run.step({})
        self.assertEqual(bot.attack_target, owner.entity)
        self.assertFalse(bot.attack_melee or bot.attack_charge)
        owner.mover.position[:] = (bot.position[0], y, bot.position[2] - reach)
        owner.mover.body = None
        switches, strikes = [], []
        damage = fight._damage

        def observe(source, target, amount, *args, **kwargs):
            if source is bot and target.owner is owner:
                strikes.append((fight.tick, amount, bot.attack_melee, bot.attack_charge))
            return damage(source, target, amount, *args, **kwargs)

        tick = run.frame
        with patch.object(fight, "_damage", side_effect=observe):
            for offset in ((95,) if skipped else range(1, 96)):
                run.match.update((tick + offset) * combat.TICK, tick + offset, frames=False)
                if bot.attack_melee or bot.attack_charge:
                    switches.append(bot.attack_target)
        special = (True, False) if key == "1" else (False, True)
        expected = 30 if key == "1" else 50
        hits = [strike for strike in strikes if strike[2:] == special]
        self.assertTrue(hits, strikes)
        self.assertEqual(hits[0][1], expected)
        self.assertTrue(all(target == owner.entity for target in switches))
        self.assertEqual(bot.attack_target, owner.entity)
        own = fight.vitals[owner.body]
        self.assertFalse(own.dead)
        before = own.total
        enemy = fight.vitals[bot.entity]
        enemy_before = enemy.total
        npc_control.stop(run.match)
        run.frame = tick + 95
        bx, by, bz = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(enemy.total, enemy_before)
        expected_health = min(250, before + (enemy_before - enemy.total) * reaper.REAPING_RATIO)
        self.assertAlmostEqual(own.total, expected_health, places=4)
        return strikes, before, enemy.total, own.total, tuple(bot.position), tuple(owner.mover.position)

    def test_actual_autonomous_specials_reselect_for_retained_reaper(self):
        for key, reach in (("1", 1.25), ("5", 6)):
            with self.subTest(npc=self.catalog[key].name):
                contiguous = self.retained_special_trace(key, reach, skipped=False)
                self.assertEqual(contiguous, self.retained_special_trace(key, reach, skipped=True))

    def autonomous_trooper_projection_trace(self, *, gaps=None):
        run, owner, zarya, _ = self.scene(self.catalog["1"], team_mode=True)
        npc.remove_spawned(run.match)
        owner.team = zarya.team = 0
        zarya.spawned = True
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, z = owner.mover.position
        zarya.mover.position[:] = (x + 8, y, z)
        zarya.mover.body = None
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        health = fight.vitals[owner.body]
        guard = combat_tests.aim(fight._eye(zarya), fight._eye(owner))
        run.step({zarya: (*guard, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 25
        bot = npc.spawn(run.match, self.catalog["1"], (x, y, z + 6), team=1,
                        autonomous=True, autonomous_specials=True)
        contacts = []
        damage, shield_damage = fight._damage, fight.zarya_bubbles.damage

        def observe_damage(shooter, target, amount, *args, **kwargs):
            before = health.total
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is bot and target.owner is owner and health.total < before:
                contacts.append((
                    fight.tick, bot.attack_target, bot.attack_melee, "body", amount, before - health.total,
                ))
            return result

        def observe_shield(target, amount, shooter):
            result = shield_damage(target, amount, shooter)
            if shooter is bot:
                self.assertIs(target, bubble)
                self.assertEqual(health.total, 250)
                contacts.append((fight.tick, bot.attack_target, bot.attack_melee, "shield", amount, result))
            return result

        def advance_until(end, count):
            while run.frame < end:
                run.frame = end if gaps is not None else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                if len(contacts) >= count:
                    break

        start = run.frame
        frames = owner.commands.last_frame, zarya.commands.last_frame
        with patch.object(fight, "_damage", side_effect=observe_damage), patch.object(
            fight.zarya_bubbles, "damage", side_effect=observe_shield,
        ):
            advance_until(start + (80 if gaps is None else gaps[0]), 1)
            rifle_end = run.frame - start
            self.assertEqual([event[1:] for event in contacts], [(owner.entity, False, "shield", 20, 20)])
            self.assertEqual(bubble.health, 5)
            self.assertEqual(bot.attack_target, owner.entity)
            self.assertFalse(bot.attack_melee)
            owner.mover.position[:] = (bot.position[0], y, bot.position[2] - 1.25)
            owner.mover.body = None
            advance_until(start + (155 if gaps is None else gaps[1]), 2)
            melee_end = run.frame - start
            self.assertEqual(contacts[-1][1:], (owner.entity, True, "shield", 30, 5))
            self.assertEqual(health.total, 250)
            self.assertIsNone(fight.zarya_bubbles.for_target(owner))
            owner.mover.position[:] = (bot.position[0], y, bot.position[2] - 6)
            owner.mover.body = None
            advance_until(start + (350 if gaps is None else gaps[2]), 3)
        self.assertEqual([event[1:] for event in contacts], [
            (owner.entity, False, "shield", 20, 20),
            (owner.entity, True, "shield", 30, 5),
            (owner.entity, False, "body", 20, 20),
        ])
        self.assertEqual((owner.commands.last_frame, zarya.commands.last_frame), frames)
        self.assertEqual(health.total, 230)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertEqual(npc_control.stop(run.match), 1)
        elapsed = run.frame - start
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        bx, by, bz = bot.position
        heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (*heading, 1)})
        self.assertLess(enemy.total, before)
        self.assertAlmostEqual(health.total, min(250, 230 + (before - enemy.total) * reaper.REAPING_RATIO))
        return (rifle_end, melee_end, elapsed), contacts, enemy.total, health.total

    def test_actual_autonomous_trooper_switches_to_melee_and_breaks_projection(self):
        stepped = self.autonomous_trooper_projection_trace()
        self.assertEqual(stepped, self.autonomous_trooper_projection_trace(gaps=stepped[0]))

    def autonomous_heavy_charge_projection_trace(self, *, projected, skipped):
        from ow174.game import npc_navigation

        run, owner, zarya, _ = self.scene(self.catalog["5"], team_mode=True)
        npc.remove_spawned(run.match)
        owner.team = zarya.team = 0
        zarya.spawned = True
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, z = owner.mover.position
        zarya.mover.position[:] = (x + 9, y, z)
        zarya.mover.body = None
        for _ in range(12):
            run.step({})
        bot = npc.spawn(run.match, self.catalog["5"], (x, y, z + 6), team=1,
                        autonomous=True, autonomous_specials=True)
        fight = run.match.combat
        health = fight.vitals[owner.body]
        health.damage(225)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(ULT_COST).value())
        for _ in range(85):
            run.step({})
            if npc_navigation.charge_motion(bot) is not None:
                break
        self.assertIsNotNone(npc_navigation.charge_motion(bot))
        self.assertTrue(bot.autonomous)
        self.assertTrue(bot.attack_charge)
        self.assertEqual(bot.attack_target, owner.entity)
        self.assertEqual(health.total, 25)
        guard = combat_tests.aim(fight._eye(zarya), fight._eye(owner))
        run.step({owner: (0, 0, 32), zarya: (*guard, 16 if projected else 0)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertEqual(bubble is not None, projected)
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        frame = owner.commands.last_frame
        before = health.total
        contacts = []
        damage = fight._damage
        shield_damage = fight.zarya_bubbles.damage

        def observe_body(shooter, target, amount, *args, **kwargs):
            if shooter is bot and target.owner is owner and bot.attack_charge and bubble is None:
                contacts.append((fight.tick, amount))
            return damage(shooter, target, amount, *args, **kwargs)

        def observe_shield(target, amount, shooter):
            if shooter is bot and target is bubble and bot.attack_charge:
                contacts.append((fight.tick, amount))
            return shield_damage(target, amount, shooter)

        end = run.frame + 23
        with patch.object(fight, "_damage", side_effect=observe_body), patch.object(
            fight.zarya_bubbles, "damage", side_effect=observe_shield,
        ):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        (impact, amount), = contacts
        self.assertEqual(amount, 50)
        self.assertEqual(owner.commands.last_frame, frame)
        if projected:
            self.assertEqual(bubble.health, 150)
            self.assertGreaterEqual(health.total, before)
            self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        else:
            self.assertTrue(health.dead)
            self.assertEqual((health.death_tick, health.killer), (impact, bot.entity))
            self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(bot.autonomous)
        enemy = fight.vitals[bot.entity]
        pool = enemy.total
        end += 210
        while run.frame < end:
            run.frame = end if skipped else run.frame + 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        if projected:
            self.assertLess(enemy.total, pool)
        else:
            self.assertEqual(enemy.total, pool)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return contacts, health.total, enemy.total, tuple(bot.position)

    def test_actual_autonomous_heavy_charge_resolves_projection_during_blossom(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                stepped = self.autonomous_heavy_charge_projection_trace(projected=projected, skipped=False)
                skipped = self.autonomous_heavy_charge_projection_trace(projected=projected, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_all_five_take_primary_melee_and_fifteen_blossom_ticks(self):
        self.assertEqual({d.name for d in self.catalog.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, victim, bot = self.scene(definition)
                fight = run.match.combat
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                charge = owner.body_script.component.entity_var(ULT_CHARGE)
                tags = next(i for i in bot.body_script.component.instances.values()
                            if i.graph.index == 0x0E62)
                self.assertTrue(tags.states[7].active)
                vitals = fight.vitals[bot.entity]
                base = vitals.total
                fight.vitals[owner.body].damage(100)
                reaper_tests.ReaperTests().fire(run, owner, victim)
                self.assertEqual(vitals.total, base - 120)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 120 * reaper.REAPING_RATIO)
                self.assertEqual(charge.value(), 60)
                # Isolate melee from primary's animation/stack recovery.
                for _ in range(40):
                    run.step({})
                x, y, z = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1.0, z))
                run.step({owner: (yaw, pitch, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, base - 150)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 150 * reaper.REAPING_RATIO)
                self.assertEqual(charge.value(), 75)
                # A fresh real body gives the full channel enough health for all pulses.
                npc.remove_spawned(run.match)
                bot = npc.spawn(run.match, definition, victim.mover.position, team=1)
                for _ in range(2):
                    run.step({})
                vitals = fight.vitals[bot.entity]
                base = vitals.total
                owner.body_script.set_ult_charge(2100)
                with patch.object(combat.log, "exception") as errors:
                    run.step({owner: (0, 0, 32)})
                    for _ in range(190):
                        run.step({})
                    self.assertFalse(errors.called)
                self.assertEqual(vitals.total, max(0, base - 510))
                self.assertEqual(charge.value(), 0)
                self.assertIsNotNone(bot.body_script)

    def test_actual_mercy_boosted_shell_carries_assist_to_all_five_talon_deaths(self):
        for definition in self.catalog.values():
            for health, bonus in ((13, 1.8), (5, 0)):
                with self.subTest(npc=definition.name, health=health):
                    run, owner, _, bot = self.scene(definition, team_mode=True,
                                                    names=("Reaper", "Target", "Mercy"))
                    mercy = run.players[2]
                    mercy.client = NpcClient(mercy)
                    self.assertTrue(run.match.switch_hero(mercy, find_hero("Mercy")))
                    owner.team = mercy.team = 0
                    x, y, z = owner.mover.position
                    mercy.mover.position[:] = (x - 3, y, z)
                    mercy.mover.body = None
                    fight = run.match.combat
                    for player in (owner, mercy):
                        script = player.body_script
                        script.ult_seconds = 0
                        script.component.set(12918, 0)
                        script.set_ult_charge(0)
                    beam = (*combat_tests.aim(fight._eye(mercy), (x, y + 1, z)), 2)
                    for _ in range(20):
                        run.step({mercy: beam})
                    self.assertEqual(mercy_beam.factor(fight, owner), 1.3)
                    vitals = fight.vitals[bot.entity]
                    vitals.damage(vitals.total - health)
                    fight.vitals[owner.body].damage(100)
                    x, y, z = bot.position
                    shot = (*combat_tests.aim(fight._eye(owner), (x, y + 1, z)), 1)
                    script = bot.body_script
                    component = script.component
                    stream = BodyStream(bot.entity)
                    _, stream.base = script.initial
                    known = {index: item[0] for index, item in stream.base.instances.items()}
                    feed_id = next(index for index, graph_id in known.items() if graph_id == 0x0C18)
                    with (patch.object(runtime, "send_message", wraps=runtime.send_message) as messages,
                          patch.object(combat.log, "exception") as errors):
                        run.step({owner: shot, mercy: beam})

                    self.assertFalse(errors.called)
                    self.assertTrue(bot.dead)
                    self.assertTrue(component.owner_dead)
                    self.assertEqual(vitals.killer, owner.body)
                    self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
                    self.assertEqual(mercy.stats.value(stats.ELIMINATIONS), 0)
                    self.assertAlmostEqual(fight.vitals[owner.body].total,
                                           150 + health * reaper.REAPING_RATIO, places=4)
                    self.assertAlmostEqual(owner.body_script.component.vars[ULT_CHARGE].value(),
                                           (health - bonus) * 0.5, places=4)
                    mercy_charge = mercy.body_script.component.vars[ULT_CHARGE].value()
                    self.assertAlmostEqual(mercy_charge, bonus, places=4)
                    self.assertAlmostEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), bonus, places=4)
                    deaths = [call.args for call in messages.call_args_list
                              if call.args[:2] == (component, combat.DIED)]
                    self.assertEqual(len(deaths), 1)
                    assistants = (expr.Entity(mercy.body),) if bonus else ()
                    self.assertEqual(deaths[0][2][combat.ASSISTS], assistants)
                    self.assertEqual(deaths[0][3], owner.body)
                    self.assertEqual(len(owner.client.deaths), 1)
                    entity, snapshot = owner.client.deaths[0]
                    self.assertEqual(entity, bot.entity)
                    self.assertIsNotNone(snapshot)
                    self.assertEqual(mercy.client.deaths, owner.client.deaths)
                    delta, _ = observers.delta(script, stream, snapshot)
                    wire = chunk(delta, script.frame, 0)
                    feed = read_chunk(wire.getvalue(), wire.count, known).instances[feed_id]
                    self.assertEqual(feed.vars[392], (expr.Entity(owner.body), ()))
                    self.assertEqual(feed.vars[9296], (assistants, ()))
                    self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_all_five_primary_kills_retain_actual_corpses_without_respawn(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, victim, bot = self.scene(definition)
                fight = run.match.combat
                body = bot.entity
                base = fight.vitals[body].total
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                with patch.object(combat.log, "exception") as errors:
                    for _ in range(2000):
                        reaper_tests.ReaperTests().fire(run, owner, victim)
                        if bot.dead:
                            break
                    self.assertFalse(errors.called)
                self.assertTrue(bot.dead)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertTrue(owner.client.deaths)
                self.assertEqual(fight.vitals[body].total, 0)
                self.assertAlmostEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(),
                                       min(2100, base * 0.5), places=4)
                for _ in range(250):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertEqual(bot.entity, body)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertNotIn(body, [t.entity for t in fight._enemies(owner)])

    def test_real_mover_capsules_define_all_five_blossom_range_boundaries_and_walls(self):
        expected = {"Trooper": (1.7, 0.5, 5.75), "Enforcer": (1.9, 0.5, 5),
                    "Sniper": (1.75, 0.5, 4.5), "Assassin": (1.5, 0.5, 14),
                    "Heavy Assault": (2.3, 0.7, 2.2)}
        for definition in self.catalog.values():
            for outside in (False, True):
                with self.subTest(npc=definition.name, outside=outside):
                    run, owner, _, bot = self.scene(definition)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    data = mover_data(definition.body)
                    self.assertEqual((target.height, target.radius, data.run_forward),
                                     expected[definition.name])
                    self.assertIs(data, loaded_mover_table()[definition.body])
                    vertical = max(0, 1.8 - (target.height - target.radius))
                    edge = math.sqrt((8 + target.radius) ** 2 - vertical ** 2)
                    x, y, z = owner.mover.position
                    bot.position = (x, y, z + edge + (0.04 if outside else -0.04))
                    initial = fight.vitals[bot.entity].total
                    owner.body_script.set_ult_charge(2100)
                    run.step({owner: (0, 0, 32)})
                    self.assertEqual(fight.vitals[bot.entity].total, initial - (0 if outside else 34))
            with self.subTest(npc=definition.name, wall=True):
                run, owner, victim, bot = self.scene(definition)
                fight = run.match.combat
                x, floor, z = owner.mover.position
                wall = z + 0.75
                run.match.collision_world = World([
                    (x - 20, floor, z - 20, x + 20, floor, z + 20, x + 20, floor, z - 20),
                    (x - 20, floor, z - 20, x - 20, floor, z + 20, x + 20, floor, z + 20),
                    (x - 5, floor, wall, x + 5, floor, wall, x + 5, floor + 5, wall),
                    (x - 5, floor, wall, x + 5, floor + 5, wall, x - 5, floor + 5, wall),
                    (x - 5, floor, wall, x + 5, floor + 5, wall, x + 5, floor, wall),
                    (x - 5, floor, wall, x - 5, floor + 5, wall, x + 5, floor + 5, wall),
                ], name="Actual NPC capsule static occluder")
                for player in run.players:
                    player.mover.use(world=run.match.collision_world)
                initial = fight.vitals[bot.entity].total
                reaper_tests.ReaperTests().fire(run, owner, victim)
                self.assertEqual(fight.vitals[bot.entity].total, initial)
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(fight.vitals[bot.entity].total, initial)

    def test_all_five_receive_actual_ana_status_and_boosted_reaper_damage_with_real_capsules(self):
        from ow174.game import ana
        from ow174.game.content import heroes

        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, caster, bot = self.scene(definition, team_mode=True)
                owner.team = 0
                caster.spawned = True
                run.match.switch_hero(caster, heroes()[ana.HERO])
                caster.team = 0
                x, y, z = owner.mover.position
                caster.mover.position[:] = [x + 1, y, z + 1.5]
                caster.mover.body = None
                fight = run.match.combat
                initial = fight.vitals[bot.entity].total
                yaw, pitch = combat_tests.aim(fight._eye(caster), bot.position)
                run.step({caster: (yaw, pitch, 16)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(fight.vitals[bot.entity].total, initial - 60)
                self.assertEqual(fight._healing_factor(bot), 0)
                self.assertIn("boost", fight.heal_modifiers[owner.body])
                fight.vitals[owner.body].damage(100)
                fight._sync_body_health(owner, fight.vitals[owner.body])
                yaw, pitch = combat_tests.aim(fight._eye(owner),
                                             (bot.position[0], bot.position[1] + 1, bot.position[2]))
                run.step({owner: (yaw, pitch, 1)})
                self.assertEqual(fight.vitals[bot.entity].total, initial - 180)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 120 * reaper.REAPING_RATIO * 1.5)
                entity = bot.entity
                npc.remove_spawned(run.match)
                self.assertNotIn(entity, fight.heal_modifiers)
                self.assertIn("boost", fight.heal_modifiers[owner.body])

    def test_actual_trooper_hit_then_reaper_shot_cannot_bypass_enemy_antiheal(self):
        from ow174.game import ana
        from ow174.game.content import heroes

        run, owner, caster, bot = self.scene(self.catalog["1"], team_mode=True)
        owner.team = 0
        caster.spawned = True
        run.match.switch_hero(caster, heroes()[ana.HERO])
        caster.team = 1
        x, y, z = owner.mover.position
        caster.mover.position[:] = [x + 1, y, z + 1.5]
        caster.mover.body = None
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        fight._sync_body_health(owner, fight.vitals[owner.body])
        yaw, pitch = combat_tests.aim(fight._eye(caster), owner.mover.position)
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 90)
        self.engage(run, owner, bot)
        for _ in range(100):
            run.step({})
            if fight.vitals[owner.body].total < 90:
                break
        self.assertEqual(fight.vitals[owner.body].total, 70)
        npc_control.stop(run.match)
        yaw, pitch = combat_tests.aim(fight._eye(owner),
                                     (bot.position[0], bot.position[1] + 1, bot.position[2]))
        run.step({owner: (yaw, pitch, 1)})
        self.assertEqual(fight.vitals[owner.body].total, 70)
        self.assertEqual(fight.vitals[bot.entity].total, 80)

    def engage(self, run, owner, bot):
        x, y, z = bot.position
        yaw, pitch = combat_tests.aim(run.match.combat._eye(owner), (x, y + 1.0, z))
        owner.mover.yaw, owner.mover.pitch = yaw, pitch
        self.assertEqual(npc_control.start(run.match, owner), bot.entity)

    def test_trooper_can_hit_after_wraith_expires_without_owner_input(self):
        run, owner, _, bot = self.scene(self.catalog["1"])
        fight = run.match.combat
        run.step({owner: (0, 0, 8)})
        self.engage(run, owner, bot)
        last_input = owner.commands.last_frame
        for _ in range(180):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(80):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if fight.vitals[owner.body].total < 250:
                break
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(fight.vitals[owner.body].total, 230)
        self.assertEqual(owner.commands.last_frame, last_input)

    def enforcer_volley_scene(self, *, distance=5):
        run, owner, _, bot = self.scene(self.catalog["2"])
        x, y, z = owner.mover.position
        bot.position = (x, y, z + distance)
        return run, owner, bot, run.match.combat

    def wait_for_enforcer_volley(self, run, bot, fight):
        for _ in range(120):
            run.step({})
            if fight.rockets:
                break
        volley = tuple(fight.rockets)
        self.assertEqual(len(volley), 25)
        self.assertTrue(all(shot.shooter is bot and shot.direct.amount == 2 for shot in volley))
        self.assertEqual({shot.definition for shot in volley}, {npc_context.PROJECTILE})
        return volley

    def test_actual_enforcer_volley_crosses_wraith_and_hits_after_idle_expiry(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, bot, fight = self.enforcer_volley_scene()
                own = fight.vitals[owner.body]
                self.engage(run, owner, bot)
                volley = self.wait_for_enforcer_volley(run, bot, fight)
                run.step({owner: (0, 0, 8)})
                self.assertTrue(reaper.intangible(owner))
                last_input = owner.commands.last_frame
                origin_z = owner.mover.position[2]
                if skipped:
                    run.frame += 28
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(28):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertTrue(all(shot.position[2] < origin_z for shot in volley))
                self.assertEqual(own.total, 250)
                self.assertEqual(owner.commands.last_frame, last_input)
                self.assertEqual(npc_control.stop(run.match), 1)
                run.frame += 210
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(reaper.intangible(owner))
                self.engage(run, owner, bot)
                self.wait_for_enforcer_volley(run, bot, fight)
                for _ in range(28):
                    run.step({})
                    if own.total == 200:
                        break
                self.assertEqual(own.total, 200)
                npc_control.stop(run.match)
                enemy = fight.vitals[bot.entity]
                before = enemy.total
                x, y, z = bot.position
                shot = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*shot, 1)})
                dealt = before - enemy.total
                self.assertGreater(dealt, 0)
                self.assertAlmostEqual(own.total, 200 + dealt * reaper.REAPING_RATIO)

    def test_actual_enforcer_volley_only_ends_blossom_on_lethal_hit(self):
        for health in (200, 30):
            with self.subTest(health=health):
                run, owner, bot, fight = self.enforcer_volley_scene()
                own = fight.vitals[owner.body]
                own.damage(250 - health)
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.engage(run, owner, bot)
                volley = self.wait_for_enforcer_volley(run, bot, fight)
                enemy = fight.vitals[bot.entity]
                before = enemy.total
                run.step({owner: (0, 0, 32)})
                self.assertIsNotNone(reaper.blossom_state(script))
                self.assertEqual(before - enemy.total, 34)
                self.assertAlmostEqual(own.total, health + 34 * reaper.REAPING_RATIO)
                for _ in range(28):
                    run.step({})
                    if fight.is_dead(owner) or own.total <= health + 34 * reaper.REAPING_RATIO - 50:
                        break
                npc_control.stop(run.match)
                if health == 30:
                    self.assertTrue(fight.is_dead(owner))
                    self.assertEqual(own.killer, bot.entity)
                    self.assertIsNone(reaper.blossom_state(script))
                else:
                    self.assertFalse(fight.is_dead(owner))
                    self.assertAlmostEqual(own.total, health + 34 * reaper.REAPING_RATIO - 50)
                    self.assertIsNotNone(reaper.blossom_state(script))
                self.assertTrue(all(shot not in fight.rockets for shot in volley))
                for _ in range(35):
                    run.step({})
                if health == 30:
                    self.assertEqual(before - enemy.total, 34)
                else:
                    self.assertGreater(before - enemy.total, 34)

    def test_actual_enforcer_volley_breaks_projection_during_blossom(self):
        for health in (20, 50):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(health=health, skipped=skipped):
                    run, owner, ally, bot = self.scene(self.catalog["2"], team_mode=True)
                    owner.team = ally.team = 0
                    ally.spawned = True
                    self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                    x, y, z = owner.mover.position
                    ally.mover.position[:] = (x + 4, y, z)
                    ally.mover.body = None
                    bot.position = (x, y, z + 5)
                    for _ in range(30):
                        run.step({})
                    fight = run.match.combat
                    own = fight.vitals[owner.body]
                    enemy = fight.vitals[bot.entity]
                    own.damage(250 - health)
                    script = owner.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    heading = combat_tests.aim(fight._eye(ally), (x, y + 1, z))
                    run.step({ally: (*heading, 16)})
                    bubble = fight.zarya_bubbles.for_target(owner)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                    fight.zarya_bubbles.damage(bubble, 189, bot)
                    self.engage(run, owner, bot)
                    volley = self.wait_for_enforcer_volley(run, bot, fight)
                    before = enemy.total
                    run.step({owner: (0, 0, 32)})
                    self.assertIsNotNone(reaper.blossom_state(script))
                    self.assertEqual(before - enemy.total, 34)
                    self.assertEqual(bubble.health, 11)
                    impacts = []

                    def observe(projection, amount, shooter, *, world=fight, hits=impacts,
                                damage=fight.zarya_bubbles.damage):
                        dealt = damage(projection, amount, shooter)
                        hits.append((world.tick, dealt))
                        return dealt

                    until = run.frame + 28
                    with patch.object(fight.zarya_bubbles, "damage", side_effect=observe):
                        while run.frame < until:
                            run.frame = until if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual([damage for _, damage in impacts], [2] * 5 + [1])
                    self.assertIsNone(fight.zarya_bubbles.for_target(owner))
                    if health == 20:
                        self.assertTrue(fight.is_dead(owner))
                        self.assertEqual(own.killer, bot.entity)
                        self.assertIsNone(reaper.blossom_state(script))
                        self.assertEqual(before - enemy.total, 34)
                    else:
                        self.assertFalse(fight.is_dead(owner))
                        self.assertTrue(all(shot not in fight.rockets for shot in volley))
                        self.assertEqual(before - enemy.total, 3 * 34)
                        self.assertAlmostEqual(own.total, health + 3 * 34 * reaper.REAPING_RATIO - 19 * 2)
                        self.assertIsNotNone(reaper.blossom_state(script))
                    npc_control.stop(run.match)
                    outcomes.append((impacts, own.death_tick))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_enforcer_volley_keeps_its_contact_time_across_wraith_expiry(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, bot, fight = self.enforcer_volley_scene()
                self.engage(run, owner, bot)
                volley = self.wait_for_enforcer_volley(run, bot, fight)
                origin_z = owner.mover.position[2]
                run.step({owner: (0, 0, 8)})
                self.assertTrue(reaper.intangible(owner))
                # Cross expiry and the normal 64-tick NPC weapon catch-up window.
                elapsed = 270
                for count in (elapsed,) if skipped else (1,) * elapsed:
                    run.frame += count
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(reaper.intangible(owner))
                # Compare the original flight independently of new fire after Wraith.
                self.assertTrue(all(shot.position[2] < origin_z for shot in volley))
                self.assertTrue(all(shot not in fight.rockets for shot in volley))
                outcomes.append(tuple((shot.last_tick, shot.position) for shot in volley))
        if len(outcomes) != 2:
            return  # a subtest already reported the failure
        contiguous_flights, skipped_flights = outcomes
        for left, right in zip(contiguous_flights, skipped_flights, strict=True):
            self.assertEqual(left[0], right[0])
            self.assertLess(math.dist(left[1], right[1]), 0.00001)

    def test_actual_enforcer_volley_samples_shadow_protection_at_contact(self):
        shadow = shadow_tests.ShadowStepTests()
        for delay, skipped in ((12, False), (12, True), (0, False), (0, True)):
            with self.subTest(delay=delay, skipped=skipped):
                run, owner, bot, fight = self.enforcer_volley_scene()
                origin = tuple(owner.mover.position)
                self.engage(run, owner, bot)
                # Offset Shadow Step from the same authored Enforcer windup.
                for _ in range(delay):
                    run.step({})
                shadow.select(run, owner)
                shadow.confirm(run, owner)
                record = fight.reaper_shadows[owner.body]
                self.assertIsNotNone(record["target"])
                volley = self.wait_for_enforcer_volley(run, bot, fight)
                self.assertFalse(reaper.intangible(owner))
                own = fight.vitals[owner.body]
                self.assertEqual(own.total, 250)
                if skipped:
                    run.frame += 12
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(12):
                        run.step({})
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertFalse(record["moved"])
                protected = delay == 0
                self.assertEqual(reaper.intangible(owner), protected)
                if protected:
                    self.assertTrue(all(shot.position[2] < origin[2] for shot in volley))
                    self.assertEqual(own.total, 250)
                else:
                    self.assertTrue(all(shot not in fight.rockets for shot in volley))
                    self.assertEqual(own.total, 200)
                npc_control.stop(run.match)
                for _ in range(120):
                    run.step({})
                    if record["moved"] and not reaper.intangible(owner):
                        break
                self.assertTrue(record["moved"])
                self.assertLess(math.dist(owner.mover.position, record["target"]), 0.002)
                self.assertFalse(reaper.intangible(owner))

    def test_actual_enforcer_new_volleys_keep_cadence_across_idle_world_gap(self):
        for mode in ("wraith", "engaged", "autonomous", "pursuit"):
            with self.subTest(mode=mode):
                continuous = self.enforcer_gap_trace(mode, skipped=False)
                skipped = self.enforcer_gap_trace(mode, skipped=True)
                launches, hits, health, travelled, _position = continuous
                self.assertGreaterEqual(len(launches), 2)
                self.assertTrue(all(count == 25 for _, count in launches))
                self.assertTrue(hits)
                self.assertLess(health, 250)
                if mode == "pursuit":
                    self.assertGreater(travelled, 5)
                self.assertEqual(continuous, skipped)

    def enforcer_gap_trace(self, mode, *, skipped):
        run, owner, bot, fight = self.enforcer_volley_scene(distance=14 if mode == "pursuit" else 5)
        origin = tuple(bot.position)
        if mode == "autonomous":
            bot.autonomous = True
        else:
            self.engage(run, owner, bot)
        if mode == "wraith":
            self.wait_for_enforcer_volley(run, bot, fight)
            run.step({owner: (0, 0, 8)})
            self.assertTrue(reaper.intangible(owner))
        start = run.frame
        own = fight.vitals[owner.body]
        launches, hits = [], []
        shoot, damage = fight._shoot, fight._damage

        def record_launch(shooter, *args, **kwargs):
            before = len(fight.rockets)
            shoot(shooter, *args, **kwargs)
            count = len(fight.rockets) - before
            if shooter is bot and count:
                launches.append((fight.tick - start, count))

        def record_hit(shooter, *args, **kwargs):
            before = own.total
            result = damage(shooter, *args, **kwargs)
            if shooter is bot and own.total < before:
                hits.append((fight.tick - start, before - own.total))
            return result

        elapsed = 300
        last_input = owner.commands.last_frame
        with patch.object(fight, "_shoot", side_effect=record_launch), patch.object(
            fight, "_damage", side_effect=record_hit
        ):
            for count in (elapsed,) if skipped else (1,) * elapsed:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(owner.commands.last_frame, last_input)
        self.assertFalse(reaper.intangible(owner))
        return launches, hits, own.total, math.dist(origin, bot.position), tuple(bot.position)

    def autonomous_enforcer_projection_trace(self, *, gap=None):
        run, owner, zarya, bot = self.scene(self.catalog["2"], team_mode=True)
        owner.team = zarya.team = 0
        zarya.spawned = True
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 5)
        zarya.mover.position[:] = (x + 7, y, z)
        zarya.mover.body = None
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        health = fight.vitals[owner.body]
        guard = combat_tests.aim(fight._eye(zarya), fight._eye(owner))
        self.assertIsNone(bot.attack_target)
        bot.autonomous = True
        bubble = None

        launches, contacts = [], []
        shoot, damage, shield_damage = fight._shoot, fight._damage, fight.zarya_bubbles.damage

        def observe_launch(shooter, *args, **kwargs):
            before = len(fight.rockets)
            shoot(shooter, *args, **kwargs)
            if shooter is bot and len(fight.rockets) > before:
                shots = fight.rockets[before:]
                self.assertEqual({shot.direct.amount for shot in shots}, {2})
                launches.append((fight.tick, bot.attack_target, len(shots)))

        def observe_damage(shooter, target, amount, *args, **kwargs):
            before = health.total
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is bot:
                self.assertIs(target.owner, owner)
                contacts.append((fight.tick, "body", amount, before - health.total))
            return result

        def observe_shield(target, amount, shooter):
            result = shield_damage(target, amount, shooter)
            if shooter is bot:
                self.assertIs(target, bubble)
                self.assertEqual(health.total, 250)
                contacts.append((fight.tick, "shield", amount, result))
            return result

        start = run.frame
        with patch.object(fight, "_shoot", side_effect=observe_launch), patch.object(
            fight, "_damage", side_effect=observe_damage,
        ), patch.object(fight.zarya_bubbles, "damage", side_effect=observe_shield):
            # The two volleys are 120 ticks apart. Project at the end of windup.
            windup = start + 62
            while run.frame < windup:
                run.frame = windup if gap is not None else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertEqual(bot.attack_target, owner.entity)
            self.assertEqual(launches, [])
            run.step({zarya: (*guard, 16)})
            bubble = fight.zarya_bubbles.for_target(owner)
            self.assertIsNotNone(bubble)
            self.assertEqual(bubble.health, 200)
            bubble.health = 61
            frames = owner.commands.last_frame, zarya.commands.last_frame
            end = start + (220 if gap is None else gap)
            while run.frame < end:
                run.frame = end if gap is not None else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                if len(contacts) >= 50:
                    break
        self.assertEqual([event[1:] for event in launches], [(owner.entity, 25)] * 2)
        self.assertEqual(launches[1][0] - launches[0][0], 120)
        self.assertEqual([event[1:] for event in contacts], [
            *([("shield", 2, 2)] * 30), ("shield", 2, 1), *([("body", 2, 2)] * 19),
        ])
        self.assertLess(contacts[24][0], launches[1][0])
        self.assertLess(launches[1][0], contacts[25][0])
        self.assertEqual((owner.commands.last_frame, zarya.commands.last_frame), frames)
        self.assertEqual(health.total, 212)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertFalse(fight.rockets)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(bot.autonomous)
        elapsed = run.frame - start
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        bx, by, bz = bot.position
        heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (*heading, 1)})
        self.assertLess(enemy.total, before)
        self.assertAlmostEqual(health.total, min(250, 212 + (before - enemy.total) * reaper.REAPING_RATIO))
        return elapsed, launches, contacts, health.total, enemy.total

    def test_actual_autonomous_enforcer_two_volleys_break_projection_before_body_hits(self):
        stepped = self.autonomous_enforcer_projection_trace()
        self.assertEqual(stepped, self.autonomous_enforcer_projection_trace(gap=stepped[0]))

    def test_enforcer_cadence_replay_is_bounded_and_stops_with_engagement(self):
        run, owner, bot, fight = self.enforcer_volley_scene()
        self.engage(run, owner, bot)
        first = run.frame + 601 - npc_context.WEAPON_REPLAY_TICKS + 1
        run.frame += 601
        with patch.object(fight, "_bot_weapons", wraps=fight._bot_weapons) as weapons:
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual([args[0] for args, _ in weapons.call_args_list], list(range(first, run.frame + 1)))
        self.assertEqual(npc_control.stop(run.match), 1)
        health = fight.vitals[owner.body].total
        run.frame += 300
        with patch.object(fight, "_bot_weapons", wraps=fight._bot_weapons) as weapons:
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        weapons.assert_called_once_with(run.frame)
        self.assertFalse(fight.rockets)
        self.assertFalse(bot.attack_input)
        self.assertEqual(fight.vitals[owner.body].total, health)

    def test_actual_enforcer_projectiles_hit_allied_shield_before_reaper(self):
        from ow174.game import barriers
        from ow174.game.content import heroes

        run, owner, defender, bot = self.scene(self.catalog["2"], team_mode=True)
        defender.spawned = True
        self.assertTrue(run.match.switch_hero(defender, heroes()[barriers.REINHARDT]))
        x, y, z = owner.mover.position
        owner.team = 0
        defender.team = owner.team
        defender.mover.position[:] = (x, y, z + 2.5)
        defender.mover.body = None
        bot.position = (x, y, z + 5)
        for _ in range(30):
            run.step({defender: (0, 0, 2)})
        instance = barriers.active(defender)
        self.assertIsNotNone(instance)
        defender.spawned = False
        self.engage(run, owner, bot)
        defender.spawned = True
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        for _ in range(120):
            run.step({defender: (0, 0, 2)})
            if instance.vars[586].value() < 1600:
                break
        self.assertLess(instance.vars[586].value(), 1600)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertEqual(fight.vitals_of(target).total, 500)

    def test_actual_matrix_intercepts_enforcer_volley_before_reaper(self):
        self.check_actual_matrix_intercept("2", pellets=25, damage=2, windup=0)

    def test_actual_matrix_intercepts_sniper_shot_before_reaper(self):
        self.check_actual_matrix_intercept("3", pellets=1, damage=100, windup=85)

    def check_actual_matrix_intercept(self, key, *, pellets, damage, windup):
        from ow174.game import dva_matrix
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_GRAPH_SHA256 and DVA_MOVER_SHA256 and dva_matrix.TAG_CATALOG_SHA256):
            self.skipTest("requires the pinned D.Va supplements and projectile catalog")
        for held in (False, True):
            with self.subTest(held=held):
                run, owner, mech, bot = self.scene(self.catalog[key], team_mode=True)
                mech.spawned = True
                self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=pools.DVA_MECH)))
                owner.team = mech.team = 0
                fight = run.match.combat
                x, y, z = owner.mover.position
                mech.mover.position[:] = (x + 1.2, y, z + 2)
                mech.mover.body = None
                bot.position = (x, y, z + 5)
                for _ in range(100):
                    run.step({})
                if not held:
                    for _ in range(60):
                        run.step({mech: (0, 0, 2)})
                    run.step({})
                mech.spawned = False
                self.engage(run, owner, bot)
                mech.spawned = True
                for _ in range(windup):
                    run.step({})
                guard = {mech: (0, 0, 2)} if held else {}
                run.step(guard)
                matrix = next(i for i in mech.body_script.component.instances.values()
                              if i.graph.index == dva_matrix.GRAPH)
                self.assertEqual(matrix.states[3].active, held)
                for _ in range(120):
                    run.step(guard)
                    if fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), pellets)
                self.assertEqual({shot.direct.amount for shot in fight.rockets}, {damage})
                self.assertTrue(all(dva_matrix.eligible(shot.definition) for shot in fight.rockets),
                                {shot.definition for shot in fight.rockets})
                with patch.object(fight, "_landed", wraps=fight._landed) as landed:
                    for _ in range(30):
                        if not fight.rockets:
                            break
                        run.step(guard)
                self.assertFalse(fight.rockets)
                self.assertEqual(matrix.states[3].active, held)
                health = fight.vitals[owner.body].total
                if held:
                    landed.assert_not_called()
                    self.assertEqual(health, 250)
                    target = next(t for t in fight._targets() if t.owner is mech)
                    self.assertEqual(fight.vitals_of(target).total, 600)
                else:
                    self.assertLess(health, 250)
                    if pellets == 1:
                        self.assertEqual(health, 250 - damage)
                        landed.assert_called_once()

    def test_actual_enforcer_volley_grants_sigma_shields_and_protects_reaper(self):
        self.check_actual_grasp_absorption("2", pellets=25, damage=2, windup=0, shields=30)

    def test_actual_sniper_shot_grants_sigma_shields_and_protects_reaper(self):
        self.check_actual_grasp_absorption("3", pellets=1, damage=100, windup=85, shields=60)

    def check_actual_grasp_absorption(self, key, *, pellets, damage, windup, shields):
        run, owner, sigma, bot = self.scene(self.catalog[key], team_mode=True)
        sigma.spawned = True
        self.assertTrue(run.match.switch_hero(sigma, find_hero("Sigma")))
        owner.team = sigma.team = 0
        fight = run.match.combat
        x, y, z = owner.mover.position
        sigma.mover.position[:] = (x, y, z + 2.5)
        sigma.mover.body = None
        bot.position = (x, y, z + 5)
        for _ in range(30):
            run.step({})
        sigma.spawned = False
        self.engage(run, owner, bot)
        sigma.spawned = True
        for _ in range(windup):
            run.step({})
        run.step({sigma: (0, 0, 8)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        for _ in range(120):
            if fight.rockets:
                break
            run.step({})
        self.assertEqual(len(fight.rockets), pellets)
        self.assertTrue(all(shot.shooter is bot and shot.direct.amount == damage for shot in fight.rockets))
        for _ in range(20):
            if not fight.rockets:
                break
            run.step({})
        self.assertFalse(fight.rockets)
        npc_control.stop(run.match)
        self.assertEqual(grasp.vars[21312].value(), pellets * damage)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        self.assertEqual(pool.total, 400)
        for _ in range(180):
            if sigma.body in fight.grasp_shields:
                break
            run.step({})
        self.assertFalse(grasp.states[1].active)
        self.assertAlmostEqual(pool.temporary_shields[sigma_grasp.SHIELD_TAG].now, shields, places=4)

    def test_actual_enforcer_volley_reflects_before_reaper_returns_fire(self):
        self.check_actual_deflect_and_return_fire("2", damage=2, windup=0)

    def test_actual_sniper_shot_reflects_before_reaper_returns_fire(self):
        self.check_actual_deflect_and_return_fire("3", damage=100, windup=85)

    def test_actual_reflected_sniper_round_resolves_enemy_grasp(self):
        for grasping in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(grasping=grasping, skipped=skipped):
                    run, owner, genji, bot = self.scene(
                        self.catalog["3"], team_mode=True, names=("Reaper", "Genji", "Sigma"),
                    )
                    sigma = run.players[2]
                    sigma.client = NpcClient(sigma)
                    genji.spawned = True
                    self.assertTrue(run.match.switch_hero(genji, find_hero("Genji")))
                    self.assertTrue(run.match.switch_hero(sigma, find_hero("Sigma")))
                    owner.team = genji.team = 0
                    sigma.team = bot.team
                    fight = run.match.combat
                    x, y, z = owner.mover.position
                    genji.mover.position[:] = (x, y, z + 2.5)
                    sigma.mover.position[:] = (x + 0.8, y, z + 9)
                    genji.mover.body = sigma.mover.body = None
                    bot.position = (x, y, z + 16)
                    for _ in range(30):
                        run.step({})
                    heading = combat_tests.aim(fight._eye(genji), (x, y + 1, z + 16))
                    genji.spawned = False
                    self.engage(run, owner, bot)
                    genji.spawned = True
                    for _ in range(85):
                        run.step({})
                    run.step({genji: (*heading, 16), sigma: (-32768, 0, 8 if grasping else 0)})
                    self.assertEqual(sigma_grasp.active(sigma), grasping)
                    returned = None
                    for _ in range(100):
                        run.step({genji: (*heading, 0), sigma: (-32768, 0, 0)})
                        returned = next((shot for shot in fight.rockets if shot.reflected), None)
                        if returned is not None:
                            break
                    self.assertIsNotNone(returned)
                    self.assertIs(returned.shooter, genji)
                    self.assertEqual(returned.team, owner.team)
                    self.assertEqual(returned.direct.amount, 100)
                    if grasping:
                        contact = fight._projectile_defense_contact(
                            genji, returned.position, returned.direction, 20,
                            moving=True, definition=returned.definition,
                        )
                        self.assertIsNotNone(contact, (
                            returned.position, returned.direction, fight._eye(sigma), sigma.mover.yaw,
                        ))
                        self.assertIs(contact[1], sigma)
                    npc_control.stop(run.match)
                    sniper_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is bot))
                    before = sniper_health.total
                    until = run.frame + 180
                    frame = sigma.commands.last_frame
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(sigma.commands.last_frame, frame)
                    self.assertEqual(fight.vitals[owner.body].total, 250)
                    self.assertEqual(sniper_health.total, before if grasping else before - 100)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0 if grasping else 100)
                    sigma_health = fight.vitals[sigma.body]
                    self.assertAlmostEqual(sigma_health.total, 460 if grasping else 400, places=4)
                    if grasping:
                        shield = sigma_health.temporary_shields[sigma_grasp.SHIELD_TAG]
                        self.assertAlmostEqual(shield.now, 60, places=4)
                    self.assertFalse(sigma_grasp.active(sigma))
                    outcomes.append((
                        sniper_health.total, sigma_health.total, genji.stats.value(stats.HERO_DAMAGE),
                    ))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def check_actual_deflect_and_return_fire(self, key, *, damage, windup):
        run, owner, genji, bot = self.scene(self.catalog[key], team_mode=True)
        genji.spawned = True
        self.assertTrue(run.match.switch_hero(genji, find_hero("Genji")))
        owner.team = genji.team = 0
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 2.5)
        genji.mover.body = None
        bot.position = (x, y, z + 5)
        for _ in range(30):
            run.step({})
        heading = combat_tests.aim(fight._eye(genji), (x, y + 1, z + 5))
        genji.spawned = False
        self.engage(run, owner, bot)
        genji.spawned = True
        for _ in range(windup):
            run.step({})
        run.step({genji: (*heading, 16)})
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is bot))
        before = enemy.total
        own = fight.vitals[owner.body]
        self.assertEqual(own.total, 250)
        own.damage(100)
        returned = None
        for _ in range(100):
            run.step({genji: (*heading, 0)})
            returned = next((shot for shot in fight.rockets if shot.reflected), None)
            if returned is not None:
                break
        self.assertIsNotNone(returned)
        self.assertIs(returned.shooter, genji)
        self.assertEqual(returned.team, owner.team)
        self.assertEqual(returned.direct.amount, damage)
        npc_control.stop(run.match)
        for _ in range(30):
            run.step({genji: (*heading, 0)})
        reflected_damage = before - enemy.total
        self.assertGreater(reflected_damage, 0)
        if key == "3":
            self.assertEqual(reflected_damage, damage)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), reflected_damage)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(own.total, 150)
        heading = combat_tests.aim(fight._eye(owner), (x, y + 1, z + 5))
        run.step({owner: (*heading, 1)})
        dealt = before - reflected_damage - enemy.total
        self.assertGreater(dealt, 0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), dealt)
        self.assertAlmostEqual(own.total, 150 + dealt * 0.35, places=4)

    def test_actual_heavy_charge_windup_resolves_authored_direction_and_speed(self):
        for yaw, expected in ((0, (0, 0, 15)), (16384, (15, 0, 0))):
            with self.subTest(yaw=yaw):
                run, owner, _, bot = self.scene(self.catalog["5"])
                bot.yaw = yaw
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                self.assertTrue(instance.vars[9570].value())
                self.assertEqual(instance.vars[61].value(), 0)
                component.edges([(35, True)])
                for _ in range(30):
                    run.step({})
                self.assertTrue(instance.states[71].active)
                self.assertFalse(getattr(instance.states.get(33), "active", False))
                for _ in range(30):
                    run.step({})
                    if getattr(instance.states.get(33), "active", False):
                        break
                motion = instance.states[33]
                self.assertTrue(motion.active)
                self.assertFalse(instance.states[71].active)
                config = motion.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
                vector = motion.evaluate(config, watch=False)
                for actual, wanted in zip(vector, expected, strict=True):
                    self.assertAlmostEqual(actual, wanted)
                self.assertEqual(instance.vars[9908].value(), 175)
                self.assertEqual(run.match.combat.vitals[owner.body].total, 250)

    def test_actual_heavy_charge_sweeps_at_authored_speed_and_stops_on_cover(self):
        from ow174.game import npc_navigation

        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, owner, _, bot = self.scene(self.catalog["5"])
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 6)
                start = bot.position
                fight = run.match.combat
                owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z + 6))
                self.assertEqual(npc_control.start(run.match, owner, charge=True), bot.entity)
                for _ in range(70):
                    run.step({})
                    if npc_navigation.charge_motion(bot) is not None:
                        break
                self.assertIsNotNone(npc_navigation.charge_motion(bot))
                self.assertEqual(bot.position, start)
                for _ in range(4):
                    run.step({})
                self.assertAlmostEqual(start[2] - bot.position[2], 4 * 15 * combat.TICK, delta=0.01)
                if blocked:
                    mid = z + 3
                    lo, hi = x - 20, x + 20
                    world = World([
                        (lo,y,z-20,lo,y,z+20,hi,y,z+20),
                        (lo,y,z-20,hi,y,z+20,hi,y,z-20),
                        (lo,y,mid,hi,y,mid,hi,y+5,mid),
                        (lo,y,mid,hi,y+5,mid,lo,y+5,mid),
                        (lo,y,mid,hi,y+5,mid,hi,y,mid),
                        (lo,y,mid,lo,y+5,mid,hi,y+5,mid),
                    ], name="wall inserted into Heavy charge")
                    run.match.collision_world = world
                    owner.mover.use(world=world)
                    for _ in range(30):
                        run.step({})
                        self.assertGreater(bot.position[2], mid)
                        if npc_navigation.charge_motion(bot) is None:
                            break
                    self.assertFalse(bot.firing)
                else:
                    npc_control.stop(run.match)
                stopped = bot.position
                for _ in range(12):
                    run.step({})
                self.assertIsNone(npc_navigation.charge_motion(bot))
                self.assertEqual(bot.position, stopped)
                self.assertEqual(bot.velocity, (0, 0, 0))
                instance = npc_navigation.charge_instance(bot)
                self.assertEqual(instance.vars[59].value(), 0)
                self.assertGreater(instance.states[41].remaining(), 14)
                self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_heavy_charge_stop_resets_windup_and_authored_cooldown(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["5"])
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 6)
        fight = run.match.combat
        owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z + 6))
        start = bot.position
        npc_control.start(run.match, owner, charge=True)
        instance = npc_navigation.charge_instance(bot)
        for _ in range(20):
            run.step({})
        self.assertTrue(instance.states[71].active)
        self.assertEqual(instance.vars[59].value(), 1)
        npc_control.stop(run.match)
        ability = instance.states[41]
        self.assertEqual(ability.remaining(), 15)
        self.assertEqual(instance.vars[59].value(), 0)
        self.assertFalse(instance.states[71].active)
        run.step({})
        npc_control.start(run.match, owner, charge=True)
        for _ in range(60):
            run.step({})
        self.assertFalse(instance.states[71].active)
        self.assertIsNone(npc_navigation.charge_motion(bot))
        self.assertEqual(bot.position, start)
        self.assertGreater(ability.remaining(), 13)
        npc_control.stop(run.match)
        for _ in range(960):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        npc_control.start(run.match, owner, charge=True)
        for _ in range(65):
            run.step({})
            if npc_navigation.charge_motion(bot) is not None:
                break
        self.assertIsNotNone(npc_navigation.charge_motion(bot))
        self.assertEqual(instance.states[71].counter, 2)

    def test_actual_heavy_charge_contact_damages_reaper_but_not_wraith(self):
        from ow174.game import npc_navigation

        for wraith in (False, True):
            with self.subTest(wraith=wraith):
                run, owner, _, bot = self.scene(self.catalog["5"])
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 6)
                fight = run.match.combat
                if wraith:
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z + 6))
                self.assertEqual(npc_control.start(run.match, owner, charge=True), bot.entity)
                instances = bot.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x1408)
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    for _ in range(85):
                        run.step({})
                self.assertLess(bot.position[2], z + 1)
                contact = instance.states.get(53)
                self.assertFalse(contact and contact.counter)
                self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 200)
                if wraith:
                    for _ in range(180):
                        run.step({})
                        if npc_navigation.charge_motion(bot) is None:
                            break
                    self.assertIsNone(npc_navigation.charge_motion(bot))
                    self.assertEqual(instance.vars[59].value(), 0)
                    self.assertGreater(instance.states[41].remaining(), 14)
                    self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_heavy_charge_contact_allows_reaper_quick_melee(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["5"])
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 6)
        fight = run.match.combat
        own = fight.vitals[owner.body]
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        self.engage_heavy_charge(run, owner, bot)
        for _ in range(95):
            run.step({})
            if own.total < 250:
                break
        self.assertEqual(own.total, 200)
        self.assertIn(owner.body, fight.npc_impulses)
        self.assertFalse(fight.is_stunned(owner))
        for frame in range(30):
            bx, by, bz = bot.position
            heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
            run.step({owner: (*heading, 0x800 if frame == 0 else 0)})
            if enemy.total < before:
                break
        self.assertEqual(enemy.total, before - 30)
        self.assertAlmostEqual(own.total, 200 + 30 * 0.35, places=4)
        self.assertIsNotNone(npc_navigation.charge_motion(bot))

    def test_actual_heavy_charge_long_gap_preserves_contact_and_protection(self):
        for mode in ("wraith", "blossom", "impact"):
            with self.subTest(mode=mode):
                continuous = self.heavy_gap_trace(mode, skipped=False)
                self.assertEqual(continuous, self.heavy_gap_trace(mode, skipped=True))

    def heavy_gap_trace(self, mode, *, skipped):
        from ow174.game import npc_navigation

        if mode == "impact":
            run, owner, mech, bot, instance, mech_pool, _ = self.heavy_charge_mech(backstop=True)
            mech_before = mech_pool.total
        else:
            run, owner, _, bot = self.scene(self.catalog["5"])
            x, y, z = owner.mover.position
            bot.position = (x, y, z + 6)
            if mode == "wraith":
                run.step({owner: (0, 0, 8)})
                self.assertTrue(reaper.intangible(owner))
            self.engage_heavy_charge(run, owner, bot)
            instance = npc_navigation.charge_instance(bot)
        fight = run.match.combat
        own = fight.vitals[owner.body]
        enemy = fight.vitals[bot.entity]
        if mode == "blossom":
            own.damage(225)
            owner.body_script.set_ult_charge(2100)
            for _ in range(70):
                run.step({})
                if npc_navigation.charge_motion(bot) is not None:
                    break
            self.assertIsNotNone(npc_navigation.charge_motion(bot))
            run.step({owner: (0, 0, 32)})
            self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        elapsed = 290
        last_input = owner.commands.last_frame
        for count in (elapsed,) if skipped else (1,) * elapsed:
            run.frame += count
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(owner.commands.last_frame, last_input)
        self.assertIsNone(npc_navigation.charge_motion(bot))
        cooldown = instance.states[41].remaining()
        self.assertGreater(cooldown, 10)
        impact_pose = None
        if mode == "wraith":
            self.assertEqual(own.total, 250)
            self.assertFalse(reaper.intangible(owner))
        elif mode == "blossom":
            self.assertTrue(own.dead)
            self.assertEqual(own.killer, bot.entity)
            self.assertEqual(len(owner.client.messages(combat.DEATH_NOTICE)), 1)
            self.assertIsNone(reaper.blossom_state(owner.body_script))
        else:
            self.assertEqual(mech_before - mech_pool.total, 295)
            self.assertEqual(instance.states[11].counter, 1)
            self.assertFalse(fight.is_stunned(mech))
            self.assertEqual(own.total, 200)
            impact_pose = tuple(mech.mover.position)
            bx, by, bz = bot.position
            heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
            before = enemy.total
            run.step({owner: (*heading, 1)})
            self.assertLess(enemy.total, before)
            self.assertAlmostEqual(own.total, 200 + (before - enemy.total) * reaper.REAPING_RATIO)
        return (
            own.total, own.death_tick, enemy.total, tuple(bot.position), tuple(owner.mover.position),
            impact_pose, cooldown,
        )

    def test_actual_heavy_charge_hits_ice_wall_flag_and_reaper_shell_hits_pillar(self):
        from ow174.game import npc_navigation

        run, owner, caster, bot = self.scene(self.catalog["5"], team_mode=True)
        caster.spawned = True
        owner.team, caster.team, bot.team = 2, 0, 1
        self.assertTrue(run.match.switch_hero(caster, combat_tests.heroes()[mei.HERO]))
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        caster.mover.position[:] = (x, y, z - 4)
        caster.mover.body = None
        owner.mover.position[:] = (x, y, z - 2)
        owner.mover.body = None
        bot.position = (x, y, z + 6)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        angles = combat_tests.aim(fight._eye(caster), (x, y, z))
        for button in (16, 0, 1, 0):
            run.step({caster: (*angles, button)})
        pieces = fight.walls.pieces
        self.assertEqual(len(pieces), 5)
        pillar = min(pieces.values(), key=lambda p: abs(p.feet[0] - x))
        self.assertTrue(pillar.charge_contact)
        self.assertEqual(pillar.health, 400)
        self.engage_heavy_charge(run, owner, bot)
        instance = npc_navigation.charge_instance(bot)
        for _ in range(100):
            run.step({})
            if pillar.health < 400:
                break
        self.assertEqual(instance.states[53].counter, 1)
        self.assertEqual(pillar.health, 225)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertIsNone(npc_navigation.charge_motion(bot))
        before = fight.vitals[bot.entity].total
        yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (yaw, pitch, 1)})
        self.assertEqual(pillar.health, 105)
        self.assertEqual(fight.vitals[bot.entity].total, before)
        run.step({caster: (*angles, 16)})
        self.assertFalse(pieces)
        for _ in range(35):
            run.step({})
        bx, by, bz = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(fight.vitals[bot.entity].total, before)

    def test_heavy_charge_direct_scope_accepts_fixture_961_flag(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["5"])
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 6)
        # The queried entity flag is authored; this fixture supplies its value.
        owner.body_script.component.set(961, True)
        fight = run.match.combat
        pool = fight.vitals[owner.body]
        before = pool.total
        self.engage_heavy_charge(run, owner, bot)
        instance = npc_navigation.charge_instance(bot)
        for _ in range(100):
            run.step({})
        self.assertEqual(instance.states[53].counter, 1)
        self.assertEqual(before - pool.total, 175)
        self.assertIsNone(npc_navigation.charge_motion(bot))

    def test_actual_heavy_charge_is_interrupted_by_sleep_or_lethal_reaper_fire(self):
        from ow174.game import npc_navigation

        for action in ("sleep", "lethal shell"):
            with self.subTest(action=action):
                run, owner, caster, bot = self.scene(self.catalog["5"])
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 7)
                if action == "sleep":
                    bot.position = (x, y, z + 10)
                    caster.spawned = True
                    self.assertTrue(run.match.switch_hero(caster, combat_tests.heroes()[ana.HERO]))
                    caster.mover.position[:] = (x + 1.5, y, z + 8.5)
                    caster.mover.body = None
                    for _ in range(20):
                        run.step({})
                fight = run.match.combat
                npc_vitals = fight.vitals[bot.entity]
                if action == "lethal shell":
                    npc_vitals.damage(npc_vitals.total - 30)
                self.engage_heavy_charge(run, owner, bot)
                for _ in range(70):
                    run.step({})
                    if npc_navigation.charge_motion(bot) is not None:
                        break
                self.assertIsNotNone(npc_navigation.charge_motion(bot))
                motion_start = bot.position[2]
                run.step({})
                self.assertLess(bot.position[2], motion_start)
                if action == "sleep":
                    for frame in range(28):
                        bx, by, bz = bot.position
                        yaw, pitch = combat_tests.aim(fight._eye(caster), (bx, by + 1, bz - 0.45))
                        run.step({caster: (yaw, pitch, 8 if frame == 0 else 0)})
                        if fight.is_stunned(bot):
                            break
                    self.assertIn(bot.entity, fight.ana_sleeps)
                    run.step({})
                    instance = npc_navigation.charge_instance(bot)
                    self.assertGreater(instance.states[41].remaining(), 14)
                else:
                    bx, by, bz = bot.position
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                    run.step({owner: (yaw, pitch, 1)})
                    self.assertTrue(bot.dead)
                    self.assertEqual(npc_vitals.killer, owner.body)
                stopped = bot.position
                health_on_stop = fight.vitals[owner.body].total
                self.assertEqual(health_on_stop, 200 if action == "sleep" else 250)
                for _ in range(30):
                    run.step({})
                self.assertEqual(bot.position, stopped)
                self.assertEqual(fight.vitals[owner.body].total, health_on_stop)
                if action == "sleep":
                    before = npc_vitals.total
                    bx, by, bz = stopped
                    yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                    run.step({owner: (yaw, pitch, 1)})
                    self.assertLess(npc_vitals.total, before)
                    self.assertIsNone(npc_navigation.charge_motion(bot))

    def test_actual_heavy_charge_hits_shadow_windup_but_misses_immune_shadow(self):
        from ow174.game import npc_navigation

        shadow = shadow_tests.ShadowStepTests()
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, owner, _, bot = self.scene(self.catalog["5"])
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 6)
                fight = run.match.combat
                if protected:
                    shadow.select(run, owner)
                    shadow.confirm(run, owner)
                    for _ in range(8):
                        run.step({})
                self.engage_heavy_charge(run, owner, bot)
                if not protected:
                    for _ in range(70):
                        run.step({})
                        if npc_navigation.charge_motion(bot) is not None:
                            break
                    self.assertIsNotNone(npc_navigation.charge_motion(bot))
                    shadow.select(run, owner)
                    shadow.confirm(run, owner)
                record = fight.reaper_shadows[owner.body]
                self.assertIsNotNone(record["target"])
                instance = npc_navigation.charge_instance(bot)
                immune_during_contact = False
                for _ in range(75):
                    run.step({})
                    if math.dist(bot.position, (x, y, z)) < 1:
                        immune_during_contact |= reaper.intangible(owner)
                    if not protected and fight.vitals[owner.body].total < 250:
                        self.assertFalse(reaper.intangible(owner))
                        break
                self.assertEqual(immune_during_contact, protected)
                self.assertEqual(fight.vitals[owner.body].total, 250 if protected else 200)
                contact = instance.states.get(53)
                self.assertFalse(contact and contact.counter)
                for _ in range(110):
                    run.step({})
                self.assertTrue(record["moved"])

    def heavy_charge_flank(self, offset, *, wraith=False, allied=False, covered=False, escape=False):
        from ow174.game import npc_navigation

        run, owner, centre, bot = self.scene(self.catalog["5"], team_mode=True)
        centre.spawned = True
        centre.team = 0
        owner.team = bot.team if allied else 0
        x, y, z = owner.mover.position
        centre.mover.position[:] = (x, y, z)
        centre.mover.body = None
        owner.mover.position[:] = (x + offset, y, z + 1)
        owner.mover.body = None
        bot.position = (x, y, z + 6)
        if covered:
            left, right, near, far = x - 25, x + 25, z - 25, z + 25
            wall = x + 0.8
            low, high, top = z - 2, z + 5, y + 4
            run.match.collision_world = World([
                (left, y, near, left, y, far, right, y, far),
                (left, y, near, right, y, far, right, y, near),
                (wall, y, low, wall, top, low, wall, top, high),
                (wall, y, low, wall, top, high, wall, y, high),
                (wall, y, low, wall, top, high, wall, top, low),
                (wall, y, low, wall, y, high, wall, top, high),
            ], name="Heavy charge flank cover")
            for player in run.players:
                player.mover.use(world=run.match.collision_world)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        self.engage_heavy_charge(run, centre, bot)
        instance = npc_navigation.charge_instance(bot)
        if wraith:
            run.step({owner: (0, 0, 8)})
            self.assertTrue(reaper.intangible(owner))
        start = tuple(owner.mover.position)
        height = start[1]
        first_impulse = None
        for _ in range(100):
            run.step({})
            impulse = fight.npc_impulses.get(owner.body)
            if first_impulse is None and impulse is not None:
                first_impulse = impulse[1]
                if escape:
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                    run.step({})
                    self.assertNotIn(owner.body, fight.npc_impulses)
            height = max(height, owner.mover.position[1])
        direct = instance.states.get(53)
        self.assertFalse(direct and direct.counter)
        centre_pool = fight.vitals[centre.body]
        self.assertEqual(centre_pool.maximum - centre_pool.total, 50)
        return run, owner, bot, first_impulse, height - start[1]

    def test_actual_heavy_charge_hits_flanking_reaper_once_and_direct_target_separately(self):
        run, owner, bot, impulse, rise = self.heavy_charge_flank(1.65)
        fight = run.match.combat
        self.assertEqual(fight.vitals[owner.body].total, 200)
        self.assertIsNotNone(impulse)
        self.assertAlmostEqual(math.hypot(impulse[0], impulse[2]), 2)
        self.assertEqual(impulse[1], 10)
        self.assertGreater(rise, 0.25)
        before = fight.vitals[bot.entity].total
        bx, by, bz = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(fight.vitals[bot.entity].total, before)

    def test_actual_heavy_charge_side_attack_skips_wraith_allies_range_and_cover(self):
        for offset, kwargs in ((1.65, {"wraith": True}), (1.65, {"allied": True}),
                               (3.5, {}), (1.65, {"covered": True})):
            with self.subTest(offset=offset, **kwargs):
                run, owner, _, impulse, rise = self.heavy_charge_flank(offset, **kwargs)
                self.assertEqual(run.match.combat.vitals[owner.body].total, 250)
                self.assertIsNone(impulse)
                self.assertLess(rise, 0.1)

    def test_actual_heavy_charge_side_knockback_retires_on_wraith(self):
        run, owner, _, impulse, _ = self.heavy_charge_flank(1.65, escape=True)
        self.assertIsNotNone(impulse)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(run.match.combat.vitals[owner.body].total, 200)

    def test_heavy_impact_health_scope_consumes_scalar_target_from_authored_graph(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["5"])
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 6)
        self.heavy_charge_world(run, x, y, z, backstop=True)
        fight = run.match.combat
        pool = fight.vitals[owner.body] = combat.Vitals(owner.body, 600)
        self.engage_heavy_charge(run, owner, bot)
        instance = npc_navigation.charge_instance(bot)
        world = instance.component.world
        track = world.track_targets

        def supply_pin_candidate(state):
            if state.instance is instance and state.node.pos == 96:
                # Diagnostic fixture for the still unhosted native query.
                gap = bot.position[2] - owner.mover.position[2]
                return (expr.Entity(owner.body),) if 0 < gap < 1.5 else ()
            return track(state)

        with (patch.object(world, "track_targets", side_effect=supply_pin_candidate),
              patch.object(fight, "_damage", wraps=fight._damage) as damage):
            for _ in range(275):
                run.step({})
        self.assertEqual(instance.vars[536].value(), expr.Entity(owner.body))
        self.assertEqual(instance.states[11].counter, 1)
        amounts = [call.args[2] for call in damage.call_args_list
                   if call.args[0] is bot and call.args[1].owner is owner]
        self.assertEqual(amounts, [50, 300])
        self.assertEqual(pool.total, 250)

    @staticmethod
    def heavy_charge_world(run, x, y, z, *, backstop, wall_distance=3):
        wall = z - wall_distance
        triangles = [
            (x - 80, y, z - 80, x - 80, y, z + 80, x + 80, y, z + 80),
            (x - 80, y, z - 80, x + 80, y, z + 80, x + 80, y, z - 80),
        ]
        if backstop:
            triangles += [
                (x - 5, y, wall, x + 5, y, wall, x + 5, y + 5, wall),
                (x - 5, y, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
            ]
        run.match.collision_world = World(triangles, name="Heavy charge impact arena")
        for player in run.players:
            player.mover.use(world=run.match.collision_world)

    def heavy_charge_mech(self, *, backstop, ice=False, support=None, charge_before_wall_expiry=None):
        from ow174.game import npc_navigation
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256):
            self.skipTest("requires the pinned 0407 mover and graph supplements")
        names = ("Reaper", "Mech", "Mei") if ice else ("Reaper", "Mech")
        if support is not None:
            names += (support,)
        run, owner, mech, bot = self.scene(self.catalog["5"], team_mode=True, names=names)
        if support is not None:
            run.players[-1].spawned = False
        owner.team = mech.team = 0
        mech.spawned = True
        self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=pools.DVA_MECH)))
        x, y, z = owner.mover.position
        mech.mover.position[:] = (x, y, z)
        mech.mover.body = None
        owner.mover.position[:] = (x + 1.65, y, z + 1)
        owner.mover.body = None
        bot.position = (x, y, z + 6)
        self.heavy_charge_world(run, x, y, z, backstop=backstop)
        if ice:
            caster = run.players[2]
            caster.team = 0
            self.assertTrue(run.match.switch_hero(caster, combat_tests.heroes()[mei.HERO]))
            caster.mover.position[:] = (x, y, z - 6)
            caster.mover.body = None
        for _ in range(100):
            run.step({})
        self.assertTrue(mech.body_script.component.entity_var(6700, create=False).value())
        fight = run.match.combat
        if ice:
            angles = combat_tests.aim(fight._eye(caster), (x, y, z - 3))
            self.place_ice_wall(run, caster, angles)
            if charge_before_wall_expiry is not None:
                expires = min(piece.expires for piece in fight.walls.pieces.values())
                run.frame = expires - charge_before_wall_expiry
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        target = next(t for t in fight._targets() if t.owner is mech)
        pool = fight.vitals_of(target)
        self.engage_heavy_charge(run, mech, bot)
        instance = npc_navigation.charge_instance(bot)
        return run, owner, mech, bot, instance, pool, z

    def test_actual_heavy_charge_impact_selects_live_mech_and_spares_direct_damage(self):
        self.check_heavy_charge_impact(backstop=True)

    def test_actual_heavy_charge_impact_breaks_projected_barrier_without_spill(self):
        run, owner, mech, _, instance, pool, z = self.heavy_charge_mech(backstop=True, support="Zarya")
        caster = run.players[2]
        caster.team = owner.team
        caster.spawned = True
        self.assertTrue(run.match.switch_hero(caster, combat_tests.heroes()[zarya_barriers.HERO]))
        x, y, _ = mech.mover.position
        caster.mover.position[:] = (x + 4, y, z - 1)
        caster.mover.body = None
        fight = run.match.combat
        for _ in range(120):
            run.step({})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual(pool.total, 600)
        mx, my, mz = mech.mover.position
        heading = combat_tests.aim(fight._eye(caster), (mx, my + 1, mz))
        run.step({caster: (*heading, 16)})
        bubble = fight.zarya_bubbles.for_target(mech)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        for _ in range(35):
            run.step({})
            if not fight.is_stunned(mech):
                break
        self.assertFalse(fight.is_stunned(mech))
        self.assertEqual(instance.states[11].counter, 1)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(mech))
        self.assertEqual(pool.total, 600)

    def test_actual_heavy_charge_crushes_carried_mech_at_placed_ice_wall(self):
        self.check_heavy_charge_impact(backstop=False, ice=True)

    def test_actual_heavy_charge_carry_resolves_ice_wall_expiry_tick(self):
        run, _, _, _, _, pool, _ = self.heavy_charge_mech(backstop=False, ice=True)
        start = run.frame
        before = pool.total
        for _ in range(115):
            run.step({})
            if pool.total < before:
                break
        self.assertEqual(before - pool.total, 295)
        travel = run.frame - start
        for blocked in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(blocked=blocked, skipped=skipped):
                    outcomes.append(self.heavy_carry_expiry_outcome(travel, blocked=blocked, skipped=skipped))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def heavy_carry_expiry_outcome(self, travel, *, blocked, skipped):
        from ow174.game import npc_navigation

        run, owner, mech, bot, instance, pool, z = self.heavy_charge_mech(
            backstop=False, ice=True, charge_before_wall_expiry=travel + int(blocked),
        )
        fight = run.match.combat
        piece = min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0] - mech.mover.position[0]))
        expires = piece.expires
        before = pool.total
        end = expires + 245
        for tick in (end,) if skipped else range(run.frame + 1, end + 1):
            run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(fight.walls.pieces)
        self.assertFalse(fight.is_stunned(mech))
        self.assertIsNone(npc_navigation.charge_motion(bot))
        self.assertEqual(instance.vars[536].value(), expr.Entity(mech.body))
        self.assertEqual(fight.vitals[owner.body].total, 200)
        self.assertEqual(piece.health, 400)
        impact = instance.states.get(11)
        self.assertEqual(impact.counter if impact is not None else 0, int(blocked))
        self.assertEqual(before - pool.total, 295 if blocked else 0)
        if blocked:
            limit = piece.feet[2] + piece.depth + mech.mover.data.stand_radius
            self.assertAlmostEqual(mech.mover.position[2], limit, delta=0.03)
        else:
            self.assertLess(mech.mover.position[2], z - 20)
        return pool.total, tuple(mech.mover.position), tuple(bot.position), tuple(owner.mover.position)

    def check_heavy_charge_impact(self, *, backstop, ice=False):
        run, owner, mech, bot, instance, pool, z = self.heavy_charge_mech(backstop=backstop, ice=ice)
        fight = run.match.combat
        before = pool.total
        carried = False
        for _ in range(115):
            run.step({})
            if fight.is_stunned(mech) and mech.mover.position[2] < z - 0.5:
                self.assertEqual(pool.total, before)
                carried = True
            if pool.total < before:
                break
        self.assertTrue(carried)
        self.assertFalse(fight.is_stunned(mech))
        self.assertEqual(instance.vars[536].value(), expr.Entity(mech.body))
        self.assertEqual(instance.states[11].counter, 1)
        self.assertEqual(before - pool.total, 295)
        if ice:
            piece = min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0] - mech.mover.position[0]))
            limit = piece.feet[2] + piece.depth + mech.mover.data.stand_radius
            self.assertAlmostEqual(mech.mover.position[2], limit, delta=0.03)
            self.assertEqual(piece.health, 400)
        self.assertEqual(fight.vitals[owner.body].total, 200)
        bot_pool = fight.vitals[bot.entity]
        before = bot_pool.total
        bx, by, bz = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(bot_pool.total, before)

    def test_actual_heavy_charge_timeout_releases_carried_mech_without_impact(self):
        self.check_heavy_charge_timeout(ice=False)

    def test_actual_heavy_charge_passes_cancelled_wall_and_releases_on_timeout(self):
        self.check_heavy_charge_timeout(ice=True)

    def check_heavy_charge_timeout(self, *, ice):
        run, _, mech, _, instance, pool, z = self.heavy_charge_mech(backstop=False, ice=ice)
        fight = run.match.combat
        before = pool.total
        carried = False
        for _ in range(275):
            run.step({})
            if fight.is_stunned(mech):
                if ice and not carried:
                    run.step({run.players[2]: (0, 0, 16)})
                    self.assertFalse(fight.walls.pieces)
                    self.assertTrue(fight.is_stunned(mech))
                carried = True
            elif carried:
                break
        self.assertTrue(carried)
        self.assertFalse(fight.is_stunned(mech))
        self.assertLess(mech.mover.position[2], z - 20)
        impact = instance.states.get(11)
        self.assertFalse(impact and impact.counter)
        self.assertEqual(pool.total, before)

    def test_actual_projected_barrier_blocks_sleep_dart_during_heavy_charge(self):
        from ow174.game import npc_navigation

        run, owner, ana_player, bot = self.scene(
            self.catalog["5"], team_mode=True, names=("Reaper", "Ana", "Zarya"),
        )
        zarya = run.players[2]
        owner.team = ana_player.team = 0
        zarya.team = bot.team
        ana_player.spawned = True
        self.assertTrue(run.match.switch_hero(ana_player, combat_tests.heroes()[ana.HERO]))
        self.assertTrue(run.match.switch_hero(zarya, combat_tests.heroes()[zarya_barriers.HERO]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=False)
        bot.position = (x, y, z + 10)
        ana_player.mover.position[:] = (x + 2, y, z + 8.5)
        zarya.mover.position[:] = (x - 3, y, z + 10)
        ana_player.mover.body = zarya.mover.body = None
        for _ in range(30):
            run.step({})
        self.engage_heavy_charge(run, owner, bot)
        heading = combat_tests.aim(fight._eye(zarya), (x, y + 1, z + 10))
        run.step({zarya: (*heading, 16)})
        bubble = fight.zarya_bubbles.for_target(bot)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        for _ in range(70):
            run.step({})
            if npc_navigation.charge_motion(bot) is not None:
                break
        self.assertIsNotNone(npc_navigation.charge_motion(bot))
        heavy = fight.vitals[bot.entity]
        before = heavy.total
        for frame in range(30):
            bx, by, bz = bot.position
            heading = combat_tests.aim(fight._eye(ana_player), (bx, by + 1, bz - 0.45))
            run.step({ana_player: (*heading, 8 if frame == 0 else 0)})
            if bubble.health < 200:
                break
        self.assertEqual(bubble.health, 195)
        self.assertEqual(heavy.total, before)
        self.assertNotIn(bot.entity, fight.ana_sleeps)
        self.assertIsNotNone(npc_navigation.charge_motion(bot))
        own = fight.vitals[owner.body]
        for _ in range(65):
            if own.total < 250:
                break
            run.step({})
        self.assertEqual(own.total, 200)

    def test_actual_sleep_dart_releases_carried_mech_before_wall_impact(self):
        from ow174.game import npc_navigation

        run, owner, mech, bot, instance, pool, z = self.heavy_charge_mech(backstop=False, support="Ana")
        caster = run.players[2]
        caster.team = owner.team
        caster.spawned = True
        self.assertTrue(run.match.switch_hero(caster, combat_tests.heroes()[ana.HERO]))
        x, y, _ = mech.mover.position
        self.heavy_charge_world(run, x, y, z, backstop=True, wall_distance=10)
        caster.mover.position[:] = (x + 2, y, z - 3)
        caster.mover.body = None
        fight = run.match.combat
        for _ in range(120):
            run.step({})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual(pool.total, 600)
        for frame in range(30):
            bx, by, bz = bot.position
            heading = combat_tests.aim(fight._eye(caster), (bx, by + 1, bz - 0.45))
            run.step({caster: (*heading, 8 if frame == 0 else 0)})
            if bot.entity in fight.ana_sleeps:
                break
        self.assertIn(bot.entity, fight.ana_sleeps)
        self.assertGreater(mech.mover.position[2], z - 9)
        run.step({})
        self.assertFalse(fight.is_stunned(mech))
        self.assertIsNone(npc_navigation.charge_motion(bot))
        for _ in range(50):
            run.step({})
        impact = instance.states.get(11)
        self.assertFalse(impact and impact.counter)
        self.assertEqual(pool.total, 600)

    def test_actual_heavy_charge_releases_carried_mech_when_reaper_kills_source(self):
        self.check_heavy_charge_releases_on_reaper_kill(button=1)

    def test_actual_blossom_kills_heavy_and_releases_carried_mech(self):
        self.check_heavy_charge_releases_on_reaper_kill(button=32)

    def test_actual_quick_melee_kills_heavy_and_releases_carried_mech(self):
        self.check_heavy_charge_releases_on_reaper_kill(button=0x800)

    def check_heavy_charge_releases_on_reaper_kill(self, *, button):
        run, owner, mech, bot, instance, pool, _ = self.heavy_charge_mech(backstop=True)
        fight = run.match.combat
        before = pool.total
        for _ in range(95):
            run.step({})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        bot_pool = fight.vitals[bot.entity]
        bot_pool.damage(bot_pool.total - 20)
        own = fight.vitals[owner.body]
        own.damage(50)
        before_attack = own.total
        if button == 32:
            owner.body_script.set_ult_charge(2100)
            run.step({owner: (0, 0, 32)})
            self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        else:
            bx, by, bz = bot.position
            heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
            run.step({owner: (*heading, button)})
            for _ in range(12):
                if bot.dead:
                    break
                bx, by, bz = bot.position
                heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                run.step({owner: (*heading, 0)})
        self.assertTrue(bot.dead)
        self.assertEqual(bot_pool.killer, owner.body)
        self.assertAlmostEqual(own.total, before_attack + 20 * 0.35, places=4)
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.is_stunned(mech))
        impact = instance.states.get(11)
        self.assertFalse(impact and impact.counter)
        self.assertEqual(pool.total, before)

    def assassin_projected_pin(self):
        run, owner, ally, bot = self.scene(self.catalog["4"], team_mode=True)
        owner.team = ally.team = 0
        ally.spawned = True
        self.assertTrue(run.match.switch_hero(ally, combat_tests.heroes()[zarya_barriers.HERO]))
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        yaw, pitch = combat_tests.aim(fight._eye(ally), (x, y + 1, z))
        run.step({ally: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)

        self.engage(run, owner, bot)
        for _ in range(90):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(bubble.health, 175)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        return run, owner, bot, bubble

    def test_actual_assassin_pin_routes_contact_damage_to_projected_barrier(self):
        run, owner, bot, bubble = self.assassin_projected_pin()
        fight = run.match.combat
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        bx, by, bz = bot.position
        heading = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (*heading, 1)})
        self.assertEqual(enemy.total, before)

        npc_control.stop(run.match)
        for _ in range(30):
            run.step({})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertIs(fight.zarya_bubbles.for_target(owner), bubble)
        self.assertEqual(bubble.health, 175)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        run.step({owner: (*heading, 1)})
        self.assertLess(enemy.total, before)

    def test_actual_assassin_continuous_pin_breaks_barrier_before_body_damage(self):
        run, owner, bot, bubble = self.assassin_projected_pin()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        for _ in range(80):
            run.step({})
            if bubble.health < 175:
                break
        self.assertLess(bubble.health, 175)
        self.assertEqual(own.total, 250)
        fight.zarya_bubbles.damage(bubble, bubble.health - 1, bot)
        self.assertEqual(bubble.health, 1)
        run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(own.total, 250)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        run.step({})
        self.assertAlmostEqual(own.total, 250 - 100 * combat.TICK, places=4)

    def test_actual_heavy_charge_spends_projected_barrier_and_reaper_returns_fire(self):
        from ow174.game import npc_navigation

        run, owner, ally, bot = self.scene(self.catalog["5"], team_mode=True)
        owner.team = ally.team = 0
        ally.spawned = True
        self.assertTrue(run.match.switch_hero(ally, combat_tests.heroes()[zarya_barriers.HERO]))
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        bot.position = (x, y, z + 6)
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        yaw, pitch = combat_tests.aim(fight._eye(ally), (x, y + 1, z))
        run.step({ally: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.engage_heavy_charge(run, owner, bot)
        instance = npc_navigation.charge_instance(bot)
        for _ in range(90):
            run.step({})
            if bubble.health < 200:
                break
        direct = instance.states.get(53)
        self.assertFalse(direct and direct.counter)
        self.assertEqual(bubble.health, 150)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertIs(fight.zarya_bubbles.for_target(owner), bubble)
        before = fight.vitals[bot.entity].total
        bx, by, bz = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(fight.vitals[bot.entity].total, before)

    def test_actual_heavy_charge_kills_channeling_reaper_and_ends_blossom_damage(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["5"])
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 6)
        fight = run.match.combat
        fight.vitals[owner.body].damage(225)
        owner.body_script.set_ult_charge(2100)
        self.engage_heavy_charge(run, owner, bot)
        for _ in range(70):
            run.step({})
            if npc_navigation.charge_motion(bot) is not None:
                break
        self.assertIsNotNone(npc_navigation.charge_motion(bot))
        initial = fight.vitals[bot.entity].total
        run.step({owner: (0, 0, 32)})
        self.assertLess(fight.vitals[bot.entity].total, initial)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        for _ in range(35):
            run.step({})
            if fight.is_dead(owner):
                break
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(fight.vitals[owner.body].killer, bot.entity)
        after = fight.vitals[bot.entity].total
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.vitals[bot.entity].total, after)
        self.assertIsNone(reaper.blossom_state(owner.body_script))

    def engage_heavy_charge(self, run, owner, bot):
        x, y, z = bot.position
        owner.mover.yaw, owner.mover.pitch = combat_tests.aim(run.match.combat._eye(owner), (x, y + 1, z))
        self.assertEqual(npc_control.start(run.match, owner, charge=True), bot.entity)

    def test_actual_heavy_charge_can_hit_again_after_contact_cooldown(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["5"])
        x, y, z = owner.mover.position
        start = (x, y, z + 6)
        bot.position = start
        fight = run.match.combat
        owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z + 6))
        npc_control.start(run.match, owner, charge=True)
        instance = npc_navigation.charge_instance(bot)
        for _ in range(260):
            run.step({})
            if instance.states[41].remaining() > 14:
                break
        self.assertEqual(fight.vitals[owner.body].total, 200)
        ability = instance.states[41]
        self.assertGreater(ability.remaining(), 14)
        self.assertEqual(instance.vars[59].value(), 0)
        npc_control.stop(run.match)
        run.step({})
        bot.position = start
        npc_control.start(run.match, owner, charge=True)
        for _ in range(20):
            run.step({})
        self.assertEqual(instance.states[71].counter, 1)
        self.assertEqual(bot.position, start)
        npc_control.stop(run.match)
        for _ in range(960):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        owner.mover.position[:] = (x, y, z)
        owner.mover.body = None
        bot.position = start
        npc_control.start(run.match, owner, charge=True)
        for _ in range(85):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertEqual(instance.states[13].counter, 2)

    def test_actual_trooper_and_heavy_melee_hit_once_and_wraith_rejects_them(self):
        for key, damage in (("1", 30), ("5", 80)):
            for wraith in (False, True):
                with self.subTest(npc=self.catalog[key].name, wraith=wraith):
                    run, owner, _, bot = self.scene(self.catalog[key])
                    fight = run.match.combat
                    if wraith:
                        run.step({owner: (0, 0, 8)})
                        self.assertTrue(reaper.intangible(owner))
                    x, y, z = bot.position
                    owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                    self.assertEqual(npc_control.start(run.match, owner, melee=True), bot.entity)
                    graph_id, _, _ = npc_control.MELEE_GRAPHS[bot.hero]
                    instance = next(i for i in bot.body_script.component.instances.values()
                                    if i.graph.index == graph_id)
                    start = tuple(owner.mover.position)
                    peak = start[1]
                    impulse = None
                    with self.assertNoLogs("ow174.game", level="ERROR"):
                        for _ in range(75):
                            run.step({})
                            peak = max(peak, owner.mover.position[1])
                            motion = fight.npc_impulses.get(owner.body)
                            if impulse is None and motion is not None:
                                impulse = motion[1]
                    if key == "5" and not wraith:
                        self.assertEqual(instance.vars[1257].value(), 20)
                        self.assertEqual(instance.vars[1258].value(), 8)
                        self.assertIsNotNone(impulse)
                        self.assertAlmostEqual(impulse[2], -8)
                        self.assertAlmostEqual(impulse[1], 8)
                        self.assertLess(owner.mover.position[2], start[2] - 0.2)
                        self.assertGreater(peak, start[1] + 0.2)
                    else:
                        self.assertIsNone(impulse)
                        self.assertIn(31, instance.component.held)
                        if not wraith:
                            self.assertEqual(instance.vars[1257].value(), 1)
                            self.assertEqual(instance.vars[1258].value(), 0)
                            self.assertAlmostEqual(owner.mover.position[2], start[2])
                    self.assertTrue(any(st.counter and st.node.cls == "STUStatescriptStateAbility"
                                        for st in instance.states.values()))
                    self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 250 - damage)
                    self.assertFalse(any(r.shooter is bot for r in fight.rockets))
                    npc_control.stop(run.match)
                    for _ in range(15):
                        run.step({})
                    self.assertNotIn(31, instance.component.held)
                    self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 250 - damage)

    def test_actual_heavy_melee_ice_wall_clips_horizontal_impulse_without_removing_lift(self):
        for walled in (False, True):
            with self.subTest(walled=walled):
                run, owner, caster, bot, angles = self.ice_wall_scene(
                    "5", wall_distance=-1.1, bot_distance=1.5,
                )
                fight = run.match.combat
                x, y, z = owner.mover.position
                stop_z = z - 0.25
                if walled:
                    self.place_ice_wall(run, caster, angles)
                    piece = min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0] - x))
                    stop_z = piece.feet[2] + piece.depth + owner.mover.data.stand_radius
                bx, by, bz = bot.position
                owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                self.assertEqual(npc_control.start(run.match, owner, melee=True), bot.entity)
                for _ in range(90):
                    run.step({})
                    if owner.body in fight.npc_impulses:
                        break
                self.assertEqual(fight.vitals[owner.body].total, 170)
                pillar_after_swing = piece.health if walled else None
                npc_control.stop(run.match)
                for _ in range(12):
                    run.step({})
                self.assertGreater(owner.mover.position[1], y + 0.2)
                if walled:
                    self.assertAlmostEqual(owner.mover.position[2], stop_z, delta=0.02)
                    self.assertAlmostEqual(fight.npc_impulses[owner.body][1][2], 0, places=4)
                    self.assertEqual(pillar_after_swing, 320)
                    self.assertEqual(piece.health, pillar_after_swing)
                else:
                    self.assertLess(owner.mover.position[2], stop_z - 0.2)

    def test_actual_talon_melee_loses_contact_when_static_cover_interrupts_windup(self):
        for key in ("1", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, _, bot = self.scene(self.catalog[key])
                fight = run.match.combat
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 1.25)
                owner.mover.yaw, owner.mover.pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z + 1.25))
                self.assertEqual(npc_control.start(run.match, owner, melee=True), bot.entity)
                for _ in range(5):
                    run.step({})
                self.assertTrue(bot.attack_input)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                mid = z + 0.6
                lo, hi = x - 100, x + 100
                world = World([
                    (lo,y,z-50,lo,y,z+50,hi,y,z+50),
                    (lo,y,z-50,hi,y,z+50,hi,y,z-50),
                    (lo,y,mid,hi,y,mid,hi,y+5,mid),
                    (lo,y,mid,hi,y+5,mid,lo,y+5,mid),
                    (lo,y,mid,hi,y+5,mid,hi,y,mid),
                    (lo,y,mid,lo,y+5,mid,hi,y+5,mid),
                ], name="cover inserted during melee windup")
                run.match.collision_world = world
                owner.mover.use(world=world)
                for _ in range(70):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_sniper_hits_shadow_windup_but_misses_immune_shadow(self):
        shadow = shadow_tests.ShadowStepTests()
        for protected, windup, health in ((False, 105, 250), (True, 54, 250), (False, 105, 60)):
            with self.subTest(protected=protected, health=health):
                run, owner, _, bot = self.scene(self.catalog["3"])
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 6)
                fight = run.match.combat
                body = owner.body
                own = fight.vitals[body]
                own.damage(250 - health)
                self.engage(run, owner, bot)
                for _ in range(windup):
                    run.step({})
                shadow.select(run, owner)
                shadow.confirm(run, owner)
                record = fight.reaper_shadows[body]
                self.assertIsNotNone(record["target"])
                origin = tuple(owner.mover.position)
                for _ in range(90):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), 1)
                shot = fight.rockets[0]
                self.assertIs(shot.shooter, bot)
                self.assertEqual(shot.direct.amount, 100)
                self.assertEqual(own.total, health)
                for _ in range(10):
                    self.assertEqual(reaper.intangible(owner), protected)
                    self.assertFalse(record["moved"])
                    run.step({})
                    if shot not in fight.rockets or shot.position[2] < z:
                        break
                if protected:
                    self.assertIn(shot, fight.rockets)
                    self.assertLess(shot.position[2], z)
                else:
                    self.assertNotIn(shot, fight.rockets)
                expected_health = health if protected else max(0, health - 100)
                self.assertEqual(own.total, expected_health)
                self.assertEqual(fight.is_dead(owner), expected_health == 0)
                if not expected_health:
                    self.assertEqual(own.killer, bot.entity)
                npc_control.stop(run.match)
                for _ in range(100):
                    run.step({})
                self.assertEqual(record["moved"], expected_health > 0)
                if not expected_health:
                    self.assertNotIn(body, fight.reaper_shadows)
                    self.assertEqual(tuple(owner.mover.position), origin)
                self.assertFalse(reaper.intangible(owner))

    def test_actual_sniper_kills_channeling_reaper_and_ends_blossom_damage(self):
        run, owner, _, bot = self.scene(self.catalog["3"])
        fight = run.match.combat
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 6)
        own = fight.vitals[owner.body]
        own.damage(190)
        owner.body_script.set_ult_charge(2100)
        self.engage(run, owner, bot)
        for _ in range(180):
            run.step({})
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 1)
        shot = fight.rockets[0]
        self.assertIs(shot.shooter, bot)
        self.assertEqual(shot.direct.amount, 100)
        self.assertEqual(own.total, 60)

        enemy = fight.vitals[bot.entity]
        before = enemy.total
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(before - enemy.total, 34)
        self.assertAlmostEqual(own.total, 60 + 34 * 0.35, places=4)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        self.assertIn(shot, fight.rockets)
        for _ in range(10):
            run.step({})
            if fight.is_dead(owner):
                break
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(own.killer, bot.entity)
        self.assertNotIn(shot, fight.rockets)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        for _ in range(math.ceil(3.0 / combat.TICK)):
            run.step({})
        self.assertEqual(before - enemy.total, 34)

    def test_actual_winston_dome_deployed_in_flight_intercepts_sniper_round(self):
        run, owner, ally, bot = self.scene(self.catalog["3"], team_mode=True)
        owner.team = ally.team = 0
        ally.spawned = True
        self.assertTrue(run.match.switch_hero(ally, find_hero("Winston")))
        fight = run.match.combat
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 2, y, z)
        ally.mover.body = None
        bot.position = (x, y, z + 30)
        self.engage(run, owner, bot)
        for _ in range(180):
            run.step({})
            if fight.rockets:
                break
        shot, = fight.rockets
        self.assertIs(shot.shooter, bot)
        self.assertEqual(shot.direct.amount, 100)
        self.assertFalse(fight.winston_bubbles.actors)

        run.step({ally: (0, 0, 16)})
        for _ in range(20):
            if fight.winston_bubbles.actors or shot not in fight.rockets:
                break
            run.step({})
        bubble, = fight.winston_bubbles.actors.values()
        self.assertEqual(bubble.health, 700)
        self.assertIn(shot, fight.rockets)
        for _ in range(20):
            run.step({})
            if shot not in fight.rockets:
                break
        self.assertNotIn(shot, fight.rockets)
        self.assertEqual(bubble.health, 600)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_sniper_round_resolves_winston_dome_at_contact(self):
        for expires_in_flight, delay in ((False, 430), (True, 435)):
            with self.subTest(expires_in_flight=expires_in_flight):
                run, owner, ally, bot = self.scene(self.catalog["3"], team_mode=True)
                owner.team = ally.team = 0
                ally.spawned = True
                self.assertTrue(run.match.switch_hero(ally, find_hero("Winston")))
                fight = run.match.combat
                x, y, z = owner.mover.position
                ally.mover.position[:] = (x + 2, y, z)
                ally.mover.body = None
                bot.position = (x, y, z + 17)
                run.step({ally: (0, 0, 16)})
                for _ in range(45):
                    if fight.winston_bubbles.actors:
                        break
                    run.step({})
                bubble, = fight.winston_bubbles.actors.values()
                deployed = run.frame
                for _ in range(delay):
                    run.step({})
                self.engage(run, owner, bot)
                for _ in range(180):
                    run.step({})
                    if fight.rockets:
                        break
                shot, = fight.rockets
                launched = run.frame
                self.assertFalse(bubble.retired, launched - deployed)
                self.assertEqual(bubble.health, 700)
                own = fight.vitals[owner.body]
                retired_in_flight = False
                for _ in range(12):
                    run.step({})
                    if shot not in fight.rockets:
                        break
                    retired_in_flight |= bubble.retired
                self.assertNotIn(shot, fight.rockets)
                expected = (700, 150) if expires_in_flight else (600, 250)
                self.assertEqual(
                    (bubble.health, own.total), expected, (launched - deployed, run.frame - deployed),
                )
                self.assertEqual(retired_in_flight, expires_in_flight)

    def test_actual_sniper_inside_winston_dome_only_hits_reaper_on_same_side(self):
        for same_side, centre_offset in ((False, 6), (True, 2)):
            with self.subTest(same_side=same_side):
                run, owner, ally, bot = self.scene(self.catalog["3"], team_mode=True)
                owner.team = ally.team = 0
                ally.spawned = True
                self.assertTrue(run.match.switch_hero(ally, find_hero("Winston")))
                fight = run.match.combat
                x, y, z = owner.mover.position
                ally.mover.position[:] = (x + 2, y, z + centre_offset)
                ally.mover.body = None
                bot.position = (x, y, z + centre_offset + 2)
                run.step({ally: (0, 0, 16)})
                for _ in range(45):
                    if fight.winston_bubbles.actors:
                        break
                    run.step({})
                bubble, = fight.winston_bubbles.actors.values()
                self.assertEqual(bubble.health, 700)
                self.engage(run, owner, bot)
                for _ in range(180):
                    run.step({})
                    if fight.rockets:
                        break
                shot, = fight.rockets
                self.assertIs(shot.shooter, bot)
                self.assertEqual(shot.direct.amount, 100)
                radius = 5
                self.assertLess(math.dist(shot.position, bubble.centre), radius)
                self.assertEqual(math.dist(fight._eye(owner), bubble.centre) < radius, same_side)
                own = fight.vitals[owner.body]
                self.assertEqual(own.total, 250)
                for _ in range(20):
                    run.step({})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual((bubble.health, own.total), (700, 150) if same_side else (600, 250))

    def test_actual_sniper_breaks_allied_winston_dome_without_body_spill(self):
        run, owner, ally, bot = self.scene(self.catalog["3"], team_mode=True)
        owner.team = ally.team = 0
        ally.spawned = True
        self.assertTrue(run.match.switch_hero(ally, find_hero("Winston")))
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 2, y, z)
        ally.mover.body = None
        bot.position = (x, y, z + 7)
        fight = run.match.combat
        run.step({ally: (0, 0, 16)})
        for _ in range(45):
            if fight.winston_bubbles.actors:
                break
            run.step({})
        bubble, = fight.winston_bubbles.actors.values()
        self.assertEqual(bubble.health, 700)

        self.engage(run, owner, bot)
        bot.autonomous = True
        own = fight.vitals[owner.body]
        for _ in range(180):
            run.step({})
            if bubble.health < 700:
                break
        self.assertEqual(bubble.health, 600)
        self.assertEqual(own.total, 250)
        fight.winston_bubbles.damage(bubble, 520)
        self.assertEqual(bubble.health, 80)
        for _ in range(180):
            run.step({})
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertEqual(own.total, 250)
        for _ in range(180):
            run.step({})
            if own.total < 250:
                break
        self.assertEqual(own.total, 150)

    def test_actual_sniper_breaks_projected_barrier_before_next_shot_hits_reaper(self):
        run, owner, ally, bot = self.scene(self.catalog["3"], team_mode=True)
        owner.team = ally.team = 0
        ally.spawned = True
        self.assertTrue(run.match.switch_hero(ally, combat_tests.heroes()[zarya_barriers.HERO]))
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        self.engage(run, owner, bot)
        bot.autonomous = True
        # Let the Sniper wind up so the projection is still active at impact.
        for _ in range(85):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 250)
        yaw, pitch = combat_tests.aim(fight._eye(ally), (x, y + 1, z))
        run.step({ally: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        fight.zarya_bubbles.damage(bubble, 120, bot)
        self.assertEqual(bubble.health, 80)

        for _ in range(120):
            run.step({})
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(180):
            run.step({})
            if fight.vitals[owner.body].total < 250:
                break
        self.assertEqual(fight.vitals[owner.body].total, 150)

    def test_actual_sniper_waits_then_repositions_and_exchanges_fire_with_reaper(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["3"])
        run.match.game_map = replace(run.match.game_map, map_guid=npc_navigation.RIALTO_MAP)
        fight = run.match.combat
        start = bot.position
        self.engage(run, owner, bot)
        move = npc_navigation.reposition_state(bot)
        self.assertIsNotNone(move)
        wait = npc_navigation.reposition_wait(move)
        with self.assertNoLogs("ow174.game", level="ERROR"):
            for _ in range(20):
                run.step({})
            self.assertTrue(wait.active)
            self.assertFalse(move.active)
            self.assertEqual(bot.position, start)
            for _ in range(25):
                run.step({})
                if move.active:
                    break
            self.assertFalse(wait.active)
            self.assertTrue(move.active)
            destination, speed = move.move_request()
            self.assertEqual(speed, 20)
            self.assertAlmostEqual(math.dist(start, destination), 4, delta=0.05)
            for _ in range(40):
                run.step({})
                if not move.active:
                    break
            self.assertFalse(move.active)
            self.assertLess(math.dist(bot.position, destination), 0.1)
            self.assertTrue(npc_navigation.repositioning(bot))
            self.assertFalse(bot.attack_input)
            self.assertEqual(fight.vitals[owner.body].total, 250)
            for _ in range(180):
                if fight.vitals[owner.body].total < 250:
                    break
                run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 150)
        npc_control.stop(run.match)
        before = fight.vitals[bot.entity].total
        x, y, z = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(fight.vitals[bot.entity].total, before)

    def test_stop_during_actual_sniper_reposition_wait_prevents_move_and_fire(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["3"])
        run.match.game_map = replace(run.match.game_map, map_guid=npc_navigation.RIALTO_MAP)
        fight = run.match.combat
        self.engage(run, owner, bot)
        for _ in range(12):
            run.step({})
        move = npc_navigation.reposition_state(bot)
        wait = npc_navigation.reposition_wait(move)
        self.assertTrue(wait.active)
        start = bot.position
        npc_control.stop(run.match)
        for _ in range(75):
            run.step({})
        self.assertFalse(wait.active)
        self.assertEqual(move.counter, 0)
        self.assertEqual(bot.position, start)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_new_static_wall_aborts_actual_sniper_move_before_crossing(self):
        from ow174.game import npc_navigation

        run, owner, _, bot = self.scene(self.catalog["3"])
        run.match.game_map = replace(run.match.game_map, map_guid=npc_navigation.RIALTO_MAP)
        fight = run.match.combat
        self.engage(run, owner, bot)
        move = npc_navigation.reposition_state(bot)
        for _ in range(50):
            run.step({})
            if move.active:
                break
        self.assertTrue(move.active)
        x, y, z = bot.position
        mid = z + 1
        lo, hi = x - 20, x + 20
        wall = World([
            (lo,y,z-20,lo,y,z+20,hi,y,z+20),
            (lo,y,z-20,hi,y,z+20,hi,y,z-20),
            (lo,y,mid,hi,y,mid,hi,y+5,mid),
            (lo,y,mid,hi,y+5,mid,lo,y+5,mid),
            (lo,y,mid,hi,y+5,mid,hi,y,mid),
            (lo,y,mid,lo,y+5,mid,hi,y+5,mid),
        ], name="cover inserted after sniper starts moving")
        run.match.collision_world = wall
        owner.mover.use(world=wall)
        for _ in range(30):
            run.step({})
            self.assertLess(bot.position[2], mid)
            if not move.active:
                break
        self.assertFalse(move.active)
        self.assertEqual(move.counter, 1)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_four_actual_ranged_npcs_seek_line_of_sight_and_exchange_fire_with_reaper(self):
        # Enforcer's first burst applies 30 here, versus 50 in the stationary fixture.
        for key, first_damage in (("1", 20), ("2", 30), ("3", 100), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, _, bot = self.scene(self.catalog[key])
                fight = run.match.combat
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 5)
                self.engage(run, owner, bot)
                mid = z + 2.5
                left, right = x - 2, x + 2
                world = World([
                    (x-50,y,z-50,x-50,y,z+50,x+50,y,z+50),
                    (x-50,y,z-50,x+50,y,z+50,x+50,y,z-50),
                    (left,y,mid,right,y,mid,right,y+5,mid),
                    (left,y,mid,right,y+5,mid,left,y+5,mid),
                    (left,y,mid,right,y+5,mid,right,y,mid),
                    (left,y,mid,left,y+5,mid,right,y+5,mid),
                ], name="finite wall appearing after engagement")
                run.match.collision_world = world
                owner.mover.use(world=world)
                start = bot.position
                for _ in range(10):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertFalse(bot.attack_input)
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    for _ in range(340):
                        run.step({})
                        if fight.vitals[owner.body].total < 250:
                            break
                self.assertGreater(abs(bot.position[0] - start[0]), 1.5)
                self.assertEqual(fight.vitals[owner.body].total, 250 - first_damage)
                npc_control.stop(run.match)
                stopped = bot.position
                npc_vitals = fight.vitals[bot.entity]
                before = npc_vitals.total
                bx, by, bz = stopped
                yaw, pitch = combat_tests.aim(fight._eye(owner), (bx, by + 1, bz))
                run.step({owner: (yaw, pitch, 1)})
                self.assertLess(npc_vitals.total, before)
                own_health = fight.vitals[owner.body].total
                expected = min(250, 250 - first_damage + 0.35 * (before - npc_vitals.total))
                self.assertAlmostEqual(own_health, expected, places=4)
                for _ in range(30):
                    run.step({})
                self.assertEqual(bot.position, stopped)
                self.assertFalse(bot.attack_input)
                self.assertEqual(fight.vitals[owner.body].total, own_health)

    def test_four_actual_ranged_scripts_damage_reaper_and_stop_without_late_damage(self):
        # Scoped manual Enforcer added; Assassin/autonomous AI remain OPEN.
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, _, bot = self.scene(self.catalog[key])
                fight = run.match.combat
                self.engage(run, owner, bot)
                initial = fight.vitals[owner.body].total
                with patch.object(combat.log, "exception") as errors:
                    for _ in range(220):
                        run.step({})
                        if fight.vitals[owner.body].total < initial:
                            break
                    self.assertFalse(errors.called)
                after = fight.vitals[owner.body].total
                self.assertGreater(after, 0)
                self.assertLess(after, initial)
                self.assertEqual(initial - after, {"1": 20, "2": 50, "3": 100, "5": 5}[key])
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(any(state.counter for instance in bot.body_script.component.instances.values()
                                    for state in instance.states.values()
                                    if state.node.cls == "STUStatescriptStateWeaponVolley"))
                npc_control.stop(run.match)
                for _ in range(80):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, after)
                self.assertFalse(bot.attack_input)

    def test_four_actual_ranged_npcs_can_kill_reaper_with_correct_attribution(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, _, bot = self.scene(self.catalog[key])
                fight = run.match.combat
                self.engage(run, owner, bot)
                for frame in range(1200):
                    run.step({})
                    if fight.is_dead(owner):
                        break
                    if key == "3" and frame % 220 == 219:
                        # The authored sniper weapon is a single shot per press.
                        # Reconstruct manual trigger release, not autonomous AI.
                        npc_control.stop(run.match)
                        for _ in range(10):
                            run.step({})
                        self.engage(run, owner, bot)
                self.assertTrue(fight.is_dead(owner))
                self.assertTrue(owner.body_script.component.owner_dead)
                self.assertEqual(fight.vitals[owner.body].total, 0)
                self.assertEqual(owner.client.messages(combat.DEATH_NOTICE)[-1],
                                 {"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": bot.entity}})
                self.assertFalse(bot.dead)

    def test_four_actual_ranged_scripts_fire_but_cannot_damage_wraith(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, _, bot = self.scene(self.catalog[key])
                fight = run.match.combat
                run.step({owner: (0, 0, 8)})
                self.engage(run, owner, bot)
                with patch.object(combat.log, "exception") as errors:
                    for _ in range(170):
                        run.step({})
                    self.assertFalse(errors.called)
                self.assertTrue(reaper.intangible(owner))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(any(state.counter for instance in bot.body_script.component.instances.values()
                                    for state in instance.states.values()
                                    if state.node.cls == "STUStatescriptStateWeaponVolley"))

    def test_actual_dart_sleeps_all_five_real_bodies_and_removal_clears_control(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, _, bot = self.scene(definition, team_mode=True)
                position = tuple(owner.mover.position)
                run.match.switch_hero(owner, combat_tests.heroes()[ana.HERO])
                owner.mover.position[:] = position
                owner.mover.body = None
                fight = run.match.combat
                for _ in range(20):
                    run.step({})
                before = fight.vitals[bot.entity].total
                x, y, z = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 8)})
                for _ in range(40):
                    run.step({owner: (yaw, pitch, 0)})
                self.assertEqual(fight.vitals[bot.entity].total, before - 5)
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(fight.ana_sleeps[bot.entity]["end"]
                                 - fight.ana_sleeps[bot.entity]["start"], 313)
                npc.remove_spawned(run.match)
                self.assertNotIn(bot.entity, fight.ana_sleeps)

    def test_sleep_blocks_actual_trooper_manual_fire_then_world_expiry_recovers(self):
        run, owner, _, bot = self.scene(self.catalog["1"], team_mode=True)
        position = tuple(owner.mover.position)
        run.match.switch_hero(owner, combat_tests.heroes()[ana.HERO])
        owner.mover.position[:] = position
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        x, y, z = bot.position
        yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (yaw, pitch, 8)})
        for _ in range(40):
            run.step({owner: (yaw, pitch, 0)})
        deadline = fight.ana_sleeps[bot.entity]["end"]
        run.match.switch_hero(owner, combat_tests.heroes()[reaper.HERO])
        owner.mover.position[:] = position
        owner.mover.body = None
        for _ in range(20):
            run.step({})
        self.engage(run, owner, bot)
        fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))
        before = fight.vitals[owner.body].total
        for _ in range(50):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, before)
        self.assertFalse(bot.attack_input)
        run.frame = deadline
        run.match.update(deadline * combat.TICK, deadline, frames=False)
        for _ in range(100):
            run.step({})
            if fight.vitals[owner.body].total < before:
                break
        self.assertEqual(fight.vitals[owner.body].total, before - 20)
        self.assertTrue(bot.attack_input)


    def test_actual_nano_modifies_reaper_exchange_with_all_five_npcs(self):
        import test_reaper_nano as nano_tests

        from ow174.game import nano
        from ow174.game.content import heroes

        for key, damage in (("1", 20), ("2", 50), ("3", 100), ("4", 25), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, caster, bot = self.scene(self.catalog[key], team_mode=True)
                caster.spawned = True
                self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
                owner.team = caster.team = 0
                x, y, z = owner.mover.position
                caster.mover.position[:] = (x - 4, y, z)
                caster.mover.body = None
                caster.body_script.ult_seconds = 0
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                own = fight.vitals[owner.body]
                nano_tests.ReaperNanoTests().cast(run, caster, owner)
                self.assertTrue(nano.active(fight, own))
                self.engage(run, owner, bot)
                for _ in range(160):
                    run.step({})
                    if own.total < 250:
                        break
                self.assertAlmostEqual(own.total, 250 - damage / 2)
                npc_control.stop(run.match)
                own.damage(100)
                pool = fight.vitals[bot.entity]
                before = pool.total
                x, y, z = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 1)})
                self.assertAlmostEqual(pool.total, before - 180)
                self.assertAlmostEqual(own.total, 150 - damage / 2 + 63, places=4)


    def test_actual_discord_scales_reaper_exchanges_with_all_five_npcs(self):
        import test_reaper_discord as discord_tests

        from ow174.game import discord
        from ow174.game.content import heroes

        for key, damage in (("1", 20), ("2", 50), ("3", 100), ("4", 25), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, caster, bot = self.scene(self.catalog[key], team_mode=True)
                caster.spawned = True
                self.assertTrue(run.match.switch_hero(caster, heroes()[discord.HERO]))
                owner.team, caster.team = 0, 1
                x, y, z = owner.mover.position
                caster.mover.position[:] = (x - 4, y, z)
                caster.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                own = fight.vitals[owner.body]
                discord_tests.ReaperDiscordTests().cast(run, caster, owner)
                self.assertEqual(fight.discord.scale(owner.body), 1.25)
                self.engage(run, owner, bot)
                for _ in range(160):
                    run.step({})
                    if own.total < 250:
                        break
                self.assertAlmostEqual(own.total, 250 - damage * 1.25)
                npc_control.stop(run.match)
                caster.team = owner.team
                for _ in range(50):
                    run.step({})
                self.assertEqual(fight.discord.scale(owner.body), 1)
                x, y, z = bot.position
                angles = combat_tests.aim(fight._eye(caster), (x, y + combat.EYE, z))
                run.step({caster: (*angles, 8)})
                self.assertEqual(fight.discord.active[caster.body].target, bot.entity)
                own.damage(100)
                pool = fight.vitals[bot.entity]
                before = pool.total
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 1)})
                self.assertAlmostEqual(pool.total, before - 150)
                self.assertAlmostEqual(own.total, 150 - damage * 1.25 + 52.5, places=4)


    def test_actual_amplification_matrix_scales_reaper_exchanges_with_all_five_npcs(self):
        import test_reaper_amplification as matrix_tests

        from ow174.game import amplification
        from ow174.game.content import heroes

        for key, damage in (("1", 40), ("2", 100), ("3", 200), ("4", 25), ("5", 10)):
            with self.subTest(npc=self.catalog[key].name):
                run, owner, caster, bot = self.scene(self.catalog[key], team_mode=True)
                caster.spawned = True
                self.assertTrue(run.match.switch_hero(caster, heroes()[amplification.HERO]))
                owner.team, caster.team = 0, 1
                x, y, z = owner.mover.position
                caster.mover.position[:] = (x + 2, y, z - 0.75)
                caster.mover.body = None
                caster.body_script.ult_seconds = 0
                for _ in range(25):
                    run.step({})
                fixture = matrix_tests.ReaperAmplificationTests()
                window = fixture.window(run, caster)
                self.assertLess(z, window.position[2])
                self.assertLess(window.position[2], bot.position[2])
                fight = run.match.combat
                own = fight.vitals[owner.body]
                self.engage(run, owner, bot)
                for _ in range(160):
                    run.step({})
                    if own.total < 250:
                        break
                self.assertAlmostEqual(own.total, 250 - damage)
                npc_control.stop(run.match)
                caster.team = owner.team
                fixture.deploy(run, caster)
                self.assertEqual(len(fight.amplification.windows), 2)
                own.damage(min(100, own.total - 50))
                prior = own.total
                pool = fight.vitals[bot.entity]
                before = pool.total
                x, y, z = bot.position
                yaw, pitch = combat_tests.aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 1)})
                dealt = min(before, 240)
                self.assertAlmostEqual(pool.total, before - dealt)
                self.assertAlmostEqual(own.total, prior + dealt * 0.35, places=4)


if __name__ == "__main__":
    unittest.main()
