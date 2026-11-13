"""Persistent turret combat under an explicit prepared-data reconstruction."""

import os
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn as torbjorn
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, hero_torbjorn, molten_core, npc, npc_control, stats, world
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, nodes


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

    def test_landed_throw_retires_prediction_aliases_and_has_one_actor(self):
        run, owner, enemy, _ = self.arena(100.0, deploy=False)
        fight = run.match.combat
        run.step({owner: (0, 4000, 8)})
        shot = None
        for _ in range(35):
            run.step({})
            if fight.rockets:
                shot = fight.rockets[0].shot
                break
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000002931)
        self.assertEqual({delivery.viewer for delivery in shot.observers}, {enemy})
        self.advance(run, 100)
        turret = fight.turrets[owner.entity]
        self.assertEqual([bot.entity for bot in run.match.bots], [turret.actor.entity])
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertFalse(fight.rockets)
        self.assertTrue(shot.created)
        for entity in shot.created:
            destroys = [update for update in owner.client.entities
                        if update.entity == entity and update.op == world.OP_DESTROY]
            self.assertEqual(len(destroys), 1)
            self.assertIsNone(destroys[0].ch2)
        for delivery in shot.observers:
            self.assertTrue(delivery.finished)
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(any(update.entity == delivery.record.ids[0]
                                and update.op == world.OP_DESTROY
                                for update in enemy.client.entities))
        self.advance(run, 150)
        self.assertEqual([bot.entity for bot in run.match.bots], [turret.actor.entity])
        self.assertEqual(fight.vitals[turret.actor.entity].total, 250.0)

    def test_airborne_throw_lands_and_fires_after_owner_is_killed(self):
        run, owner, enemy, _ = self.arena(8, deploy=False)
        fight = run.match.combat
        x, y, z = owner.mover.position
        enemy.mover.position = [x + 4, y, z + 8]
        enemy.mover.body = None
        targets = {target.owner: target for target in fight._targets()}
        hp = fight.vitals_of(targets[owner])
        enemy_hp = fight.vitals_of(targets[enemy])
        hp.damage(hp.total - 1)
        body = owner.body
        run.step({owner: (0, 4000, 8)})
        for _ in range(35):
            if any(rocket.spawn == hero_torbjorn.TURRET for rocket in fight.rockets):
                break
            run.step({})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == hero_torbjorn.TURRET)
        self.assertNotIn(owner.entity, fight.turrets)
        direction = aim(fight._eye(enemy), (x, y + .6, z))
        run.step({enemy: (*direction, 1)})
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, enemy.body)
        self.assertIn(flight, fight.rockets)
        for _ in range(275):
            if enemy_hp.total < 200:
                break
            run.step({})
        turret = fight.turrets[owner.entity]
        self.assertEqual(owner.body, body)
        self.assertTrue(fight.is_dead(owner))
        self.assertIs(turret.actor.combat_owner, owner)
        self.assertEqual(turret.target, enemy.body)
        self.assertEqual(turret.shots, 1)
        self.assertEqual(enemy_hp.total, 186)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)

    def test_same_tick_hero_switch_cancels_deployment_from_airborne_throw(self):
        run, owner, _, _ = self.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        position = list(owner.mover.position)
        run.step({owner: (0, 4000, 8)})
        for _ in range(35):
            if any(rocket.spawn == hero_torbjorn.TURRET for rocket in fight.rockets):
                break
            run.step({})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == hero_torbjorn.TURRET)
        self.assertIsNotNone(flight.shot)
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotIn(flight, fight.rockets)
        self.assertTrue(match.switch_hero(owner, heroes()[hero_torbjorn.HERO]))
        owner.mover.position = position
        owner.mover.body = None
        self.advance(run, 100)
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(owner), 0)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 15)
        self.assertFalse(any(r.shooter is owner and r.spawn == hero_torbjorn.TURRET for r in fight.rockets))
        for _ in range(350):
            if hero_torbjorn.turret_cooldown_remaining(owner) <= 0:
                break
            run.step({})
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 0)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 100)
        self.assertIs(fight.turrets[owner.entity].owner, owner)

    def test_interact_rejects_construction_then_dismantles_on_a_fresh_press(self):
        run, owner, _, turret = self.arena(100.0)
        fight = run.match.combat
        run.step({owner: (0, 0, 4)})
        self.assertIn(owner.entity, fight.turrets)
        for _ in range(210):
            run.step({owner: (0, 0, 4)})
        self.assertIn(owner.entity, fight.turrets)
        run.step({})
        run.step({owner: (0, 0, 4)})
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertNotIn(turret.actor, run.match.bots)
        ability = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).states[13]
        self.assertGreater(ability.remaining(), 9.9)
        for _ in range(580):
            run.step({owner: (0, 4000, 12 if run.frame % 2 else 0)})
        self.assertNotIn(owner.entity, fight.turrets)
        for _ in range(160):
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
        self.assertIn(owner.entity, fight.turrets)

    def test_simultaneous_interact_and_throw_cannot_bypass_dismantle_cooldown(self):
        run, owner, _, _ = self.arena(100.0)
        self.advance(run, 200)
        run.step({owner: (0, 4000, 12)})
        self.advance(run, 100)
        fight = run.match.combat
        self.assertNotIn(owner.entity, fight.turrets)
        self.assertFalse(any(rocket.shooter is owner for rocket in fight.rockets))
        ability = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).states[13]
        self.assertGreater(ability.remaining(), 8.0)

    def test_firing_blocks_actual_redeployment_then_recovers_after_three_seconds(self):
        run, owner, enemy, turret = self.arena()
        self.advance(run, 230)
        self.assertGreater(turret.shots, 0)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 30)
        self.assertIs(run.match.combat.turrets[owner.entity], turret)
        self.assertFalse(any(rocket.shooter is owner for rocket in run.match.combat.rockets))
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        fight._damage(owner, target, 1000.0, False, None, run.frame)
        self.advance(run, 150)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 30)
        self.assertIs(run.match.combat.turrets[owner.entity], turret)
        self.advance(run, 35)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 90)
        self.assertIsNot(run.match.combat.turrets[owner.entity], turret)

    def test_actual_enemy_damage_blocks_redeployment_for_authored_damage_window(self):
        run, owner, enemy, turret = self.arena(100.0)
        self.advance(run, 190)
        fight = run.match.combat
        hp = fight.vitals[turret.actor.entity]
        before = hp.total
        x, y, z = turret.actor.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 0.5, z))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertLess(hp.total, before)
        self.assertIsNotNone(turret.last_damaged)
        for _ in range(170):
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
        self.assertIs(fight.turrets[owner.entity], turret)
        for _ in range(140):
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
        self.assertIsNot(fight.turrets[owner.entity], turret)

    def test_interact_cannot_remove_another_players_turret(self):
        run, owner, enemy, turret = self.arena(100.0)
        self.advance(run, 200)
        run.step({enemy: (0, 0, 4)})
        self.assertIs(run.match.combat.turrets[owner.entity], turret)
        self.assertFalse(hero_torbjorn.turret_message(enemy.body_script.world, turret.actor.entity,
                                                    0x024000000000003F, enemy.body))
        self.assertIs(run.match.combat.turrets[owner.entity], turret)

    def test_reconstructed_context_does_not_masquerade_as_a_strict_native_service(self):
        run, owner, _, turret = self.arena(100.0)
        component = owner.body_script.component
        self.assertIs(hero_torbjorn.owned_turret(owner.body_script.world), turret)
        component.strict = True
        try:
            self.assertIsNone(hero_torbjorn.owned_turret(owner.body_script.world))
            self.assertIs(hero_torbjorn.foreign_turret_var(owner.body_script.world,
                                                         turret.actor.entity, 253), NotImplemented)
            self.assertFalse(hero_torbjorn.turret_message(owner.body_script.world, turret.actor.entity,
                                                        0x024000000000003F, owner.body))
        finally:
            component.strict = False
        self.assertIs(run.match.combat.turrets[owner.entity], turret)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five-NPC catalog")
    def test_fighting_each_actual_npc_rejects_redeploy_without_stopping_damage(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, _, turret = self.arena(100.0)
                x, y, z = owner.mover.position
                actor = npc.spawn(run.match, definition, (x, y, z + 8), team=1 - owner.team)
                self.advance(run, 230)
                fight = run.match.combat
                hp = fight.vitals[actor.entity]
                before = hp.total
                self.assertLess(before, definition.health + definition.shields)
                run.step({owner: (0, 4000, 8)})
                self.advance(run, 40)
                self.assertIs(fight.turrets[owner.entity], turret)
                self.assertLess(hp.total, before)
                ability = next(i for i in owner.body_script.component.instances.values()
                               if i.graph.index == 0x16C5).states[13]
                self.assertEqual(ability.remaining(), 0.0)
                self.assertFalse(ability.flags & ability.RUNNING)
    def test_respawn_rebinds_the_surviving_turret_to_new_owner_body(self):
        from test_mei_ice_presentation import decode_ice_components

        run, owner, enemy, turret = self.arena(100.0)
        captured_bits = owner.team_bit
        self.assertEqual(turret.actor.spawner['source_filter_bits'], captured_bits)
        self.advance(run, 200)
        fight = run.match.combat
        old_body = owner.body
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(enemy, target, 1000.0, False, None, run.frame)
        for _ in range(650):
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertFalse(fight.is_dead(owner))
        self.assertIs(fight.turrets[owner.entity], turret)
        self.assertEqual(turret.actor.spawner['source_filter_bits'], captured_bits)
        for viewer in (owner, enemy):
            created = run.match._bot_create_update(turret.actor, viewer)
            self.assertEqual(decode_ice_components(created.build(0), create=True)[26][0], captured_bits)
        run.step({owner: (0, 0, 4)})
        self.assertNotIn(owner.entity, fight.turrets)

    def test_respawn_does_not_bypass_cooldown_from_turret_destroyed_during_death(self):
        run, owner, enemy, _ = self.arena(100.0, deploy=False)
        fight = run.match.combat
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 240)
        turret = fight.turrets[owner.entity]
        old_body = owner.body
        hp = fight.vitals[old_body]
        hp.damage(hp.total - 1)
        x, y, z = owner.mover.position
        enemy.mover.position = [x + 4, y, z]
        enemy.mover.body = None
        self.advance(run, 3)
        run.step({enemy: (*aim(fight._eye(enemy), (x, y + .6, z)), 1)})
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, enemy.body)
        respawn = next(when for when, who in fight.respawns if who is owner)
        enemy.mover.position = [x + 100, y, z]
        enemy.mover.body = None
        while fight.now < respawn - .5:
            run.step({})

        tx, ty, tz = turret.actor.position
        enemy.mover.position = [tx + 4, ty, tz]
        enemy.mover.body = None
        self.advance(run, 3)
        turret_hp = fight.vitals[turret.actor.entity]
        turret_hp.damage(turret_hp.total - 1)
        run.step({enemy: (*aim(fight._eye(enemy), (tx, ty + .5, tz)), 1)})
        self.assertTrue(turret.actor.dead)
        killed_at = turret_hp.died_at
        self.assertEqual(turret_hp.killer, enemy.body)
        while fight.now < respawn + .1:
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertNotIn(owner.entity, fight.turrets)
        while fight.now < killed_at + 9.7:
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
            self.assertNotIn(owner.entity, fight.turrets)
            self.assertFalse(any(
                r.shooter is owner and r.spawn == hero_torbjorn.TURRET for r in fight.rockets))
        for _ in range(100):
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
            if owner.entity in fight.turrets:
                break
        self.assertIn(owner.entity, fight.turrets)

    def test_same_hero_reselect_cannot_bypass_dismantle_cooldown(self):
        run, owner, _, _ = self.arena(100.0, deploy=False)
        match = run.match
        fight = match.combat
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 240)
        self.assertIn(owner.entity, fight.turrets)
        run.step({owner: (0, 4000, 4)})
        self.assertNotIn(owner.entity, fight.turrets)
        self.advance(run, 90)
        old_body = owner.body
        remaining = hero_torbjorn.turret_cooldown_remaining(owner)
        self.assertGreater(remaining, 8)
        self.assertTrue(match.switch_hero(owner, owner.hero))
        self.assertNotEqual(owner.body, old_body)
        self.assertAlmostEqual(hero_torbjorn.turret_cooldown_remaining(owner), remaining)
        deadline = fight.now + remaining
        self.advance(run, 30)
        old_body = owner.body
        remaining = hero_torbjorn.turret_cooldown_remaining(owner)
        self.assertAlmostEqual(remaining, deadline - fight.now, delta=.02)
        match.skin_of = lambda *_: (0, True)
        self.assertTrue(match.skin_changed(owner, owner.hero.guid))
        self.assertNotEqual(owner.body, old_body)
        self.assertEqual(owner.skin, (0, True))
        self.assertAlmostEqual(hero_torbjorn.turret_cooldown_remaining(owner), remaining)
        while fight.now < deadline - .25:
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
            self.assertNotIn(owner.entity, fight.turrets)
            self.assertFalse(any(
                r.shooter is owner and r.spawn == hero_torbjorn.TURRET for r in fight.rockets))
        for _ in range(100):
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
            if owner.entity in fight.turrets:
                break
        self.assertIn(owner.entity, fight.turrets)

    def test_different_hero_round_trip_cannot_bypass_dismantle_cooldown(self):
        run, owner, _, _ = self.arena(100.0, deploy=False)
        match = run.match
        fight = match.combat
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 240)
        old_turret = fight.turrets[owner.entity]
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x16C5)
        instance.write((expr.ENTITY, 3652), 1)
        run.step({owner: (0, 4000, 4)})
        self.assertNotIn(old_turret.actor, match.bots)
        self.assertAlmostEqual(hero_torbjorn.turret_cooldown_remaining(owner), 5, delta=.02)
        deadline = fight.now + hero_torbjorn.turret_cooldown_remaining(owner)

        torb = owner.hero
        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 0)
        self.advance(run, 120)
        self.assertTrue(match.switch_hero(owner, torb))
        self.assertAlmostEqual(
            hero_torbjorn.turret_cooldown_remaining(owner), deadline - fight.now, delta=.02)
        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
        self.advance(run, 100)
        self.assertTrue(match.switch_hero(owner, torb))
        self.assertAlmostEqual(
            hero_torbjorn.turret_cooldown_remaining(owner), deadline - fight.now, delta=.02)
        while fight.now < deadline - .25:
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
            self.assertNotIn(owner.entity, fight.turrets)
            self.assertFalse(any(
                r.shooter is owner and r.spawn == hero_torbjorn.TURRET for r in fight.rockets))
        for _ in range(100):
            run.step({owner: (0, 4000, 8 if run.frame % 2 else 0)})
            if owner.entity in fight.turrets:
                break
        fresh = fight.turrets[owner.entity]
        self.assertIsNot(fresh, old_turret)
        self.assertIs(fresh.actor.combat_owner, owner)
        self.assertNotIn(old_turret.actor, match.bots)

    def test_reselect_resumes_paused_turret_cooldown_without_resetting_it(self):
        run, owner, _, _ = self.arena(100.0, deploy=False)
        match = run.match
        fight = match.combat
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 240)
        old_turret = fight.turrets[owner.entity]
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x16C5)
        instance.write((expr.ENTITY, 3652), -1)
        run.step({owner: (0, 4000, 4)})
        self.assertNotIn(old_turret.actor, match.bots)
        self.advance(run, 100)
        self.assertEqual(instance.state(13).cooldown_rate, 0)
        self.assertEqual(instance.state(13).remaining(), 10)
        self.assertTrue(match.switch_hero(owner, owner.hero))
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 10)
        deadline = fight.now + 10
        run.step({owner: (0, 4000, 8)})
        while fight.now < deadline + .05:
            run.step({})
            self.assertNotIn(owner.entity, fight.turrets)
            self.assertFalse(any(r.spawn == hero_torbjorn.TURRET for r in fight.rockets))
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 100)
        self.assertIs(fight.turrets[owner.entity].owner, owner)

    def test_first_throw_is_accepted_when_dismantle_cooldown_expires_on_another_hero(self):
        run, owner, _, _ = self.arena(100.0, deploy=False)
        match = run.match
        fight = match.combat
        torb = owner.hero
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 240)
        self.assertIn(owner.entity, fight.turrets)
        run.step({owner: (0, 4000, 4)})
        self.assertNotIn(owner.entity, fight.turrets)
        remaining = hero_torbjorn.turret_cooldown_remaining(owner)
        self.assertGreater(remaining, 9.9)
        deadline = fight.now + remaining
        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
        while fight.now <= deadline:
            run.step({})
        self.assertTrue(match.switch_hero(owner, torb))
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 0)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 100)
        self.assertIs(fight.turrets[owner.entity].owner, owner)

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

    def test_landed_enemy_molten_destroys_actual_turret_and_gates_redeployment(self):
        from test_game_torbjorn_defenses import TorbjornDefenseTests
        from test_game_torbjorn_phases import TorbjornPhaseTests

        fixture = TorbjornPhaseTests()
        place = TorbjornDefenseTests.place
        hero = heroes()[hero_torbjorn.HERO]
        run, defender, attacker, _ = fixture.arena(hero.name, 100, 100)
        fight = run.match.combat
        run.step({defender: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[defender.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        x, y, z = turret.actor.position
        place(defender, (x + 50, y, z + 50))
        attacker.body_script.set_ult_charge(10000)
        run.step({attacker: (0, 0, 32)})
        self.advance(run, 46)
        place(attacker, (x + 1.8, y, z))
        direction = aim(fight._eye(attacker), (x + 1.8, y, z + .1))
        run.step({attacker: (*direction, 1)})
        self.assertTrue(any(rocket.spawn == molten_core.POOL for rocket in fight.rockets))
        place(attacker, (x + 100, y, z + 100))
        for _ in range(25):
            run.step({})
            if fight.molten_pools:
                break
        pool, = fight.molten_pools.values()
        self.assertEqual(hp.total, 250)

        key = attacker.entity, turret.actor.entity
        for _ in range(60):
            run.step({})
            if hp.total < 250:
                break
        self.assertIn(key, fight.molten_burns)
        self.assertAlmostEqual(hp.total, 248.08)
        before = hp.total
        self.advance(run, 5)
        self.assertAlmostEqual(before - hp.total, 12.8)
        for _ in range(120):
            run.step({})
            if turret.actor.dead:
                break
        self.assertTrue(turret.actor.dead)
        self.assertEqual(hp.killer, attacker.body)
        self.assertEqual(hp.death_tick, run.frame)
        self.assertNotIn(key, fight.molten_burns)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertAlmostEqual(attacker.stats.value(stats.HERO_DAMAGE), 250)
        self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
        self.assertAlmostEqual(hero_torbjorn.turret_cooldown_remaining(defender), 10, delta=.02)

        place(defender, (x + 7, y, z))
        run.step({defender: (0, 4000, 8)})
        self.assertNotIn(defender.entity, fight.turrets)
        self.assertFalse(any(rocket.spawn == hero_torbjorn.TURRET for rocket in fight.rockets))
        run.frame += 540
        run.step({})
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(defender), 0)
        run.step({defender: (0, 4000, 8)})
        self.assertFalse(any(rocket.spawn == hero_torbjorn.TURRET for rocket in fight.rockets))

        for _ in range(115):
            run.step({})
            if hero_torbjorn.turret_cooldown_remaining(defender) == 0:
                break
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(defender), 0)
        run.step({defender: (0, 4000, 8)})
        for _ in range(70):
            if defender.entity in fight.turrets:
                break
            run.step({})
        self.assertNotEqual(fight.turrets[defender.entity].actor.entity, turret.actor.entity)

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

    def test_lethal_enemy_input_starts_cooldown_before_the_next_world_update(self):
        run, owner, enemy, turret = self.arena(100.0)
        self.advance(run, 190)
        fight = run.match.combat
        hp = fight.vitals[turret.actor.entity]
        hp.now[world.HEALTH_POOL] = 1.0
        fight._sync_body_health(turret.actor, hp)
        x, y, z = turret.actor.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 0.5, z))
        run.frame += 1
        # Two independently received inputs can be consumed in one world tick.
        enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertTrue(turret.actor.dead)
        owner.take_commands([Command(run.frame, pitch=4000, buttons=8)])
        ability = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).states[13]
        self.assertGreater(ability.remaining(), 9.9)
        self.assertFalse(ability.flags & ability.RUNNING)

    def ranged_npc_turret_encounter(self, definition):
        run, owner, bait, _ = self.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        bait.team = owner.team
        self.assertTrue(match.switch_hero(bait, heroes()[hero_torbjorn.HERO]))
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[owner.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        x, y, z = turret.actor.position
        bot = npc.spawn(match, definition, (x, y, z + 8), team=1 - owner.team)
        bait.mover.position = [x + 3, y, z - .75]
        bait.mover.body = None
        self.advance(run, 2)
        yaw, pitch = aim(fight._eye(bait), (x, y + .85, z + 8))
        run.step({bait: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(match, bait), bot.entity)
        bait.mover.position = [x, y, z - .75]
        bait.mover.body = None
        bait_hp = fight.vitals_of(next(target for target in fight._targets() if target.owner is bait))
        return run, owner, bait, turret, bot, hp, bait_hp

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_actual_ranged_npc_fire_destroys_turret_and_starts_redeploy_cooldown(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            if definition.name == "Assassin":
                continue
            with self.subTest(npc=definition.name):
                run, owner, bait, turret, bot, hp, bait_hp = self.ranged_npc_turret_encounter(definition)
                match = run.match
                fight = match.combat
                hp.damage(249)
                fight._sync_body_health(turret.actor, hp)
                for _ in range(170):
                    run.step({bait: (0, 0, CROUCH)})
                    if hp.dead:
                        break
                self.assertTrue(hp.dead, (hp.total, bait_hp.total, bot.dead))
                self.assertEqual(hp.killer, bot.entity)
                self.assertFalse(bait_hp.dead)
                npc_control.stop(match)
                ability = next(i for i in owner.body_script.component.instances.values()
                               if i.graph.index == 0x16C5).state(13)
                self.assertGreater(ability.remaining(), 9.9)
                run.step({owner: (0, 4000, 8)})
                self.advance(run, 15)
                self.assertNotIn(owner.entity, fight.turrets)
                for _ in range(650):
                    if ability.remaining() <= 0:
                        break
                    run.step({})
                self.assertEqual(ability.remaining(), 0)
                run.step({owner: (0, 4000, 8)})
                self.advance(run, 100)
                self.assertNotEqual(fight.turrets[owner.entity].actor.entity, turret.actor.entity)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_full_health_turret_trades_fire_with_all_four_ranged_npcs(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        remaining_health = {"Trooper": (70, 0), "Enforcer": (166, 0), "Sniper": (50, 0),
                            "Heavy Assault": (0, 3776)}
        for definition in catalog.values():
            if definition.name == "Assassin":
                continue
            with self.subTest(npc=definition.name):
                run, owner, bait, turret, bot, hp, bait_hp = self.ranged_npc_turret_encounter(definition)
                npc_hp = run.match.combat.vitals[bot.entity]
                start = npc_hp.maximum
                for _ in range(650):
                    run.step({bait: (0, 0, CROUCH)})
                    if hp.dead or npc_hp.dead:
                        break
                self.assertLess(hp.total, 250)
                self.assertLess(npc_hp.total, start)
                self.assertFalse(bait_hp.dead)
                self.assertEqual((hp.total, npc_hp.total), remaining_health[definition.name])
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), start - npc_hp.total)
                if hp.dead:
                    self.assertEqual(hp.killer, bot.entity)
                    ability = next(i for i in owner.body_script.component.instances.values()
                                   if i.graph.index == 0x16C5).state(13)
                    self.assertGreater(ability.remaining(), 9.9)
                else:
                    self.assertEqual(npc_hp.killer, owner.body)
                    self.assertIs(run.match.combat.turrets[owner.entity], turret)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_held_npc_fire_reaches_overload_after_destroying_the_nearer_turret(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        armor_damage = {"Trooper": 15, "Enforcer": 9.514538, "Sniper": 95, "Heavy Assault": 2.5}
        for definition in catalog.values():
            name = definition.name
            if name not in armor_damage:
                continue
            with self.subTest(npc=name):
                run, owner, bait, turret, bot, hp, bait_hp = self.ranged_npc_turret_encounter(definition)
                fight = run.match.combat
                npc_hp = fight.vitals[bot.entity]
                hp.damage(249)
                fight._sync_body_health(turret.actor, hp)
                base = dict(bait_hp.now)
                if name != "Sniper":
                    run.step({bait: (0, 0, CROUCH | 16)})
                    self.assertEqual(bait_hp.over_armour.amount, 100)
                for _ in range(170):
                    if hp.dead:
                        break
                    run.step({bait: (0, 0, CROUCH)})
                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, bot.entity)
                self.assertEqual(bait_hp.now, base)
                self.assertTrue(bot.attack_input)
                dealt = npc_hp.maximum - npc_hp.total
                self.assertGreater(dealt, 0)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), dealt)

                if name == "Sniper":
                    run.step({bait: (0, 0, CROUCH | 16)})
                armor_source = bait_hp.over_armour.source
                self.assertIsNotNone(armor_source)
                self.assertEqual(bait_hp.over_armour.amount, 100)
                for _ in range(300):
                    run.step({bait: (0, 0, CROUCH)})
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(bot.attack_target, bait.entity)
                    self.assertEqual(bait_hp.over_armour.source, armor_source)
                    if bait_hp.over_armour.amount < 100:
                        break
                self.assertAlmostEqual(100 - bait_hp.over_armour.amount, armor_damage[name], places=4)
                self.assertEqual(bait_hp.now, base)
                self.assertNotIn(owner.entity, fight.turrets)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs Talon Sniper catalog")
    def test_hammer_repair_before_or_on_sniper_impact_frame(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        sniper = next(row for row in catalog.values() if row.name == "Sniper")
        for repair_first in (False, True):
            with self.subTest(repair_first=repair_first):
                run, owner, bait, turret, bot, hp, bait_hp = self.ranged_npc_turret_encounter(sniper)
                fight = run.match.combat
                x, y, z = turret.actor.position
                owner.mover.position = [x - 1.25, y, z]
                owner.mover.body = None
                start = run.frame
                run.frame += 1
                owner.take_commands([Command(run.frame, action=2)])
                hp.damage(175)
                fight._sync_body_health(turret.actor, hp)
                yaw, pitch = aim(fight._eye(owner), (x, y + .5, z))
                swing_frame = 109 if repair_first else 110
                bait_before = bait_hp.total
                with patch.object(hp, "heal", wraps=hp.heal) as heal:
                    for frame in range(2, 128):
                        run.step({owner: (yaw, pitch, int(frame == swing_frame)), bait: (0, 0, CROUCH)})
                    contact = fight.hammer_contacts[owner.body]
                    self.assertEqual(hp.total, 75)
                    self.assertFalse(contact.repaired)
                    heal.assert_not_called()
                    run.step({owner: (yaw, pitch, 0), bait: (0, 0, CROUCH)})
                    self.assertEqual(run.frame, start + 128)
                    self.assertEqual(hp.total, 125 if repair_first else 75)
                    run.step({owner: (yaw, pitch, 0), bait: (0, 0, CROUCH)})
                    self.assertEqual(run.frame, start + 129)
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(bait_hp.total, bait_before)
                    self.assertIs(fight.hammer_contacts[owner.body], contact)
                    self.assertLessEqual(contact.begin, owner.body_script.component.now)
                    self.assertLess(owner.body_script.component.now, contact.end)

                    self.assertEqual(hp.total, 25 if repair_first else 0)
                    if repair_first:
                        self.assertIs(fight.turrets[owner.entity], turret)
                        self.assertEqual(contact.repaired, {turret.actor.entity})
                    else:
                        self.assertEqual(hp.killer, bot.entity)
                        self.assertNotIn(owner.entity, fight.turrets)
                        self.assertFalse(contact.repaired)
                        ability = next(i for i in owner.body_script.component.instances.values()
                                       if i.graph.index == 0x16C5).state(13)
                        self.assertGreater(ability.remaining(), 9.9)
                    for _ in range(12):
                        run.step({owner: (yaw, pitch, 0), bait: (0, 0, CROUCH)})
                    if repair_first:
                        heal.assert_called_once_with(50)
                    else:
                        heal.assert_not_called()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs Heavy Assault catalog")
    def test_actual_hammer_repairs_turret_under_heavy_assault_fire(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        heavy = next(row for row in catalog.values() if row.name == "Heavy Assault")
        run, owner, bait, turret, bot, hp, _ = self.ranged_npc_turret_encounter(heavy)
        fight = run.match.combat
        x, y, z = turret.actor.position
        owner.mover.position = [x - 1.25, y, z]
        owner.mover.body = None
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        yaw, pitch = aim(fight._eye(owner), (x, y + .5, z))
        healed = []
        heal = hp.heal

        def record(amount):
            healed.append(heal(amount))
            return healed[-1]

        with patch.object(hp, "heal", side_effect=record) as repairs:
            for frame in range(220):
                run.step({owner: (yaw, pitch, 1 if frame >= 45 else 0), bait: (0, 0, CROUCH)})
                if hp.dead:
                    break
        self.assertFalse(hp.dead, healed)
        self.assertTrue(bot.attack_input)
        self.assertTrue(all(call.args == (50.0,) for call in repairs.call_args_list))
        self.assertGreaterEqual(sum(healed), 50)
        self.assertGreater(250 + sum(healed) - hp.total, 100)
        self.assertIs(fight.turrets[owner.entity], turret)
        for _ in range(350):
            run.step({bait: (0, 0, CROUCH)})
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, bot.entity)
        ability = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).state(13)
        self.assertGreater(ability.remaining(), 9.9)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_hammer_repairs_actual_trooper_and_enforcer_damage_before_return_fire(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            if definition.name not in ("Trooper", "Enforcer"):
                continue
            with self.subTest(npc=definition.name):
                self.exercise_hammer_repair_between_npc_volleys(definition)

    def exercise_hammer_repair_between_npc_volleys(self, definition):
        run, owner, bait, turret, bot, hp, bait_hp = self.ranged_npc_turret_encounter(definition)
        fight = run.match.combat
        npc_hp = fight.vitals[bot.entity]
        x, y, z = turret.actor.position
        owner.mover.position = [x - 1.25, y, z]
        owner.mover.body = None
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        yaw, pitch = aim(fight._eye(owner), (x, y + .5, z))

        # Each actor opens fire on the allied bait through the nearer turret.
        # One hammer swing lands after the first volley and before the next.
        trooper = definition.name == "Trooper"
        swing_frame, repair_frame, return_frame = (46, 65, 113) if trooper else (70, 89, 190)
        before, after, damaged_again = (190, 240, 180) if trooper else (222, 250, 222)
        for frame in range(2, repair_frame):
            run.step({owner: (yaw, pitch, int(frame == swing_frame)), bait: (0, 0, CROUCH)})
        self.assertTrue(bot.attack_input)
        self.assertEqual(hp.total, before)
        contact = fight.hammer_contacts[owner.body]
        self.assertFalse(contact.repaired)

        run.step({owner: (yaw, pitch, 0), bait: (0, 0, CROUCH)})
        self.assertEqual(hp.total, after)
        self.assertEqual(contact.repaired, {turret.actor.entity})
        shots_at_repair = turret.shots
        for _ in range(return_frame - repair_frame):
            run.step({bait: (0, 0, CROUCH)})
        self.assertEqual(hp.total, damaged_again)
        self.assertGreater(turret.shots, shots_at_repair)
        self.assertEqual(npc_hp.maximum - npc_hp.total, 14 * turret.shots)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14 * turret.shots)
        self.assertIs(fight.turrets[owner.entity], turret)
        self.assertFalse(bait_hp.dead)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Assassin data")
    def test_actual_turret_fire_kills_assassin_and_releases_owners_pin(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Assassin")
        run, owner, other, _ = self.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        other.team = 1 - owner.team
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[owner.entity]
        x, y, z = turret.actor.position
        turret_hp = fight.vitals[turret.actor.entity]
        original = tuple(other.mover.position)
        other.mover.position = [x, y, z + 6]
        other.mover.body = None
        direction = aim(fight._eye(other), (x, y + .6, z))
        for _ in range(60):
            run.step({other: (*direction, 1)})
            if turret_hp.total <= 195:
                break
        self.assertLessEqual(turret_hp.total, 195)
        before = turret_hp.total
        other.mover.position = list(original)
        other.mover.body = None
        other.team = owner.team
        owner.mover.position = [x - 1.6, y, z]
        owner.mover.body = None
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        self.advance(run, 45)
        bot = npc.spawn(match, definition, (x - 1.6, y, z + 1.5), team=1 - owner.team)
        hp = fight.vitals[bot.entity]
        owner_hp = fight.vitals[owner.body]
        self.advance(run, 2)
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertTrue(fight.is_stunned(owner))
        repair_aim = aim(fight._eye(owner), (x, y + .4, z))
        for _ in range(20):
            run.step({owner: (*repair_aim, 1)})
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            self.assertFalse(owner.body_script.component.held)
            self.assertEqual(turret_hp.total, before)
        shots_at_pin = turret.shots
        # Put the live pinned encounter one turret shot from a lethal hit.
        self.assertGreater(hp.total, 14)
        hp.damage(hp.total - 14)
        fight._sync_body_health(bot, hp)
        for _ in range(30):
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            run.step({})
            if bot.dead:
                break
        self.assertTrue(bot.dead, (hp.total, turret.shots, turret.target))
        self.assertEqual(hp.killer, owner.body)
        self.assertGreater(turret.shots, shots_at_pin)
        self.assertIs(fight.turrets[owner.entity], turret)
        self.assertLess(owner_hp.total, 250)
        self.assertFalse(owner_hp.dead)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(owner))
        run.step({})
        run.step({owner: (*repair_aim, 1)})
        for _ in range(35):
            run.step({owner: (*repair_aim, 0)})
        self.assertEqual(turret_hp.total, before + 50)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Assassin data")
    def test_turret_lethal_hit_releases_pin_and_fills_molten_on_the_same_frame(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Assassin")
        run, owner, other, _ = self.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        other.team = owner.team
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[owner.entity]
        x, y, z = turret.actor.position
        owner.mover.position = [x - 1.6, y, z]
        owner.mover.body = None
        clear = match.collision_world
        match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - .8, z - 2, x - .8, z + 3, y0=y, y1=y + 4)
        )
        bot = npc.spawn(match, definition, (x - 1.6, y, z + 1.5), team=1 - owner.team)
        while run.frame < bot.records_from:
            run.step({})
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            if fight.pins.get(bot.entity) == owner.body:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(turret.shots, 0)
        hp = fight.vitals[bot.entity]
        hp.damage(hp.total - 14, armour_reduction=False)
        fight._sync_body_health(bot, hp)
        script = owner.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        charge = script.component.entity_var(342)
        script.set_ult_charge(cost - 14)
        ultimate, = script.ultimates
        for _ in range(3):
            run.step({owner: (16384, 4000, 32)})
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            self.assertEqual(charge.value(), cost - 14)
            self.assertFalse(ultimate.presses or ultimate.flags & nodes.Ability.RUNNING)

        match.collision_world = clear
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(bot.dead)
        self.assertEqual(hp.killer, owner.body)
        self.assertEqual(turret.shots, 1)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(charge.value(), cost)
        owner.take_commands([Command(run.frame, yaw=16384, pitch=4000, buttons=32)])
        other.take_commands([Command(run.frame)])
        self.assertEqual(charge.value(), 0)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.advance(run, 45)
        run.step({owner: (16384, 4000, 1)})
        globule, = fight.rockets
        self.assertIs(globule.shooter, owner)
        self.assertEqual(globule.spawn, molten_core.POOL)
        self.advance(run, 65)
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, owner)
        self.assertIs(fight.turrets[owner.entity], turret)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Assassin data")
    def test_turret_resumes_fire_on_full_health_assassin_when_cover_opens_during_pin(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Assassin")
        run, owner, other, _ = self.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        other.team = owner.team
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[owner.entity]
        x, y, z = turret.actor.position
        owner.mover.position = [x - 1.6, y, z]
        owner.mover.body = None
        clear = match.collision_world
        match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - .8, z - 2, x - .8, z + 3, y0=y, y1=y + 4)
        )
        bot = npc.spawn(match, definition, (x - 1.6, y, z + 1.5), team=1 - owner.team)
        hp = fight.vitals[bot.entity]
        old_body = owner.body
        owner_hp = fight.vitals[old_body]
        self.advance(run, 2)
        direction = aim(fight._eye(owner), (x - 1.6, y + .9, z + 1.5))
        run.step({owner: (*direction, 16)})
        self.assertEqual(owner_hp.total, 350)
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            if fight.pins.get(bot.entity) == owner.body:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(owner_hp.over_armour.amount, 80)
        self.assertEqual(hp.total, hp.maximum)
        self.assertEqual(turret.shots, 0)
        rifle = next(i for i in owner.body_script.component.instances.values()
                     if i.graph.index == 0x1683)
        for _ in range(20):
            run.step({owner: (*direction, 1)})
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            self.assertEqual(hp.total, hp.maximum)
        self.assertEqual(turret.shots, 0)

        match.collision_world = clear
        for _ in range(620):
            if not owner_hp.dead:
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
            self.assertEqual(rifle.vars[53].value(), 18)
            run.step({owner: (*direction, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead, (hp.total, hp.maximum, turret.shots, owner_hp.total))
        self.assertEqual(hp.maximum, 500)
        self.assertEqual(turret.shots, 36)
        self.assertEqual(hp.killer, old_body)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), hp.maximum)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(rifle.vars[53].value(), 18)
        self.assertTrue(owner_hp.dead, (owner_hp.total, turret.shots, hp.maximum))
        self.assertLess(owner_hp.death_tick, hp.death_tick)
        self.assertEqual(owner_hp.killer, bot.entity)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertIs(fight.turrets[owner.entity], turret)

        for _ in range(750):
            if owner.body != old_body:
                break
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertIs(fight.turrets[owner.entity], turret)
        self.assertIs(turret.actor.combat_owner, owner)
        other.team = 1 - owner.team
        x, y, z = owner.mover.position
        other.mover.position = [x, y, z + 4]
        other.mover.body = None
        other_hp = fight.vitals[other.body]
        before = other_hp.total
        direction = aim(fight._eye(owner), (x, y + .9, z + 4))
        run.step({owner: (*direction, 1)})
        self.advance(run, 20)
        new_rifle = next(i for i in owner.body_script.component.instances.values()
                         if i.graph.index == 0x1683)
        self.assertEqual(new_rifle.vars[53].value(), 17)
        self.assertEqual(before - other_hp.total - 14 * (turret.shots - 36), 70)

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

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_thrown_turret_keeps_owner_and_fires_after_each_npc_kill_and_respawn(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition, airborne in product(catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, airborne=airborne):
                run, owner, other, _ = self.arena(100.0, deploy=False)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                other.team = owner.team
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                if not airborne:
                    run.step({owner: (0, 4000, 8)})
                    self.advance(run, 240)
                    turret = fight.turrets[owner.entity]
                    self.assertEqual(turret.shots, 0)
                x, y, z = owner.mover.position
                distance = 1.5 if definition.name == "Assassin" else 5
                bot = npc.spawn(match, definition, (x, y, z + distance), team=1 - owner.team)
                while run.frame < bot.records_from:
                    run.step({})
                sniper_winding_up = airborne and definition.name == "Sniper"
                if sniper_winding_up:
                    self.assertEqual(npc_control.start(match, owner), bot.entity)
                    self.advance(run, 25)
                if airborne:
                    run.step({owner: (0, -12000, 8)})
                    for _ in range(35):
                        if any(r.spawn == hero_torbjorn.TURRET for r in fight.rockets):
                            break
                        run.step({owner: (0, -12000, 0)})
                    flight, = (r for r in fight.rockets if r.spawn == hero_torbjorn.TURRET)
                    run.step({owner: (0, 0, 0)})
                old_body = owner.body
                hp = fight.vitals[old_body]
                hp.damage(hp.total - 1)
                if not sniper_winding_up:
                    self.assertEqual(npc_control.start(match, owner), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hp.dead:
                        break
                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, bot.entity)
                npc_control.stop(match)
                if airborne:
                    self.assertIn(flight, fight.rockets)
                    self.assertNotIn(owner.entity, fight.turrets)
                    for _ in range(400):
                        if owner.entity in fight.turrets:
                            break
                        run.step({})
                    turret = fight.turrets[owner.entity]
                    self.assertNotIn(flight, fight.rockets)
                    tx, ty, tz = turret.actor.position
                    bot.position = (tx + 5, ty, tz)
                    self.advance(run, 187)
                bot_hp = fight.vitals[bot.entity]

                def check_next_shot(run, owner, turret, bot_hp):
                    before = bot_hp.total
                    credit = owner.stats.value(stats.HERO_DAMAGE)
                    for _ in range(30):
                        run.step({})
                        if bot_hp.total < before:
                            break
                    self.assertAlmostEqual(before - bot_hp.total, 14)
                    self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 14)
                    self.assertIs(run.match.combat.turrets[owner.entity], turret)
                    self.assertIs(turret.actor.combat_owner, owner)

                check_next_shot(run, owner, turret, bot_hp)
                self.assertEqual(owner.body, old_body)
                bot.team = owner.team
                for _ in range(750):
                    if owner.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(owner.body, old_body)
                bot.team = 1 - owner.team
                check_next_shot(run, owner, turret, bot_hp)

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

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_thrown_turret_reacquires_each_npc_after_team_range_and_wall_rejection(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, other, _ = self.arena(100, deploy=False)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                other.team = owner.team
                run.step({owner: (0, 4000, 8)})
                self.advance(run, 100)
                turret = fight.turrets[owner.entity]
                x, y, z = turret.actor.position
                near = (x, y, z + 8)
                bot = npc.spawn(match, definition, near, team=owner.team)
                hp = fight.vitals[bot.entity]
                before = hp.total
                self.advance(run, 250)
                self.assertIsNone(turret.target)
                self.assertEqual(turret.shots, 0)
                self.assertEqual(hp.total, before)
                bot.team = 1 - owner.team
                clear = match.collision_world
                covered = World(
                    box(x - 60, x + 60, y - 1, y, z - 60, z + 60)
                    + wall(x - 4, z + 4, x + 4, z + 4, y0=y, y1=y + 4)
                )
                for case in ("wall", "range"):
                    with self.subTest(case=case):
                        shots = turret.shots
                        match.collision_world = covered if case == "wall" else clear
                        bot.position = near if case == "wall" else (x, y, z + turret.config["range"] + 2)
                        self.advance(run, 20)
                        self.assertIsNone(turret.target)
                        self.assertEqual(turret.shots, shots)
                        self.assertEqual(before - hp.total, 14 * shots)
                        match.collision_world = clear
                        bot.position = near
                        for _ in range(20):
                            run.step({})
                            if turret.shots > shots:
                                break
                        self.assertEqual(turret.target, bot.entity)
                        self.assertEqual(turret.shots, shots + 1)
                        self.assertEqual(before - hp.total, 14 * (shots + 1))
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 28)

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
        before_damage = snapshot_digest(case.capture(session))
        turret.last_damaged = owner.match.combat.now
        self.assertNotEqual(before_damage, snapshot_digest(case.capture(session)))
