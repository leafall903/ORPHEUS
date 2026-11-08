"""Executed Soldier interactions with prepared real PvE bodies and mover capsules.

Set ORPHEUS_PVE_DATA_DIR and ORPHEUS_PVE_MOVERS_FILE to external prepared data.
The body VM remains permissive and collision/transport are explicit fixtures.
These cases do not claim enemy AI, difficulty scaling or client acceptance.
"""

import logging
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_soldier_combat_lane as soldier_fixture
from test_game_combat import FakeClient, Run, aim

from ow174.game import combat, mover, npc
from ow174.game.collision import World, flat_world
from ow174.game.script import expr, graph
from ow174.game.script.driver import OWNER_HEALTH


class NpcRecordingClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.bot_deaths = []
        self.bot_death_updates = []

    def queue_bot_death(self, entity, event):
        self.bot_deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.bot_death_updates.append((entity, event))

    def bot_stream(self, entity):
        return None  # no NPC script ACK/observer projectile presentation is modeled here


class SoldierPveStaticTests(unittest.TestCase):
    input = soldier_fixture.SoldierCombatLaneTests.input
    vitals = soldier_fixture.SoldierCombatLaneTests.vitals
    start_visor = soldier_fixture.SoldierCombatLaneTests.start_visor

    @classmethod
    def setUpClass(cls):
        data_dir = os.environ.get("ORPHEUS_PVE_DATA_DIR")
        movers_file = os.environ.get("ORPHEUS_PVE_MOVERS_FILE")
        if not data_dir or not movers_file:
            raise unittest.SkipTest("explicit prepared five-NPC script/catalog/mover inputs are required")
        cls.data_dir = Path(data_dir)
        cls.catalog = npc.load_catalog(cls.data_dir / "pve_npcs_174.json")
        if {row.name for row in cls.catalog.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault",
        }:
            raise ValueError("the complete five-class NPC catalog is required")
        cls.edits = [patch.object(graph, "GRAPHS_PATH", cls.data_dir / "game_graphs_174.json.gz"),
                     patch.object(mover, "MOVERS_PATH", Path(movers_file))]
        for edit in cls.edits:
            edit.start()
        cls.reset_catalogs()

    @staticmethod
    def reset_catalogs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()
        mover._movers.cache_clear()

    @classmethod
    def tearDownClass(cls):
        for edit in reversed(cls.edits):
            edit.stop()
        cls.reset_catalogs()

    def arena(self, key, position=(0.0, 0.0, 1.0), team=1):
        logging.getLogger("ow174.script").setLevel(logging.ERROR)
        run = Run()
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0.0)
        soldier, = run.players
        soldier.client = NpcRecordingClient(soldier)
        soldier.mover.position = (0.0, 0.0, -3.0)
        definition = self.catalog[key]
        enemy = npc.spawn(run.match, definition, position, team=team)
        self.assertIsNotNone(enemy.body_script)
        self.assertIn(enemy.body, mover._movers())  # forbid silent Soldier-capsule fallback
        self.assertEqual(enemy.hero, definition.hero)
        self.assertEqual(enemy.body, definition.body)
        self.assertEqual(enemy.body_script.component.vars[OWNER_HEALTH].value(),
                         definition.health + definition.armour + definition.shields)
        return run, soldier, enemy

    def test_rifle_melee_helix_and_visor_deal_authored_damage_to_all_five_bodies(self):
        for key, definition in self.catalog.items():
            for weapon, damage in (("rifle", 20), ("melee", 30), ("helix", 120), ("visor", 20)):
                with self.subTest(npc=definition.name, weapon=weapon):
                    position = (3.0, 0.0, 3.0) if weapon == "visor" else (0.0, 0.0, -1.5)
                    run, soldier, enemy = self.arena(key, position)
                    pool = self.vitals(run, enemy)
                    before = pool.total
                    yaw, pitch = aim(run.match.combat._eye(soldier),
                                     (position[0], 0.8, position[2]))
                    if weapon == "visor":
                        self.start_visor(run, soldier)
                        self.input(run, soldier, 1)
                    else:
                        self.input(run, soldier, {"rifle": 1, "melee": 0x800, "helix": 2}[weapon], yaw, pitch)
                    for _ in range(25):
                        self.input(run, soldier)
                    self.assertEqual(before - pool.total, damage)
                    if definition.shields:
                        self.assertLess(pool.now[2], definition.shields)
                        self.assertEqual(pool.now[1], 0)  # variable 221 is NOT armour for these NPCs

    def test_all_five_real_bodies_respect_static_wall_for_rifle_melee_and_helix(self):
        for key, definition in self.catalog.items():
            for buttons in (1, 0x800, 2):
                with self.subTest(npc=definition.name, buttons=buttons):
                    run, soldier, enemy = self.arena(key, (0.0, 0.0, -0.8))
                    run.match.collision_world = World([
                        (-10, -10, -2, 10, -10, -2, 10, 10, -2),
                        (-10, -10, -2, 10, 10, -2, -10, 10, -2),
                    ])
                    pool = self.vitals(run, enemy)
                    before = pool.total
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, -0.8))
                    self.input(run, soldier, buttons, yaw, pitch)
                    for _ in range(25):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, before)

    def test_field_heals_allied_bodies_but_excludes_enemy_and_dead_bodies(self):
        for key, definition in self.catalog.items():
            for team in (0, 1):
                with self.subTest(npc=definition.name, team=team):
                    run, soldier, enemy = self.arena(key, (0.0, 0.0, -3.0), team=team)
                    pool = self.vitals(run, enemy)
                    pool.damage(50)
                    before = pool.total
                    run.match.combat.heal_area(soldier, soldier.mover.position, 5, 35)
                    self.assertEqual(pool.total, before + (35 if team == 0 else 0))
                    pool.died_at = run.match.combat.now
                    dead_health = pool.total
                    run.match.combat.heal_area(soldier, soldier.mover.position, 5, 35)
                    self.assertEqual(pool.total, dead_health)

    def test_rifle_kills_each_real_body_and_retains_corpse_without_respawn(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, enemy = self.arena(key)
                pool = self.vitals(run, enemy)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                for _ in range(5000):
                    if enemy.dead:
                        break
                    rounds = soldier.body_script.component.instances[11].vars[53].value()
                    self.input(run, soldier, 1 if rounds else 4, yaw, pitch)
                self.assertTrue(enemy.dead)
                self.assertEqual(pool.total, 0)
                self.assertTrue(enemy.body_script.component.owner_dead)
                self.assertTrue(any(entity == enemy.entity for entity, _ in soldier.client.bot_deaths))
                self.assertFalse(any(actor is enemy for _, actor in run.match.combat.respawns))
                for _ in range(200):
                    self.input(run, soldier)
                self.assertIn(enemy, run.match.bots)
                self.assertTrue(enemy.retain_corpse)
                self.assertFalse(any(target.owner is enemy for target in run.match.combat._enemies(soldier)))
                self.assertEqual(npc.remove_spawned(run.match), (enemy.entity,))

    def test_authored_trooper_sniper_heavy_damage_soldier_and_respect_team_aim_and_wall(self):
        # The remaining two bodies do not yet emit volleys from slot 0. They
        # stay open; do not replace their authored weapons with a Soldier rifle.
        for key, amount in (("1", 20), ("3", 100), ("5", 5)):
            for case in ("hit", "ally", "miss", "wall"):
                with self.subTest(npc=self.catalog[key].name, case=case):
                    run, soldier, actor = self.arena(key, (0.0, 0.0, -7.0), team=0 if case == "ally" else 1)
                    if case == "miss":
                        actor.yaw = 16384
                    if case == "wall":
                        run.match.collision_world = World([
                            (-10, -10, -5, 10, -10, -5, 10, 10, -5),
                            (-10, -10, -5, 10, 10, -5, -10, 10, -5),
                        ])
                    actor.firing = True
                    script = actor.body_script
                    script.advance_live(script.frame + 1, actor.position, weapon_input=(0, True))
                    pool = self.vitals(run, soldier)
                    observed = []
                    shoot = run.match.combat._shoot

                    def record(shooter, command, state, *args, observed=observed, shoot=shoot, **kwargs):
                        direct = combat.hit_def(state, state.node.field("m_6396149F"))
                        if direct is not None:
                            observed.append(direct.amount)
                        return shoot(shooter, command, state, *args, **kwargs)

                    with patch.object(run.match.combat, "_shoot", side_effect=record):
                        for frame in range(run.frame + 1, run.frame + 201):
                            run.match.update(frame * 0.016, frame, frames=False)
                            if case == "hit" and pool.total < 200:
                                break
                    self.assertTrue(observed)
                    self.assertEqual(observed[0], amount)
                    self.assertEqual(pool.total, 200 - amount if case == "hit" else 200)

    def test_duplicate_or_invalid_npc_weapon_input_cannot_create_extra_press_edges(self):
        def damage(repeat):
            run, soldier, actor = self.arena("1", (0.0, 0.0, -7.0))
            actor.firing = True
            script = actor.body_script
            for frame in range(run.frame + 1, run.frame + 101):
                if frame == run.frame + 1 or repeat:
                    script.advance_live(frame, actor.position, weapon_input=(0, True))
                run.match.update(frame * 0.016, frame, frames=False)
            return self.vitals(run, soldier).total

        ordinary = damage(False)
        self.assertLess(ordinary, 200)
        self.assertEqual(damage(True), ordinary)
        _run, _, actor = self.arena("1")
        script = actor.body_script
        before = (script.frame, tuple(script.component.held))
        for value in ((True, True), (0, 1), (99, True), [0, True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                script.advance_live(script.frame + 1, actor.position, weapon_input=value)
            self.assertEqual((script.frame, tuple(script.component.held)), before)

    def test_npc_self_health_query_tracks_explicit_pools_without_supporting_foreign_queries(self):
        run, soldier, actor = self.arena("2")
        script = actor.body_script
        instance = next(iter(script.component.instances.values()))
        cfg = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(script.component.world.native(cfg, instance), 350)
        target = next(target for target in run.match.combat._targets() if target.owner is actor)
        run.match.combat._damage(soldier, target, 40, False, None, run.frame)
        self.assertEqual(script.component.world.native(cfg, instance), 310)
        script.component.set(65000, expr.Entity(soldier.body))
        foreign = {"$": "STU_9B7648C3", "m_entity": {
            "$": "STUConfigVarDynamic", "m_identifier": 65000, "m_60DB8F99": 1,
        }}
        self.assertEqual(instance.evaluate(foreign.get("m_entity")), expr.Entity(soldier.body))
        self.assertNotEqual(script.component.world.native(foreign, instance), 310)

    def test_heavy_damage_fills_authored_ultimate_cost_then_activation_spends_and_blocks_gain(self):
        run, soldier, enemy = self.arena("5", (3.0, 0.0, 3.0))
        script = soldier.body_script
        script.ult_seconds = 0
        cost = script.component.vars[338].value()
        self.assertGreater(cost, 0)
        pool = self.vitals(run, enemy)
        yaw, pitch = aim(run.match.combat._eye(soldier), (3.0, 0.8, 3.0))
        for _ in range(5000):
            if script.component.vars[342].value() == cost:
                break
            rounds = script.component.instances[11].vars[53].value()
            self.input(run, soldier, 1 if rounds else 4, yaw, pitch)
        self.assertEqual(script.component.vars[342].value(), cost)
        self.assertLess(pool.total, 4000)
        self.input(run, soldier, 0x20)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(80):
            self.input(run, soldier)
        before = pool.total
        self.input(run, soldier, 1)  # off-axis acquisition from the active ultimate
        self.assertEqual(pool.total, before - 20)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(400):
            self.input(run, soldier)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(script.component.vars[342].value(), 20)


if __name__ == "__main__":
    unittest.main()
