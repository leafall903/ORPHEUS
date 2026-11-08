"""Persistent turret combat under an explicit prepared-data reconstruction."""

import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_torbjorn as torbjorn
from test_game_combat import aim

from ow174.game import hero_torbjorn, npc, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn bundle")
class TorbjornTurretTests(unittest.TestCase):
    @staticmethod
    def clear_graph_cache():
        for function in (graph._data, graph._records, graph.graph, graph.mode_variables):
            function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        graph.GRAPHS_PATH = Path(os.environ["ORPHEUS_TORBJORN_GRAPH_BUNDLE"])
        cls.clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, distance=8.0, *, deploy=True):
        run, owner, enemy = torbjorn.TorbjornProjectileTests().arena(distance)
        for viewer in run.match.players:
            viewer.client.queue_bot_death = lambda *args: None
            viewer.client.queue_bot_death_update = lambda *args: None
        turret = None
        if deploy:
            x, y, z = owner.mover.position
            turret = hero_torbjorn.deploy_turret(run.match.combat, owner, (x, y, z + 1.5))
        return run, owner, enemy, turret

    @staticmethod
    def advance(run, ticks):
        for _ in range(ticks):
            run.step({})

    def test_build_then_actual_turret_fire_credits_owner(self):
        run, owner, enemy, turret = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        enemy_hp = fight.vitals_of(target)
        self.assertEqual(fight.vitals[turret.actor.entity].total, 75.0)
        self.advance(run, 187)
        self.assertEqual(enemy_hp.total, 200.0)
        self.assertEqual(turret.shots, 0)
        self.advance(run, 63)
        self.assertEqual(fight.vitals[turret.actor.entity].total, 250.0)
        self.assertEqual(turret.shots, 5)
        self.assertEqual(enemy_hp.total, 130.0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70.0)
        self.assertEqual(fight.vitals[owner.body].total, 250.0)

    def test_range_ally_and_static_wall_reject_targets(self):
        for case in ("range", "ally", "wall"):
            with self.subTest(case=case):
                run, owner, enemy, turret = self.arena(45.0 if case == "range" else 8.0)
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = owner.team
                elif case == "wall":
                    x, y, z = owner.mover.position
                    wall = z + 4.0
                    run.match.collision_world = World([
                        (x-3, y, wall, x+3, y, wall, x+3, y+4, wall),
                        (x-3, y, wall, x+3, y+4, wall, x-3, y+4, wall),
                    ], name="turret-wall")
                self.advance(run, 250)
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
                self.assertEqual(turret.shots, 0)

    def test_actual_throw_lands_and_creates_the_persistent_actor(self):
        run, owner, _, _ = self.arena(100.0, deploy=False)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 100)
        turret = run.match.combat.turrets[owner.entity]
        self.assertEqual(turret.actor.body, hero_torbjorn.TURRET)
        self.assertAlmostEqual(turret.actor.position[1], owner.mover.position[1])
        self.assertIsNotNone(turret.actor.body_script)
        self.assertEqual(turret.actor.combat_owner, owner)

    def test_actual_throw_spam_cannot_bypass_redeploy_cooldown(self):
        run, owner, _, _ = self.arena(100.0, deploy=False)
        start = run.frame
        run.step({owner: (0, 4000, 8)})
        for _ in range(100):
            run.step({})
            if owner.entity in run.match.combat.turrets:
                break
        first = run.match.combat.turrets[owner.entity].actor.entity
        while run.frame < start + 300:
            run.step({owner: (0, 4000, 8 if run.frame % 2 == 0 else 0)})
        self.assertEqual(run.match.combat.turrets[owner.entity].actor.entity, first)
        while run.frame < start + 420:
            run.step({owner: (0, 4000, 8 if run.frame % 2 == 0 else 0)})
        self.assertNotEqual(run.match.combat.turrets[owner.entity].actor.entity, first)

    def test_enemy_destruction_starts_full_cooldown_and_rejects_early_throw(self):
        run, owner, enemy, turret = self.arena(100.0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is turret.actor)
        fight._damage(enemy, target, 1000.0, False, None, run.frame)
        run.step({})
        ability = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).states[13]
        self.assertGreater(ability.remaining(), 9.9)
        for index in range(580):
            run.step({owner: (0, 4000, 8 if index % 2 == 0 else 0)})
        self.assertNotIn(owner.entity, fight.turrets)
        for index in range(200):
            run.step({owner: (0, 4000, 8 if index % 2 == 0 else 0)})
        self.assertIn(owner.entity, fight.turrets)

    def test_turret_receives_real_player_fire_without_headshot_multiplier(self):
        run, _, enemy, turret = self.arena()
        self.advance(run, 190)
        fight = run.match.combat
        x, y, z = turret.actor.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 0.9, z))
        gun = enemy.body_script.component.instances[11]
        state = gun.states[10]
        hp = fight.vitals[turret.actor.entity]
        fight._shoot(enemy, Command(run.frame, yaw=yaw, pitch=pitch), state, 0, run.frame * 16,
                     activation=(1, run.frame * 16))
        self.assertEqual(hp.total, 230.0)
        self.assertEqual(enemy.client.messages(25001)[-1]["+0x78"]["+0x8"], 0)

    def test_hammer_repairs_own_turret_without_damaging_it(self):
        run, owner, enemy, turret = self.arena(100.0)
        self.advance(run, 190)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is turret.actor)
        vitals = fight.vitals_of(target)
        fight._damage(enemy, target, 80.0, False, None, run.frame)
        self.assertEqual(vitals.total, 170.0)
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        self.advance(run, 45)
        for _ in range(45):
            run.step({owner: (0, 0, 1)})
        self.assertEqual(vitals.total, 220.0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0.0)

    def test_destroy_redeploy_and_manual_npc_clear_do_not_respawn_turrets(self):
        run, owner, enemy, turret = self.arena(100.0)
        fight = run.match.combat
        self.assertEqual(npc.remove_spawned(run.match), ())
        old_entity = turret.actor.entity
        next_turret = hero_torbjorn.deploy_turret(fight, owner, turret.actor.position)
        self.assertNotEqual(next_turret.actor.entity, old_entity)
        self.assertNotIn(old_entity, fight.vitals)
        target = next(t for t in fight._targets() if t.owner is next_turret.actor)
        fight._damage(enemy, target, 1000.0, False, None, run.frame)
        self.advance(run, 220)
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertNotIn(next_turret.actor, run.match.bots)
        self.assertFalse(any(actor is next_turret.actor for _, actor in fight.respawns))

    def test_owner_death_preserves_turret_but_hero_change_retires_it(self):
        run, owner, enemy, turret = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(enemy, target, 1000.0, False, None, run.frame)
        self.advance(run, 200)
        self.assertIn(owner.entity, fight.turrets)
        self.assertGreater(turret.shots, 0)
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.advance(run, 1)
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertNotIn(turret.actor, run.match.bots)

    def test_disconnected_owner_retires_turret_before_it_can_fire(self):
        run, owner, _, turret = self.arena()
        fight = run.match.combat
        owner.client = None
        fight.update(fight.now + 0.016, fight.tick + 1, frames=False)
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertNotIn(turret.actor, run.match.bots)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_turret_fire_damages_all_five_actual_npc_bodies(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, _, turret = self.arena(100.0)
                x, y, z = owner.mover.position
                bot = npc.spawn(run.match, definition, (x, y, z + 8), team=1)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.advance(run, 250)
                self.assertEqual(before - vitals.total, 70.0)
                self.assertEqual(turret.target, bot.entity)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70.0)

    def test_live_turret_timing_geometry_and_ownership_are_in_passive_snapshots(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        owner = players[0]
        session.apply("switch_hero", {"account": owner.account_lo, "hero": hero_torbjorn.HERO})
        x, y, z = owner.mover.position
        turret = hero_torbjorn.deploy_turret(owner.match.combat, owner, (x, y, z + 1.5))
        first = snapshot_digest(case.capture(session))
        turret.next_fire += 0.1
        second = snapshot_digest(case.capture(session))
        self.assertNotEqual(first, second)
        turret.actor.combat_geometry = (1.1, 0.5, 0.75, False)
        self.assertNotEqual(second, snapshot_digest(case.capture(session)))
