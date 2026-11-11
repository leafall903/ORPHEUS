"""Actual Enforcer primary fire through the manual NPC console and combat."""

import math
import unittest
from collections import Counter

import test_soldier_pve_static as fixture
from test_game_combat import aim, in_world

from ow174.game import combat, npc_console
from ow174.game.collision import World
from ow174.game.content import heroes


class SoldierPveEnforcerTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)

    def arena(self, distance=5):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, "2", (0, 0, distance - 3))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        angles = aim(run.match.combat._eye(soldier), (0, .8, distance - 3))
        run.step({soldier: (*angles, 0)})
        target = next(t for t in run.match.combat._targets() if t.owner is soldier)
        return run, soldier, actor, run.match.combat.vitals_of(target), npc_console.Console(self.catalog)

    def step_until_launch(self, run, actor):
        for _ in range(300):
            run.step({})
            pellets = [rocket for rocket in run.match.combat.rockets if rocket.shooter is actor]
            if pellets:
                self.assertEqual(len(pellets), 25)
                return pellets
        self.fail("Enforcer 13EB never launched a volley")

    def finish_volley(self, run, pellets):
        for _ in range(85):
            if not any(pellet in run.match.combat.rockets for pellet in pellets):
                return
            run.step({})
        self.fail("Enforcer pellets exceeded their authored range/lifetime")

    def step_until_hit(self, run, actor, pool, damage=50):
        before = pool.total
        self.finish_volley(run, self.step_until_launch(run, actor))
        self.assertEqual(before - pool.total, damage)

    def start_pending_volley(self, run, soldier, actor, console):
        component = actor.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
        ammo = weapon.find_var(0, 53)
        self.assertEqual(console.press("0", run.match, soldier)["entity"], actor.entity)
        for _ in range(35):
            run.step({})
        windup = weapon.states[weapon.graph.node(11).state]
        self.assertTrue(windup.active)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(ammo.value(), 4)
        return windup, ammo

    @staticmethod
    def blocked_world(z):
        return World([
            (-120, 0, -120, -120, 0, 120, 120, 0, 120),
            (-120, 0, -120, 120, 0, 120, 120, 0, -120),
            (-4, 0, z, 4, 0, z, 4, 4, z),
            (-4, 0, z, 4, 4, z, -4, 4, z),
            (-4, 0, z, 4, 4, z, 4, 0, z),
            (-4, 0, z, -4, 4, z, 4, 4, z),
        ])

    def test_actual_primary_damage_stop_and_restart(self):
        run, soldier, actor, pool, console = self.arena()
        self.assertEqual(console.press("0", run.match, soldier)["entity"], actor.entity)
        self.step_until_hit(run, actor, pool)
        self.assertEqual(console.press("{", run.match, soldier)["count"], 1)
        for _ in range(150):
            run.step({})
        self.assertEqual(pool.total, 150)
        self.assertFalse(actor.attack_input)
        self.assertEqual(console.press("0", run.match, soldier)["entity"], actor.entity)
        self.step_until_hit(run, actor, pool)

    def test_wall_range_and_team_gate_release_then_restore_fire(self):
        for gate in ("wall", "range", "ally"):
            with self.subTest(gate=gate):
                run, soldier, actor, pool, console = self.arena()
                console.press("0", run.match, soldier)
                self.step_until_hit(run, actor, pool)
                world, team, position = run.match.collision_world, actor.team, actor.position
                if gate == "wall":
                    run.match.collision_world = self.blocked_world(0)
                    self.assertIsNotNone(combat.ray_world(
                        run.match.collision_world, (0, 1, 2), (0, 0, -1), 5,
                    ))
                elif gate == "range":
                    actor.position = (0, 0, 102)
                else:
                    actor.team = soldier.team
                for _ in range(150):
                    run.step({})
                self.assertEqual(pool.total, 150)
                self.assertFalse(actor.attack_input)
                run.match.collision_world, actor.team, actor.position = world, team, position
                self.step_until_hit(run, actor, pool)

    def test_authored_pellet_rings_and_actual_spread_hits_and_misses(self):
        for distance, damage in ((2, 50), (9, 28), (20, 10), (100, 2)):
            with self.subTest(distance=distance):
                run, soldier, actor, pool, console = self.arena(distance)
                console.press("0", run.match, soldier)
                pellets = self.step_until_launch(run, actor)
                forward = pellets[0].direction
                angles = Counter(round(math.degrees(math.acos(min(1, combat._dot(forward, p.direction)))), 3)
                                 for p in pellets)
                self.assertEqual(angles, {0: 1, 2: 8, 4: 16})
                self.assertTrue(all(p.definition == 0x04000000000023F0 and p.speed == 80 for p in pellets))
                self.assertTrue(all(p.left == 1.25 and p.direct.amount == 2 for p in pellets))
                self.finish_volley(run, pellets)
                self.assertEqual(200 - pool.total, damage)

    def test_launched_pellets_miss_or_end_on_wall_range_team_and_stop(self):
        for action in ("move", "wall", "range", "ally", "stop"):
            with self.subTest(action=action):
                run, soldier, actor, pool, console = self.arena(9)
                console.press("0", run.match, soldier)
                pellets = self.step_until_launch(run, actor)
                if action == "stop":
                    console.press("{", run.match, soldier)
                    self.assertFalse(run.match.combat.rockets)
                else:
                    actor.firing = False  # release the controller and leave its already-fired projectiles
                    if action == "wall":
                        run.match.collision_world = self.blocked_world(3)
                    elif action == "ally":
                        soldier.team = actor.team
                    else:
                        soldier.mover.position = [3, 0, -3] if action == "move" else [0, 0, -96]
                        soldier.mover.body = None
                self.finish_volley(run, pellets)
                self.assertEqual(pool.total, 200)

    def test_held_fire_spends_one_round_per_volley_and_resumes_after_reload(self):
        run, soldier, actor, pool, console = self.arena(20)
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = instance.find_var(0, 53)
        self.assertEqual(ammo.value(), 4)
        console.press("0", run.match, soldier)
        for remaining in (3, 2, 1):
            self.finish_volley(run, self.step_until_launch(run, actor))
            self.assertEqual(ammo.value(), remaining)
        self.assertEqual(pool.total, 170)
        last = self.step_until_launch(run, actor)
        self.assertEqual(ammo.value(), 0)
        for _ in range(100):
            if ammo.value() != 0:
                break
            run.step({})
            self.assertTrue(all(rocket in last for rocket in run.match.combat.rockets))
        self.assertEqual(ammo.value(), 4)
        self.finish_volley(run, last)
        self.assertEqual(pool.total, 160)
        self.step_until_hit(run, actor, pool, damage=10)
        self.assertEqual(ammo.value(), 3)

    def test_actual_sleep_dart_preserves_last_volley_and_admitted_reload(self):
        distance = 35
        run, soldier, actor, pool, console = self.arena(distance)
        fight = run.match.combat
        ana = run.match.add_player(2, "Support", soldier.hero.guid, soldier.team, False)
        in_world(run.match, ana)
        ana.client = fixture.NpcRecordingClient(ana)
        run.players.append(ana)
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position = [2, 0, distance - 3]
        ana.mover.body = None
        _, ammo = self.start_pending_volley(run, soldier, actor, console)
        for remaining in (3, 2, 1):
            before = pool.total
            self.finish_volley(run, self.step_until_launch(run, actor))
            self.assertLess(pool.total, before)
            self.assertEqual(ammo.value(), remaining)
        before = pool.total
        last = self.step_until_launch(run, actor)
        self.assertEqual(ammo.value(), 0)
        angles = aim(fight._eye(ana), (0, .8, distance - 3))
        run.step({ana: (*angles, 8)})
        last_pose = ana.mover.last_command
        for _ in range(45):
            if fight.is_stunned(actor):
                break
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIs(ana.mover.last_command, last_pose)
        self.assertIn(actor.entity, fight.sleeps)
        self.assertTrue(fight.is_stunned(actor))
        self.assertEqual(ammo.value(), 0)
        self.assertEqual(pool.total, before)
        self.assertTrue(any(shot in fight.rockets for shot in last))
        self.finish_volley(run, last)
        self.assertTrue(fight.is_stunned(actor))
        damage = before - pool.total
        self.assertEqual(damage, 4)
        after = pool.total
        deadline = fight.sleeps[actor.entity].end
        while run.frame < deadline - 1:
            run.step({})
            self.assertTrue(fight.is_stunned(actor))
            self.assertFalse(actor.attack_input)
            self.assertFalse(fight.rockets)
            self.assertEqual(pool.total, after)
        self.assertEqual(ammo.value(), 4)
        self.assertEqual(actor.attack_target, soldier.entity)
        run.step({})
        self.assertFalse(fight.is_stunned(actor))
        self.step_until_hit(run, actor, pool, damage=damage)
        self.assertEqual(ammo.value(), 3)

    def test_soldier_field_recovers_actual_enforcer_damage(self):
        run, soldier, actor, pool, console = self.arena()
        console.press("0", run.match, soldier)
        self.step_until_hit(run, actor, pool)
        console.press("{", run.match, soldier)
        run.step({soldier: (0, 0, 0x10)})
        for _ in range(180):
            run.step({})
            if pool.total == 200:
                break
        self.assertEqual(pool.total, 200)

    def test_actual_flashbang_cancels_pending_volley_and_enforcer_recovers(self):
        run, soldier, actor, pool, console = self.arena()
        support = run.match.add_player(2, "Support", soldier.hero.guid, soldier.team, False)
        in_world(run.match, support)
        support.client = fixture.NpcRecordingClient(support)
        run.players.append(support)
        self.assertTrue(run.match.switch_hero(support, heroes()[0x02E0000000000042]))
        support.mover.position = [2, 0, 2]
        support.mover.body = None
        fight = run.match.combat
        windup, ammo = self.start_pending_volley(run, soldier, actor, console)
        angles = aim(fight._eye(support), (0, .8, 2))
        run.step({support: (*angles, 0x10)})
        for _ in range(18):
            if fight.is_stunned(actor):
                break
            run.step({support: (*angles, 0)})
        self.assertTrue(fight.is_stunned(actor))
        self.assertEqual(actor.attack_target, soldier.entity)
        for _ in range(110):
            self.assertFalse(any(shot.shooter is actor for shot in fight.rockets))
            self.assertEqual(ammo.value(), 4)
            self.assertEqual(pool.total, 200)
            if not fight.is_stunned(actor):
                break
            run.step({})
        self.assertFalse(fight.is_stunned(actor))
        self.assertFalse(windup.active)
        self.step_until_hit(run, actor, pool)
        self.assertEqual(ammo.value(), 3)

    def test_pending_volley_restarts_after_controller_gate_or_manual_stop(self):
        for gate in ("cover", "range", "team", "stop"):
            with self.subTest(gate=gate):
                run, soldier, actor, pool, console = self.arena()
                fight = run.match.combat
                windup, ammo = self.start_pending_volley(run, soldier, actor, console)
                world, team, position = run.match.collision_world, actor.team, actor.position
                if gate == "cover":
                    run.match.collision_world = self.blocked_world(0)
                elif gate == "range":
                    actor.position = (0, 0, 102)
                elif gate == "team":
                    actor.team = soldier.team
                else:
                    self.assertEqual(console.press("{", run.match, soldier)["count"], 1)
                run.step({})
                self.assertFalse(actor.attack_input)
                self.assertFalse(windup.active)
                run.match.collision_world, actor.team, actor.position = world, team, position
                if gate == "stop":
                    self.assertEqual(console.press("0", run.match, soldier)["entity"], actor.entity)
                self.assertEqual(actor.attack_target, soldier.entity)
                # Cross the old launch frame after restoring the gate.
                for _ in range(32):
                    run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(ammo.value(), 4)
                    self.assertEqual(pool.total, 200)
                self.assertTrue(actor.attack_input)
                self.step_until_hit(run, actor, pool)
                self.assertEqual(ammo.value(), 3)

    def test_soldier_rifle_kills_enforcer_before_or_after_volley_launch(self):
        for launched in (False, True):
            with self.subTest(launched=launched):
                run, soldier, actor, pool, console = self.arena(distance=9)
                fight = run.match.combat
                target = fixture.SoldierPveStaticTests.vitals(self, run, actor)
                target.damage(target.total - 20)
                windup, ammo = self.start_pending_volley(run, soldier, actor, console)
                pellets = self.step_until_launch(run, actor) if launched else []
                angles = aim(fight._eye(soldier), (0, .8, 6))
                run.step({soldier: (*angles, 1)})
                self.assertTrue(actor.dead)
                self.assertTrue(actor.body_script.component.owner_dead)
                self.assertEqual(target.killer, soldier.body)
                self.assertFalse(windup.active)
                self.assertEqual(pool.total, 200)
                if launched:
                    self.assertTrue(all(shot in fight.rockets and shot.source_entity == actor.entity
                                        for shot in pellets))
                    self.finish_volley(run, pellets)
                for _ in range(75):
                    run.step({})
                    self.assertFalse(fight.rockets)
                self.assertEqual(ammo.value(), 3 if launched else 4)
                self.assertEqual(pool.total, 172 if launched else 200)

    def test_actual_enforcer_kill_reports_source_and_soldier_respawns(self):
        run, soldier, actor, pool, console = self.arena()
        old = soldier.body
        console.press("0", run.match, soldier)
        for _ in range(4):
            self.step_until_hit(run, actor, pool)
        self.assertTrue(run.match.combat.is_dead(soldier))
        self.assertTrue(soldier.body_script.component.owner_dead)
        self.assertEqual(pool.killer, actor.entity)
        self.assertEqual(soldier.client.messages(combat.DEATH_NOTICE), [
            {"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": actor.entity}},
        ])
        console.press("{", run.match, soldier)
        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 2):
            run.step({})
        self.assertNotEqual(soldier.body, old)
        self.assertFalse(run.match.combat.is_dead(soldier))
        self.assertFalse(run.match.combat.rockets)
