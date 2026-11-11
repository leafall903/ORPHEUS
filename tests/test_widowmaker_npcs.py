"""Optional actual-body NPC checks with an explicitly supplied external bundle.

ORPHEUS_NPC_TEST_DATA must name the prepared data directory. This is synthetic
collision, acknowledged adapter transport and permissive body VM evidence. It
does not certify authored AI, difficulty pools, specials or native hitboxes.
"""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import FakeClient, aim, in_world
from test_game_mover import box
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch
from test_widowmaker_deflect import GENJI

from ow174.game import combat, dva_body, npc, npc_control, sigma, zarya
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph
from ow174.game.script.expr import Entity
from ow174.game.world import SHIELDS_POOL

DATA = os.environ.get("ORPHEUS_NPC_TEST_DATA")


def _clear_graph_cache():
    for function in (graph._data, graph._records, graph.graph):
        function.cache_clear()


class _NpcPeer(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.bot_deaths = []

    def queue_bot_death(self, entity, snapshot):
        self.bot_deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.bot_deaths.append((entity, snapshot))

    def bot_stream(self, entity):
        return None  # explicit fixture boundary: native projectile observer transport is not exercised


class _ActualNpcMatch(_WidowmakerMatch):
    @classmethod
    def setUpClass(cls):
        directory = Path(DATA)
        cls.catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        cls.bundle_patch = patch.object(graph, "GRAPHS_PATH", directory / "game_graphs_174.json.gz")
        cls.bundle_patch.start()
        _clear_graph_cache()
        cls.addClassCleanup(cls._restore_bundle)

    @classmethod
    def _restore_bundle(cls):
        cls.bundle_patch.stop()
        _clear_graph_cache()

    def spawn(self, definition, position=(0, 0, 1.5)):
        self.b.team = self.a.team
        self.a.client, self.b.client = _NpcPeer(self.a), _NpcPeer(self.b)
        bot = npc.spawn(self.run.match, definition, position, team=1)
        self.assertIsNotNone(bot.body_script, definition.name)
        self.assertTrue(bot.retain_corpse)
        return bot



@unittest.skipUnless(DATA, "set ORPHEUS_NPC_TEST_DATA to run actual NPC body fixtures")
class WidowmakerActualNpcTests(_ActualNpcMatch, unittest.TestCase):
    def test_player_world_reads_an_actual_npc_body_variable(self):
        bot = self.spawn(next(iter(self.catalog.values())))
        target = bot.body_script.component
        owner = self.a.body_script.world
        target.set(28985, True)

        self.assertTrue(owner.script_component_present(bot.entity))
        self.assertIs(owner.entity_var(bot.entity, 28985), True)

    def test_each_actual_npc_uses_explicit_extracted_mover_dimensions(self):
        expected = {
            0x1D40: (1.7, 0.5, 5.75),
            0x2279: (1.9, 0.5, 5.0),
            0x1E7D: (1.75, 0.5, 4.5),
            0x1EBD: (1.5, 0.5, 14.0),
            0x1EBB: (2.3, 0.7, 2.2),
        }
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                data = defined_mover_data(definition.body)
                self.assertIsNotNone(data, "actual NPC acceptance requires explicit mover inputs")
                self.assertEqual((data.stand_height, data.stand_radius, data.run_forward),
                                 expected[definition.body & 0xFFFF])
                bot = self.spawn(definition)
                target = self.target(bot)
                self.assertEqual((target.height, target.radius),
                                 (data.stand_height, data.stand_radius))

    def test_five_actual_bodies_take_scoped_damage_through_authored_base_pools(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                vitals = self.run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.press(SCOPE, 100)
                self.press(SCOPE | FIRE)
                self.assertAlmostEqual(before - vitals.total, 120)
                if definition.shields:
                    self.assertEqual(vitals.now[SHIELDS_POOL], max(0, definition.shields - 120))
                self.assertEqual(self.a.client.messages(combat.HIT)[-1]["+0x78"]["+0x0"],
                                 {"+0x0": bot.entity})

    def test_scoped_falloff_and_range_against_each_actual_npc(self):
        for definition in self.catalog.values():
            for distance, damage in ((150, 36), (450, 0)):
                with self.subTest(npc=definition.name, distance=distance):
                    self.setUp()
                    bot = self.spawn(definition, (0, 0, distance))
                    script = self.a.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x1B)
                    before = self.health(bot)
                    ammo = weapon.vars[53].value()
                    angles = aim(self.run.match.combat._eye(self.a), (0, 1, distance))

                    for _ in range(100):
                        self.run.step({self.a: (*angles, SCOPE)})
                    self.run.step({self.a: (*angles, SCOPE | FIRE)})

                    self.assertAlmostEqual(before - self.health(bot), damage, places=4)
                    self.assertAlmostEqual(script.component.vars[342].value(), damage, places=4)
                    self.assertEqual(weapon.vars[53].value(), ammo - 5)
                    hits = self.a.client.messages(combat.HIT)
                    self.assertEqual(len(hits), int(damage > 0))
                    if hits:
                        self.assertEqual(hits[0]["+0x78"]["+0x0"], {"+0x0": bot.entity})

    def test_both_weapon_modes_critical_hit_each_actual_npc(self):
        for definition in self.catalog.values():
            for buttons, damage in ((FIRE, 26), (SCOPE | FIRE, 300)):
                with self.subTest(npc=definition.name, buttons=buttons):
                    self.setUp()
                    bot = self.spawn(definition)
                    target = self.target(bot)
                    vitals = self.run.match.combat.vitals[bot.entity]
                    before = vitals.total
                    height = target.eye - combat.HEAD_BELOW / 2
                    if buttons & SCOPE:
                        self.press(SCOPE, 100, height=height)
                    self.press(buttons, height=height)
                    expected = min(damage, before)
                    self.assertAlmostEqual(before - vitals.total, expected)
                    hit = self.a.client.messages(combat.HIT)[-1]["+0x78"]
                    self.assertEqual(hit["+0x0"], {"+0x0": bot.entity})
                    self.assertEqual(hit["+0x4"], round(expected))
                    self.assertEqual(hit["+0x8"], combat.CRITICAL | (combat.KILLING if vitals.dead else 0))

    def test_static_wall_blocks_both_weapon_modes_and_melee_against_each_actual_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                self.run.match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (-50, 0, 0.75, 50, 0, 0.75, 50, 50, 0.75),
                    (-50, 0, 0.75, 50, 50, 0.75, -50, 50, 0.75),
                ], name="actual NPC occlusion")
                before = self.run.match.combat.vitals[bot.entity].total
                self.press(FIRE)
                self.press(SCOPE, 100)
                self.press(SCOPE | FIRE)
                self.press(0, 30)
                self.press(0x800)
                self.press(0, 40)
                self.assertEqual(self.run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_actual_grapple_ignores_each_npc_as_anchor_and_reaches_a_wall_behind_it(self):
        for definition in self.catalog.values():
            for has_wall in (False, True):
                with self.subTest(npc=definition.name, wall=has_wall):
                    self.setUp()
                    bot = self.spawn(definition, (0, 0, 2.5))
                    fight = self.run.match.combat
                    vitals = fight.vitals_of(self.target(bot))
                    before = bot.position, vitals.total
                    triangles = box(-20, 20, -1, 0, -20, 25)
                    if has_wall:
                        triangles += box(-8, 8, 0, 6, 6, 6.4)
                    world = self.run.match.collision_world = World(triangles)
                    self.a.mover.use(world=world)
                    hook = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0x005A)
                    origin = fight._eye(self.a)
                    angles = aim(origin, (0, 1.35, 2.5))
                    target = self.target(bot)
                    distance = combat.ray_capsule(
                        origin, combat.aim(*angles), *target.segment(), target.radius,
                    )
                    self.assertIsNotNone(distance)
                    self.assertLess(distance, 6)
                    self.run.step({self.a: (*angles, 8)})
                    for _ in range(40):
                        if hook.state(0).cast is not None:
                            break
                        self.run.step({self.a: (*angles, 0)})
                    self.assertIsNotNone(hook.state(0).cast)
                    cast, _ = hook.state(0).cast
                    self.assertEqual(cast.hit, has_wall)
                    self.assertIsNone(cast.entity)
                    if has_wall:
                        self.assertAlmostEqual(cast.point.z, 6)
                    for _ in range(110):
                        self.run.step({})
                    if has_wall:
                        self.assertGreater(self.a.mover.position[2], 5)
                        self.assertLess(self.a.mover.position[2], 6)
                        self.assertGreater(hook.vars[61].value(), 9)
                    else:
                        self.assertAlmostEqual(self.a.mover.position[2], 0)
                    self.assertFalse(hook.state(1).active)
                    self.assertEqual((bot.position, vitals.total), before)
                    self.assertFalse(fight.captured(bot.entity))
                    self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_five_actual_bodies_take_mine_poison_and_leave_retained_native_script_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                fight = self.run.match.combat
                before = fight.vitals[bot.entity].total
                self.press(0x10)
                self.press(0, 130)
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                self.press(0, 400)
                self.assertAlmostEqual(before - fight.vitals[bot.entity].total, 75, places=5)
                self.assertFalse(fight.revealed_to(self.a, bot.entity))
                # Exercise the native-script terminal lifecycle independently
                # of the above real weapon damage; not a weapon-lethality claim.
                fight._damage(self.a, self.target(bot), before, False, None, self.run.frame)
                self.assertTrue(bot.dead)
                self.assertIsNotNone(bot.body_script)
                self.press(0, 220)
                self.assertIn(bot, self.run.match.bots)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertEqual(fight.vitals[bot.entity].total, 0)

    def test_armed_mine_requires_range_and_clear_sight_for_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition, (0, 0, 20))
                match = self.run.match
                fight = match.combat
                self.a.body_script.ult_seconds = 0
                self.a.body_script.set_ult_charge(0)
                before = self.health(bot)
                self.press(0x10)
                self.press(0, 130)
                self.assertEqual(len(fight.venom.mines), 1)
                mine = fight.venom.mines[0]
                self.assertGreaterEqual(match.tick, mine.ready)
                self.assertEqual(fight.venom.poison, [])
                self.assertEqual(self.health(bot), before)

                x, _, z = mine.position
                bot.position = (x, 0, z + 1.5)
                wall = z + 0.75
                clear_world = match.collision_world
                match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (-5, 0, wall, 5, 0, wall, 5, 3, wall),
                    (-5, 0, wall, 5, 3, wall, -5, 3, wall),
                ], name="NPC mine trigger occlusion")
                self.press(0, 30)
                self.assertEqual(fight.venom.mines, [mine])
                self.assertEqual(fight.venom.poison, [])
                self.assertFalse(fight.revealed_to(self.a, bot.entity))
                self.assertEqual(self.health(bot), before)
                self.assertEqual(self.a.body_script.component.vars[342].value(), 0)

                match.collision_world = clear_world
                self.press(0, 30)
                self.assertEqual(fight.venom.mines, [])
                self.assertTrue(fight.venom.poisoned_by(self.a, bot.entity))
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                self.press(0, 320)
                self.assertEqual(fight.venom.poison, [])
                self.assertFalse(fight.revealed_to(self.a, bot.entity))
                self.assertAlmostEqual(before - self.health(bot), 75, places=5)
                self.assertAlmostEqual(self.a.body_script.component.vars[342].value(), 75, places=5)

    def test_four_npc_weapons_destroy_deployed_venom_mine(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                match = self.run.match
                fight = match.combat
                bot = self.spawn(definition, (0, 0, 9))
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 9))
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                clear_world = match.collision_world
                match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (-5, 0, 3, 5, 0, 3, 5, 3, 3),
                    (-5, 0, 3, 5, 3, 3, -5, 3, 3),
                ], name="NPC mine attachment fixture")
                self.run.step({self.a: (0, -4096, 0x10)})
                for _ in range(30):
                    self.run.step({self.a: (0, -4096, 0)})
                self.assertEqual(len(fight.venom.mines), 1)
                mine = fight.venom.mines[0]
                self.assertEqual(mine.health, 1)

                # Put the deployed mine on the NPC's aim line to Widowmaker.
                x, y, z = mine.position
                eye = fight._eye(self.a)
                scale = 9 / (z - eye[2])
                bot.position = (eye[0] + (x - eye[0]) * scale,
                                eye[1] + (y - eye[1]) * scale - combat.EYE, eye[2] + 9)
                match.collision_world = clear_world
                before = self.health(bot)
                for _ in range(210):
                    self.run.step({})
                    if mine not in fight.venom.mines:
                        break
                self.assertNotIn(mine, fight.venom.mines)
                self.assertLessEqual(mine.health, 0)
                self.assertEqual(fight.venom.poison, [])
                self.assertEqual(self.health(bot), before)

    def test_melee_range_and_charge_against_each_actual_npc(self):
        for definition in self.catalog.values():
            for distance, damage in ((2.7, 30), (3.4, 0)):
                with self.subTest(npc=definition.name, distance=distance):
                    self.setUp()
                    bot = self.spawn(definition, (0, 0, distance))
                    script = self.a.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    before = self.health(bot)
                    angles = aim(self.run.match.combat._eye(self.a), (0, 1, distance))

                    self.run.step({self.a: (*angles, MELEE)})
                    for _ in range(40):
                        self.run.step({self.a: (*angles, 0)})

                    self.assertEqual(before - self.health(bot), damage)
                    self.assertEqual(script.component.vars[342].value(), damage)
                    hits = self.a.client.messages(combat.HIT)
                    self.assertEqual(len(hits), int(damage > 0))
                    if hits:
                        self.assertEqual(hits[0]["+0x78"]["+0x0"], {"+0x0": bot.entity})

    def test_five_actual_bodies_take_melee_then_weapon_lethal_damage_and_remain_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                fight = self.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.press(0x800)
                self.press(0, 40)
                self.assertAlmostEqual(before - vitals.total, 30)
                # Real scoped input, ammo exhaustion and authored reloads. Do
                # not inject terminal damage or replenish weapon variables.
                damaging_shots = 0
                for _ in range(80):
                    if bot.dead:
                        break
                    self.press(SCOPE, 150)
                    previous = vitals.total
                    self.press(SCOPE | FIRE)
                    if vitals.total < previous:
                        damaging_shots += 1
                        # Reload can consume part of the held-scope interval,
                        # so the first shot afterwards may be partly charged.
                        dealt = previous - vitals.total
                        self.assertGreaterEqual(dealt + 1e-6, min(12, previous))
                        self.assertLessEqual(dealt, min(120, previous) + 1e-6)
                self.assertTrue(bot.dead)
                self.assertGreater(damaging_shots, 0)
                self.assertEqual(vitals.total, 0)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.press(0, 220)
                self.assertIn(bot, self.run.match.bots)
                self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_all_five_actual_attack_graph_inputs_damage_widowmaker(self):
        # Enforcer uses the 25-pellet authority; Assassin uses the manual1157 bridge.
        for key in ("1", "2", "3", "4", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                self.a.client, self.b.client = _NpcPeer(self.a), _NpcPeer(self.b)
                distance = 2.5 if key == '4' else 3  # inside the explicit three-dimensional pin reach
                bot = npc.spawn(self.run.match, definition, (0, 0, distance), team=1, yaw=32768)
                self.assertIsNotNone(bot.body_script)
                self.a.mover.yaw, self.a.mover.pitch = aim(self.run.match.combat._eye(self.a),
                                                       (0, 1, distance))
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                before = self.health(self.a)
                for _ in range(180):
                    self.run.step({})
                self.assertLess(self.health(self.a), before)
                self.assertIsNotNone(bot.body_script)
                if key == '4':
                    self.assertEqual(self.health(self.a), 0)
                    self.assertNotIn(bot.entity, self.run.match.combat.pins)

    def test_each_actual_npc_rechecks_team_before_attacking_selected_widowmaker(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                distance = 2.5 if key == "4" else 3
                bot = self.spawn(definition, (0, 0, distance))
                fight = self.run.match.combat
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, distance))
                enemy_team = bot.team
                self.assertNotEqual(self.a.team, enemy_team)
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                bot.team = self.a.team
                for _ in range(120):
                    self.run.step({})
                self.assertEqual(self.health(self.a), 175)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(bot.entity, fight.pins)

                bot.team = enemy_team
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertLess(self.health(self.a), 175)

    def test_static_wall_releases_all_five_selected_npc_attacks(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                match = self.run.match
                distance = 2.5
                bot = self.spawn(definition, (0, 0, distance))
                self.a.mover.yaw, self.a.mover.pitch = aim(match.combat._eye(self.a), (0, 1, distance))
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                self.run.step({})
                self.assertTrue(bot.attack_input)

                clear_world = match.collision_world
                match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (-5, 0, 1.25, 5, 0, 1.25, 5, 3, 1.25),
                    (-5, 0, 1.25, 5, 3, 1.25, -5, 3, 1.25),
                ], name="reverse NPC occlusion")
                health = self.health(self.a)
                for _ in range(120):
                    self.run.step({})
                self.assertEqual(self.health(self.a), health)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
                self.assertNotIn(bot.entity, match.combat.pins)

                match.collision_world = clear_world
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < health:
                        break
                self.assertTrue(bot.attack_input)
                self.assertLess(self.health(self.a), health)

    def test_all_five_selected_npc_attacks_release_out_of_range_and_resume(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                match = self.run.match
                bot = self.spawn(definition, (0, 0, 2.5))
                self.a.mover.yaw, self.a.mover.pitch = aim(match.combat._eye(self.a), (0, 1, 2.5))
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                self.run.step({})
                self.assertTrue(bot.attack_input)

                near = bot.position
                # Move the fixture body beyond the manual pin or weapon controller reach.
                bot.position = (0, 0, 3.5 if key == "4" else 101)
                before = self.health(self.a)
                for _ in range(120):
                    self.run.step({})
                self.assertEqual(self.health(self.a), before)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
                self.assertNotIn(bot.entity, match.combat.pins)

                bot.position = near
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < before:
                        break
                self.assertTrue(bot.attack_input)
                self.assertLess(self.health(self.a), before)

    def test_all_five_npc_attacks_release_on_death_and_resume_on_respawn(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                match = self.run.match
                bot = self.spawn(definition, (0, 0, 2.5))
                self.a.mover.yaw, self.a.mover.pitch = aim(match.combat._eye(self.a), (0, 1, 2.5))
                self.assertEqual(self.health(self.a), 175)
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                self.run.step({})
                self.assertTrue(bot.attack_input)

                body = self.a.body
                original_position = self.a.mover.position[:]
                for _ in range(1200):
                    self.run.step({})
                    if match.combat.is_dead(self.a):
                        break
                self.assertTrue(match.combat.is_dead(self.a))
                self.assertEqual(self.health(self.a), 0)
                for _ in range(round(combat.respawn_seconds(match) / combat.TICK) + 2):
                    self.run.step({})
                    if self.a.body != body:
                        break
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
                    self.assertNotIn(bot.entity, match.combat.pins)

                self.assertNotEqual(self.a.body, body)
                self.assertFalse(match.combat.is_dead(self.a))
                self.assertEqual(self.health(self.a), 175)
                self.assertEqual(bot.attack_target, self.a.entity)
                # Restore the original encounter distance after the mode chooses a spawn point.
                self.a.mover.position[:] = original_position
                self.a.mover.body = None
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertLess(self.health(self.a), 175)

    def test_scoped_damage_charges_infra_then_same_npc_can_damage_widowmaker(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                self.setUp()
                self.b.spawned = False
                self.a.client, self.b.client = _NpcPeer(self.a), _NpcPeer(self.b)
                distance = 2.5 if key == "4" else 3
                bot = npc.spawn(self.run.match, definition, (0, 0, distance), team=1, yaw=32768)
                script = self.a.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(342)
                cost = script.component.entity_var(338).value()
                script.set_ult_charge(cost - 120)
                fight = self.run.match.combat
                before = self.health(bot)

                direction = aim(fight._eye(self.a), (0, 1, distance))
                for _ in range(100):
                    self.run.step({self.a: (*direction, SCOPE)})
                self.run.step({self.a: (*direction, SCOPE | FIRE)})
                self.assertEqual(before - self.health(bot), 120)
                self.assertEqual(charge.value(), cost)
                self.press(0x20)
                self.press(0, 45)
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                self.assertEqual(charge.value(), 0)

                future = self.run.frame + 1000
                self.run.match.update(future * combat.TICK, future)
                self.run.frame = future
                self.assertFalse(fight.revealed_to(self.a, bot.entity))

                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, distance))
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                before = self.health(self.a)
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < before:
                        break
                self.assertLess(self.health(self.a), before)
                self.assertEqual(charge.value(), 0)

    def test_infra_sight_tracks_each_actual_npc_without_awarding_charge_during_effect(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                script = self.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.vars[338].value())
                self.press(0x20)
                self.press(0, 45)
                fight = self.run.match.combat
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                before = fight.vitals[bot.entity].total
                self.press(FIRE)
                self.assertAlmostEqual(before - fight.vitals[bot.entity].total, 13)
                self.assertEqual(script.component.vars[342].value(), 0)
                future = self.run.frame + 1000
                self.run.match.update(future * combat.TICK, future)
                self.assertFalse(fight.revealed_to(self.a, bot.entity))

    def test_active_infra_rechecks_team_and_range_for_each_npc_through_cover(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                match = self.run.match
                fight = match.combat
                match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (-5, 0, 0.75, 5, 0, 0.75, 5, 3, 0.75),
                    (-5, 0, 0.75, 5, 3, 0.75, -5, 3, 0.75),
                ], name="NPC Infra-Sight occlusion")
                script = self.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.vars[338].value())
                self.press(0x20)
                self.press(0, 45)
                infra = next(i for i in script.component.instances.values() if i.graph.index == 0x67)
                reveal = infra.state(0)
                health = self.health(bot)
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                self.assertIn(bot.entity, dict(reveal.payload()["targets"]))
                self.press(FIRE)
                self.assertEqual(self.health(bot), health)
                self.assertEqual(self.a.client.messages(combat.HIT), [])

                enemy_team = bot.team
                for team, distance, visible in (
                    (self.a.team, 1.5, False), (enemy_team, 199, True),
                    (enemy_team, 201, False), (enemy_team, 1.5, True),
                ):
                    bot.team = team
                    bot.position = (0, 0, distance)
                    self.press(0, 2)
                    self.assertEqual(fight.revealed_to(self.a, bot.entity), visible)
                    self.assertEqual(bot.entity in dict(reveal.payload()["targets"]), visible)

                self.assertEqual(script.component.vars[342].value(), 0)

    def test_lethal_scoped_hit_removes_each_npc_from_active_infra_sight(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                script = self.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                fight = self.run.match.combat
                vitals = fight.vitals[bot.entity]
                height = self.target(bot).eye - combat.HEAD_BELOW / 2

                # Soften high-health bodies through real shots and reloads before Q.
                for _ in range(40):
                    if vitals.total <= 300:
                        break
                    self.press(SCOPE, 150, height=height)
                    self.press(SCOPE | FIRE, height=height)
                self.assertGreater(vitals.total, 0)
                self.assertLessEqual(vitals.total, 300)
                self.press(0, 120)
                script.set_ult_charge(script.component.vars[338].value())
                self.press(0x20)
                self.press(0, 45)
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                self.assertEqual(script.component.vars[342].value(), 0)

                self.press(SCOPE, 150, height=height)
                self.assertTrue(fight.revealed_to(self.a, bot.entity))
                self.press(SCOPE | FIRE, height=height)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertTrue(script.ultimate_active())
                self.assertFalse(fight.revealed_to(self.a, bot.entity))
                self.assertEqual(script.component.vars[342].value(), 0)
                hit = self.a.client.messages(combat.HIT)[-1]["+0x78"]
                self.assertEqual(hit["+0x0"], {"+0x0": bot.entity})
                self.assertEqual(hit["+0x8"], combat.CRITICAL | combat.KILLING)

    def test_four_actual_npc_weapons_resume_after_real_stun_unless_explicitly_stopped(self):
        for key in ("1", "2", "3", "5"):
            for stop in (False, True):
                definition = self.catalog[key]
                with self.subTest(npc=definition.name, stop_during_stun=stop):
                    self.setUp()
                    bot = self.spawn(definition, (0, 0, 3))
                    self.run.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                    self.b.mover.position[:] = (1, 0, 0)
                    self.b.mover.body = None
                    self.b.mover.history.clear()
                    self.run.match.combat.history.pop(self.b.body, None)
                    fight = self.run.match.combat
                    npc_health = fight.vitals[bot.entity].total
                    direction = aim(fight._eye(self.b), (0, 1, 3))
                    self.run.step({self.b: (*direction, 0x10)})
                    for _ in range(50):
                        if fight.is_stunned(bot):
                            break
                        self.run.step({})
                    self.assertTrue(fight.is_stunned(bot))
                    self.assertEqual(fight.vitals[bot.entity].total, npc_health - 25)
                    self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3))
                    self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                    if stop:
                        self.assertEqual(npc_control.stop(self.run.match), 1)
                    deadline = fight.stuns[bot.entity]
                    health = self.health(self.a)
                    primary = npc_control.button(bot)
                    while self.run.frame < deadline - 1:
                        self.run.step({})
                        self.assertNotIn(primary, bot.body_script.component.held)
                        self.assertEqual(self.health(self.a), health)
                    for _ in range(180):
                        self.run.step({})
                        if not stop and self.health(self.a) < health:
                            break
                    self.assertFalse(fight.is_stunned(bot))
                    if stop:
                        self.assertEqual(self.health(self.a), health)
                        self.assertFalse(bot.firing)
                        self.assertNotIn(primary, bot.body_script.component.held)
                    else:
                        self.assertLess(self.health(self.a), health)

    def test_four_actual_npc_weapons_aim_up_to_hit_elevated_widowmaker(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition, (0, 0, 3))
                self.b.spawned = False
                # A real collision platform supports the target; neither the
                # target nor its projectile path floats inside the floor.
                self.run.match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (-1, 2, -1, -1, 2, 1, 1, 2, 1),
                    (-1, 2, -1, 1, 2, 1, 1, 2, -1),
                ], name="elevated Widow NPC target")
                self.a.mover.position[:] = (0, 2, 0)
                self.a.mover.body = None
                self.a.mover.history.clear()
                fight = self.run.match.combat
                fight.history.pop(self.a.body, None)
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3))
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                before = self.health(self.a)
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < before:
                        break
                self.assertAlmostEqual(self.a.mover.position[1], 2, places=3)
                self.assertLess(bot.pitch, -1000)
                self.assertLess(self.health(self.a), before)

    def test_all_five_allied_bodies_reject_weapon_melee_mine_and_infra_enemy_effects(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                bot.team = self.a.team
                script = self.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.vars[338].value())
                fight = self.run.match.combat
                before = fight.vitals[bot.entity].total
                self.press(FIRE)
                self.press(SCOPE, 100)
                self.press(SCOPE | FIRE)
                self.press(0, 30)
                self.press(0x800)
                self.press(0, 40)
                self.press(0x10)
                self.press(0, 130)
                self.assertEqual(len(fight.venom.mines), 1)
                self.assertFalse(fight.venom.poison)
                self.press(0x20)
                self.press(0, 45)
                infra = next(i for i in script.component.instances.values() if i.graph.index == 0x67)
                self.assertTrue(infra.state(0).active)
                self.assertFalse(fight.revealed_to(self.a, bot.entity))
                self.assertEqual(fight.vitals[bot.entity].total, before)
                self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_projected_barrier_blocks_scoped_hit_on_each_actual_npc_without_awarding_charge(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                zarya = 0x02E0000000000068
                ally = self.run.match.add_player(3, "NpcShield", zarya, bot.team, False)
                in_world(self.run.match, ally)
                ally.client = _NpcPeer(ally)
                self.run.match.switch_hero(ally, heroes()[zarya])
                ally.mover.position[:] = (-2, 0, 1.5)
                ally.mover.body = None
                self.run.players.append(ally)
                self.a.body_script.ult_seconds = 0
                self.a.body_script.set_ult_charge(0)
                self.press(SCOPE, 100)
                incoming = aim(self.run.match.combat._eye(self.a), (0, 1, 1.5))
                protecting = aim(self.run.match.combat._eye(ally), (0, combat.EYE, 1.5))
                self.run.step({self.a: (*incoming, SCOPE), ally: (*protecting, 0x10)})
                barrier = next(i for i in ally.body_script.component.instances.values()
                               if i.graph.index == 0x25C)
                self.assertTrue(barrier.state(10).active)
                self.assertEqual(barrier.vars[536].value(), Entity(bot.entity))
                fight = self.run.match.combat
                before = fight.vitals[bot.entity].total
                self.press(SCOPE | FIRE)
                self.assertEqual(fight.vitals[bot.entity].total, before)
                self.assertEqual(fight.zarya_barriers.projected[ally.body].remaining, 80)
                self.assertEqual(ally.body_script.component.entity_var(87).value(), 24)
                self.assertEqual(self.a.body_script.component.vars[342].value(), 0)
                self.assertEqual(self.a.client.messages(combat.HIT), [])
                self.press(SCOPE, 130)
                self.assertFalse(barrier.state(10).active)
                self.press(SCOPE | FIRE)
                self.assertEqual(before - fight.vitals[bot.entity].total, 120)
                self.assertEqual(self.a.body_script.component.vars[342].value(), 120)

    def test_projected_barrier_absorbs_active_mine_poison_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition)
                match = self.run.match
                ally = match.add_player(3, "NpcShield", zarya.HERO, bot.team, False)
                in_world(match, ally)
                ally.client = _NpcPeer(ally)
                self.assertTrue(match.switch_hero(ally, heroes()[zarya.HERO]))
                ally.mover.position[:] = (-8, 0, 1.5)
                ally.mover.body = None
                self.run.players.append(ally)
                fight = match.combat
                script = self.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                initial_health = self.health(bot)

                self.press(0x10)
                for _ in range(180):
                    self.press(0)
                    if fight.venom.poisoned_by(self.a, bot.entity):
                        break
                self.assertTrue(fight.venom.poisoned_by(self.a, bot.entity))
                self.assertFalse(fight.venom.poisoned_by(self.a, ally.body))
                protecting = aim(fight._eye(ally), (0, combat.EYE, 1.5))
                self.run.step({ally: (*protecting, 0x10)})
                barrier = next(i for i in ally.body_script.component.instances.values()
                               if i.graph.index == 0x25C)
                self.assertTrue(barrier.state(10).active)
                self.assertEqual(barrier.vars[536].value(), Entity(bot.entity))
                protected_health = self.health(bot)
                charge = script.component.vars[342].value()
                self.press(0, 64)
                pool = fight.zarya_barriers.projected[ally.body]
                self.assertLess(pool.remaining, 200)
                self.assertEqual(self.health(bot), protected_health)
                self.assertEqual(script.component.vars[342].value(), charge)
                self.assertTrue(fight.revealed_to(self.a, bot.entity))

                self.press(0, 400)
                self.assertFalse(barrier.state(10).active)
                self.assertFalse(fight.venom.poisoned_by(self.a, bot.entity))
                self.assertFalse(fight.revealed_to(self.a, bot.entity))
                body_damage = initial_health - self.health(bot)
                self.assertLess(self.health(bot), protected_health)
                self.assertAlmostEqual(body_damage + 200 - pool.remaining, 75, places=5)
                self.assertAlmostEqual(script.component.vars[342].value(), body_damage, places=5)

    def test_all_four_npc_weapons_are_absorbed_by_allied_matrix_then_hit_widowmaker(self):
        if graph.entity_body(dva_body.MECH) is None or defined_mover_data(dva_body.MECH) is None:
            self.skipTest("requires explicit mech graph and mover supplements")
        for key, projectile in (("1", 0x1FE6), ("2", 0x23F0), ("3", 0x1E80), ("5", 0x206A)):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                match = self.run.match
                fight = match.combat
                projectile |= 0x0400000000000000
                self.assertTrue(fight.projectile_catalog.absorbable(projectile))
                bot = self.spawn(definition, (0, 0, 14))
                mech = replace(heroes()[dva_body.DVA], body=dva_body.MECH)
                self.assertTrue(match.switch_hero(self.b, mech))
                self.b.mover.position[:] = (0, 0, -2)
                self.b.mover.body = None
                for _ in range(100):
                    self.run.step({})
                matrix = next(i for i in self.b.body_script.component.instances.values()
                              if i.graph.index == 0x02CC)
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 14))
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                if key == "3":
                    for _ in range(25):
                        self.run.step({})
                self.assertEqual(self.health(self.a), 175)

                absorbed = []
                matrix_hit = fight._matrix_hit

                def observe(shooter, origin, direction, reach, definition, *, team=None,
                            matrix_hit=matrix_hit, bot=bot, absorbed=absorbed):
                    contact = matrix_hit(shooter, origin, direction, reach, definition, team=team)
                    if shooter is bot and contact is not None:
                        absorbed.append(definition)
                    return contact

                with patch.object(fight, "_matrix_hit", side_effect=observe):
                    for _ in range(125):
                        self.run.step({self.b: (0, 0, 2)})
                        if absorbed:
                            break
                self.assertTrue(matrix.state(3).active)
                self.assertTrue(absorbed, self.health(self.a))
                self.assertEqual(set(absorbed), {projectile})
                self.assertEqual(self.health(self.a), 175)
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertFalse(matrix.state(3).active)
                self.assertLess(self.health(self.a), 175)

    def test_all_four_npc_weapons_reflect_from_allied_genji_back_to_attacker(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                match = self.run.match
                fight = match.combat
                bot = self.spawn(definition, (0, 0, 6))
                self.assertTrue(match.switch_hero(self.b, heroes()[GENJI]))
                self.b.mover.position[:] = (-4, 0, 3)
                self.b.mover.body = None
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 6))
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                if key == "3":
                    for _ in range(25):
                        self.run.step({})

                self.b.mover.position[:] = (0, 0, 3)
                self.b.mover.body = None
                direction = aim(fight._eye(self.b), (0, 1, 6))
                before = self.health(bot)
                self.run.step({self.b: (*direction, 16)})
                deflect = next(i for i in self.b.body_script.component.instances.values()
                               if i.graph.index == 0x3A3)
                for _ in range(115):
                    self.run.step({self.b: (*direction, 0)})
                    if self.health(bot) < before:
                        break
                self.assertTrue(deflect.state(10).active)
                self.assertLess(self.health(bot), before)
                self.assertTrue(self.b.client.messages(combat.HIT))
                self.assertEqual(self.health(self.b), 200)
                self.assertEqual(self.health(self.a), 175)

                self.b.mover.position[:] = (-4, 0, 3)
                self.b.mover.body = None
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertLess(self.health(self.a), 175)

    def test_all_four_npc_weapons_feed_allied_sigma_grasp_shields(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                match = self.run.match
                fight = match.combat
                bot = self.spawn(definition, (0, 0, 8))
                self.assertTrue(match.switch_hero(self.b, heroes()[sigma.HERO]))
                self.b.mover.position[:] = (-4, 0, 3)
                self.b.mover.body = None
                target = aim(fight._eye(self.a), (0, 1, 8))
                self.a.mover.yaw, self.a.mover.pitch = target
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                if key == "3":
                    for _ in range(25):
                        self.run.step({})

                self.b.mover.position[:] = (0, 0, 3)
                self.b.mover.body = None
                self.run.step({self.b: (0, 0, 8)})
                grasp = next(i for i in self.b.body_script.component.instances.values()
                             if i.graph.index == sigma.GRASP)
                absorbed = 0
                for _ in range(115):
                    self.run.step({self.b: (0, 0, 0)})
                    absorbed = grasp.vars[21312].value() or 0
                    if absorbed > 0:
                        break
                self.assertTrue(grasp.state(1).active)
                self.assertGreater(absorbed, 0)
                self.assertEqual(self.health(self.b), 400)
                self.assertEqual(self.health(self.a), 175)

                self.assertEqual(npc_control.stop(match), 1)
                for _ in range(160):
                    self.run.step({})
                self.assertFalse(grasp.state(1).active)
                shield = fight.vitals[self.b.body].temporary_shields[sigma.SHIELD_TAG]
                self.assertAlmostEqual(shield.now, absorbed * 0.6, places=4)
                self.assertEqual(self.health(self.a), 175)

                self.b.mover.position[:] = (-4, 0, 3)
                self.b.mover.body = None
                self.a.mover.yaw, self.a.mover.pitch = target
                self.assertEqual(npc_control.start(match, self.a), bot.entity)
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertLess(self.health(self.a), 175)

    def test_four_actual_npc_weapons_are_blocked_on_widow_then_hit_after_projected_expiry(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition, (0, 0, 3))
                self.run.match.switch_hero(self.b, heroes()[0x02E0000000000068])
                self.b.mover.position[:] = (-2, 0, 0)
                self.b.mover.body = None
                fight = self.run.match.combat
                if key == "3":
                    # Its observed first shot arrives127 frames after start,
                    # beyond a125-frame barrier. Cast reactively after20 ticks;
                    # this explicit fixture timing is not native AI automation.
                    self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3))
                    self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                    for _ in range(20):
                        self.run.step({})
                    self.assertEqual(self.health(self.a), 175)
                protecting = aim(self.run.match.combat._eye(self.b), self.run.match.combat._eye(self.a))
                self.run.step({self.b: (*protecting, 0x10)})
                barrier = next(i for i in self.b.body_script.component.instances.values()
                               if i.graph.index == 0x25C)
                self.assertTrue(barrier.state(10).active)
                self.assertEqual(barrier.vars[536].value(), Entity(self.a.body))
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3))
                if key != "3":
                    self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                for _ in range(110):
                    self.run.step({})
                    pool = fight.zarya_barriers.projected.get(self.b.body)
                    if pool is not None and pool.remaining < 200:
                        break
                self.assertIsNotNone(pool)
                self.assertLess(pool.remaining, 200)
                self.assertEqual(self.health(self.a), 175)
                self.assertGreater(self.b.body_script.component.entity_var(87).value(), 0)
                self.assertEqual(npc_control.stop(self.run.match), 1)
                for _ in range(180):
                    self.run.step({})
                self.assertFalse(barrier.state(10).active)
                self.assertEqual(self.health(self.a), 175)
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3))
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertLess(self.health(self.a), 175)


if __name__ == "__main__":
    unittest.main()
