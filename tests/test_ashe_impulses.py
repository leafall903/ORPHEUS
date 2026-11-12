"""Coach Gun momentum goes through real posed movement/collision, not teleports."""

import math
import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures
import test_ashe_pve_npcs as npc_fixtures

from ow174.game import combat, npc
from ow174.game.collision import World
from ow174.game.content import HEROES_PATH, SOLDIER, heroes
from ow174.game.mover import IN_AIR, mover_data
from ow174.game.script import graph


def wall_scene(z):
    triangles = [
        (-1000, 1, -1000, -1000, 1, 1000, 1000, 1, 1000),
        (-1000, 1, -1000, 1000, 1, 1000, 1000, 1, -1000),
    ]
    # The mover's static triangle faces are one-sided. Face the approaching
    # capsule; the hitscan ray interface independently accepts either face.
    if z > 0:
        triangles.extend([(-10, -10, z, 10, 10, z, 10, -10, z),
                          (-10, -10, z, -10, 10, z, 10, 10, z)])
    else:
        triangles.extend([(-10, -10, z, 10, -10, z, 10, 10, z),
                          (-10, -10, z, 10, 10, z, -10, 10, z)])
    return World(triangles)


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class CoachImpulseTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press

    def test_cast_pushes_owner_back_and_enemy_forward_after_real_pose(self):
        run, owner, enemy = self.fight()
        self.press(run, owner, 8)
        self.assertLess(owner.mover.position[2], -1)
        self.assertGreater(enemy.mover.position[2], 2)
        self.assertGreater(enemy.mover.position[1], 1)
        self.assertTrue(enemy.mover.flags & IN_AIR)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 110)
        self.assertEqual(owner.mover.history[-1].position, tuple(owner.mover.position))
        self.assertEqual(owner.mover.history[-1].velocity, owner.mover.velocity)

    def test_reinhardt_passive_scales_real_coach_shove_on_the_next_pose(self):
        run, owner, enemy = self.fight()
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000007]))
        enemy.mover.position[:] = position
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        instances = enemy.body_script.component.instances.values()
        passive = next(item for item in instances if item.graph.index == 0x00A4)
        self.assertTrue(passive.states[4].active)
        target = next(item for item in fight._targets() if item.owner is enemy)
        before = fight.vitals_of(target).total
        with patch.object(enemy.mover, "add_impulse", wraps=enemy.mover.add_impulse) as applied:
            for frame in range(30):
                run.step({owner: (0, 0, 8 if frame < 5 else 0)})
                if enemy.body in fight.impulses.pending:
                    break
            impulse, = fight.impulses.pending[enemy.body]
            self.assertTrue(impulse.apply_resistance)
            self.assertEqual(impulse.due_tick, run.frame + 1)
            applied.assert_not_called()
            run.step({})
        applied.assert_called_once()
        for actual, raw in zip(applied.call_args.args[0], impulse.vector, strict=True):
            self.assertAlmostEqual(actual, raw * 0.7, places=5)
        self.assertNotIn(enemy.body, fight.impulses.pending)
        self.assertEqual(before - fight.vitals[enemy.body].total, 45)

    def test_coach_rulesets_scale_recoil_and_horizontal_shove_independently(self):
        for shove, recoil in ((1, 1), (0.25, 0.5), (0, 0)):
            with self.subTest(shove=shove, recoil=recoil):
                run, owner, enemy = self.fight()
                fight = run.match.combat
                engine = fight.impulses
                enemy_start = tuple(enemy.mover.position)
                world = owner.body_script.component.world
                ruleset = world.ruleset
                overrides = {0x4400: shove, 0x4401: recoil}
                def configured(key, values=overrides, base=ruleset):
                    return values[key] if key in values else base(key)

                with (patch.object(world, "ruleset", side_effect=configured),
                      patch.object(engine, "queue", wraps=engine.queue) as queued):
                    self.press(run, owner, 8)
                self.assertEqual(fight.vitals[enemy.body].total, 110)
                vectors = dict(call.args for call in queued.call_args_list)
                if shove:
                    x, y, z = vectors[enemy]
                    pre_y = max(5, 10 * shove / math.sqrt(1.25))
                    pre_z = 20 * shove / math.sqrt(1.25)
                    ratio = min(1, 20 * shove / math.hypot(pre_y, pre_z))
                    self.assertAlmostEqual(x, 0)
                    self.assertAlmostEqual(y, pre_y * ratio, places=5)
                    self.assertAlmostEqual(z, pre_z * ratio, places=5)
                    self.assertAlmostEqual(math.hypot(y, z), 20 * shove, places=5)
                else:
                    self.assertNotIn(enemy, vectors)
                    self.assertEqual(tuple(enemy.mover.position), enemy_start)
                if recoil:
                    self.assertAlmostEqual(math.hypot(*vectors[owner]), 18 * recoil)
                    self.assertLess(owner.mover.position[2], 0)
                else:
                    self.assertNotIn(owner, vectors)
                    self.assertEqual(owner.mover.position[2], 0)

    def test_downward_shot_launches_owner_and_misses_enemy(self):
        run, owner, enemy = self.fight()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200)
        self.press(run, owner, 8, frames=30, pitch=16000)
        self.assertGreater(owner.mover.position[1], 3)
        self.assertTrue(owner.mover.airborne)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)

    def test_recoil_clamps_elevation_around_the_authored_rearward_basis(self):
        for yaw, pitch, expected_elevation in ((0, -8192, 20), (8192, 0, 20),
                                               (-24576, 8192, 48.78)):
            with self.subTest(yaw=yaw, pitch=pitch):
                run, owner, _enemy = self.fight()
                engine = run.match.combat.impulses
                with patch.object(engine, "queue", wraps=engine.queue) as queued:
                    for frame in range(20):
                        run.step({owner: (yaw, pitch, 8 if frame < 5 else 0)})
                vector, = (call.args[1] for call in queued.call_args_list if call.args[0] is owner)
                x, y, z = vector
                self.assertAlmostEqual(math.hypot(*vector), 18)
                self.assertAlmostEqual(math.degrees(math.atan2(y, math.hypot(x, z))),
                                       expected_elevation, delta=0.02)
                angle = yaw * math.tau / 65536
                self.assertAlmostEqual(x / math.hypot(x, z), -math.sin(angle))
                self.assertAlmostEqual(z / math.hypot(x, z), -math.cos(angle))
                self.assertGreater(owner.mover.position[1], 1)

    def test_victim_shove_uses_cast_direction_for_off_axis_pellet_hits(self):
        run, owner, enemy = self.fight(distance=3)
        enemy.mover.position[0] = 0.28
        enemy.mover.body = None
        fight = run.match.combat
        with patch.object(fight.impulses, "queue", wraps=fight.impulses.queue) as queued:
            self.press(run, owner, 8)
        vector, = (call.args[1] for call in queued.call_args_list if call.args[0] is enemy)
        pellets = (200 - fight.vitals[enemy.body].total) / 6
        self.assertGreater(pellets, 0)
        self.assertAlmostEqual(vector[0], 0)
        self.assertAlmostEqual(vector[1], 10 * pellets / (15 * math.sqrt(1.25)), places=5)
        self.assertAlmostEqual(vector[2], 20 * pellets / (15 * math.sqrt(1.25)), places=5)

    def test_victim_rings_keep_vertical_floor_inside_and_zero_shove_outside(self):
        for distance in (12, 16):
            with self.subTest(distance=distance):
                run, owner, enemy = self.fight(distance=distance)
                fight = run.match.combat
                engine = fight.impulses
                with (patch.object(engine, "coach_targets", wraps=engine.coach_targets) as hits,
                      patch.object(engine, "queue", wraps=engine.queue) as queued):
                    self.press(run, owner, 8)
                self.assertLess(fight.vitals[enemy.body].total, 200)
                _, hit_distance, pellets = hits.call_args.args[1][enemy.body]
                vectors = [call.args[1] for call in queued.call_args_list if call.args[0] is enemy]
                if distance < 15:
                    self.assertGreater(hit_distance, 8)
                    self.assertLess(hit_distance, 15)
                    vector, = vectors
                    weight = (15 - hit_distance) / 7
                    self.assertAlmostEqual(vector[1], 5 * pellets / 15, places=5)
                    horizontal = 20 * weight * pellets / (15 * math.sqrt(1.25))
                    self.assertAlmostEqual(vector[2], horizontal, places=5)
                else:
                    self.assertGreater(hit_distance, 15)
                    self.assertEqual(vectors, [])

    def test_coach_nearest_pellet_controls_outer_ring(self):
        for surface in (15 - 1 / 512, 15, 15 + 1 / 512):
            with self.subTest(surface=surface):
                run, owner, enemy = self.fight(distance=15)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is enemy)
                enemy.mover.position[2] = surface + target.radius
                enemy.mover.body = None
                for _ in range(3):
                    run.step({})
                engine = fight.impulses
                with (patch.object(engine, "coach_targets", wraps=engine.coach_targets) as hits,
                      patch.object(engine, "queue", wraps=engine.queue) as queued,
                      patch.object(enemy.mover, "add_impulse", wraps=enemy.mover.add_impulse) as applied):
                    self.press(run, owner, 8)
                self.assertEqual(fight.vitals[enemy.body].total, 188)
                _, distance, count = hits.call_args.args[1][enemy.body]
                self.assertEqual((distance, count), (surface, 2))
                victim_shoves = [call.args[1] for call in queued.call_args_list if call.args[0] is enemy]
                if surface < 15:
                    vector, = victim_shoves
                    self.assertAlmostEqual(vector[1], 5 * count / 15)
                    applied.assert_called_once_with(vector)
                else:
                    self.assertEqual(victim_shoves, [])
                    applied.assert_not_called()

    def test_recoil_sweeps_capsule_against_wall(self):
        run, owner, _enemy = self.fight()
        run.match.collision_world = wall_scene(-1)
        self.press(run, owner, 8)
        self.assertGreaterEqual(owner.mover.position[2], -1 + owner.mover.data.stand_radius - 0.05)
        self.assertLess(owner.mover.position[2], -0.1)

    def test_enemy_knockback_cannot_cross_wall(self):
        run, owner, enemy = self.fight()
        run.match.collision_world = wall_scene(2)
        self.press(run, owner, 8)
        self.assertGreater(enemy.mover.position[2], 1)
        self.assertLessEqual(enemy.mover.position[2], 2 - enemy.mover.data.stand_radius + 0.05)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 110)

    def test_allies_receive_neither_damage_nor_knockback(self):
        run, owner, ally = self.fight(team_game=True)
        ally.team = owner.team
        before = tuple(ally.mover.position)
        self.press(run, owner, 8)
        self.assertEqual(tuple(ally.mover.position), before)
        self.assertEqual(ally.mover.velocity, (0, 0, 0))
        self.assertLess(owner.mover.position[2], -1)

    def test_spam_does_not_reapply_recoil_during_cooldown(self):
        run, owner, _enemy = self.fight()
        engine = run.match.combat.impulses
        with patch.object(engine, "recoil", wraps=engine.recoil) as recoil:
            for _ in range(8):
                self.press(run, owner, 8)
        self.assertEqual(recoil.call_count, 1)

    def test_replacement_discards_pending_old_body_impulse(self):
        run, owner, _enemy = self.fight()
        fight = run.match.combat
        old_body = owner.body
        for frame in range(60):
            run.step({owner: (0, 0, 8 if frame < 5 else 0)})
            if old_body in fight.impulses.pending:
                break
        self.assertIn(old_body, fight.impulses.pending)
        run.match.switch_hero(owner, heroes()[SOLDIER])
        self.assertNotIn(old_body, fight.impulses.pending)
        for _ in range(20):
            run.step({})
        self.assertEqual(owner.mover.velocity, (0, 0, 0))

    def test_launch_lands_without_continued_external_force(self):
        run, owner, _enemy = self.fight()
        self.press(run, owner, 8, frames=30, pitch=16000)
        for _ in range(250):
            run.step({})
        self.assertAlmostEqual(owner.mover.position[1], 1, delta=0.05)
        self.assertFalse(owner.mover.airborne)
        self.assertEqual(run.match.combat.impulses.pending, {})

    def test_dead_player_cannot_receive_queued_impulse_on_next_pose(self):
        run, owner, enemy = self.fight()
        fight = run.match.combat
        fight.impulses.queue(enemy, (0, 10, 20))
        target = next(item for item in fight._targets() if item.owner is enemy)
        fight._damage(owner, target, 500, False, None, run.frame)
        self.assertTrue(fight.is_dead(enemy))
        for _ in range(5):
            run.step({})
        self.assertNotIn(enemy.body, fight.impulses.pending)
        self.assertEqual(enemy.mover.velocity, (0, 0, 0))


@unittest.skipUnless(npc_fixtures.CATALOG.is_file() and npc_fixtures.BUNDLE.is_file(),
                     "requires actual external PvE inputs")
class NpcCoachImpulseTests(unittest.TestCase):
    setUpClass = classmethod(npc_fixtures.AshePveNpcTests.setUpClass.__func__)
    tearDownClass = classmethod(npc_fixtures.AshePveNpcTests.tearDownClass.__func__)
    fight = npc_fixtures.AshePveNpcTests.fight
    press = npc_fixtures.AshePveNpcTests.press
    spawn = npc_fixtures.AshePveNpcTests.spawn

    def test_actual_five_npcs_take_coach_damage_and_publish_resisted_movement(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, enemy = self.spawn(definition, 1)
                expected_height = {"Trooper": 1.7, "Enforcer": 1.9, "Sniper": 1.75,
                                   "Assassin": 1.5, "Heavy Assault": 2.3}[definition.name]
                self.assertEqual(mover_data(enemy.body).stand_height, expected_height)
                before = run.match.combat.vitals[enemy.entity].total
                position = enemy.position
                _yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                           (0, enemy.position[1] + 0.85, enemy.position[2]))
                self.press(run, owner, 8, pitch=pitch)
                movement = enemy.movement(run.frame)
                if definition.name == "Heavy Assault":
                    script = enemy.body_script.component
                    passive = next(item for item in script.instances.values() if item.graph.index == 0x1254)
                    self.assertTrue(passive.states[0].active)
                    self.assertEqual(enemy.position, position)
                    self.assertEqual(enemy.velocity, (0, 0, 0))
                    self.assertFalse(movement.flags & IN_AIR)
                    self.assertNotIn(enemy.entity, run.match.combat.impulses.pending)
                else:
                    self.assertGreater(enemy.position[2], 2)
                    self.assertGreater(enemy.position[1], 1)
                    self.assertGreater(enemy.velocity[2], 0)
                    self.assertTrue(movement.flags & IN_AIR)
                self.assertEqual(movement.velocity, enemy.velocity)
                self.assertEqual(before - run.match.combat.vitals[enemy.entity].total, 90)

    def test_manual_removal_discards_npc_physics_and_pending_forces(self):
        definition = next(iter(self.catalog.values()))
        run, owner, enemy = self.spawn(definition, 1)
        self.press(run, owner, 8)
        engine = run.match.combat.impulses
        self.assertIn(enemy.entity, engine.npcs)
        self.assertEqual(npc.remove_spawned(run.match), (enemy.entity,))
        self.assertNotIn(enemy.entity, engine.npcs)
        self.assertNotIn(enemy.entity, engine.pending)
        position = enemy.position
        for _ in range(50):
            run.step({})
        self.assertEqual(enemy.position, position)


if __name__ == "__main__":
    unittest.main()
