"""Current combat queries retain both exact ray and movement-world layers."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_combat as fixtures
from test_game_mover import wall
from ow174.game import collision, combat, content, volskaya
from ow174.game.collision_fingerprint import world_fingerprint


class LayeredCombatQueryTests(unittest.TestCase):
    def test_exact_static_ray_geometry_and_moving_cover_both_remain_visible(self):
        base = collision.World(wall(-5, 6, 5, 6), surfaces=[7] * 4)
        base.ray_geometry = collision.RayWorld(wall(-5, 4, 5, 4), surfaces=[17] * 4)
        moving = collision.World(wall(-5, 2, 5, 2), surfaces=[23] * 4)
        layered = collision.LayeredWorld(base, moving)
        before = world_fingerprint(layered)
        origin, direction = (0., 1., 0.), (0., 0., 1.)
        with patch.object(base, 'cast_ray', side_effect=AssertionError('coarse static ray used')):
            hit = layered.ray_geometry.cast_ray(origin, direction, 10.)
            self.assertEqual((hit.distance, hit.surface), (2., 23))
            self.assertEqual(combat.ray_cover(layered.ray_geometry, origin, direction, 10., ()), 2.)
            farther = collision.LayeredWorld(base, collision.World(wall(-5, 8, 5, 8)))
            hit = farther.ray_geometry.cast_ray(origin, direction, 10.)
            self.assertEqual((hit.distance, hit.surface), (4., 17))
        self.assertEqual(world_fingerprint(layered), before)
        self.assertEqual(set(vars(layered)), {'base', 'moving', 'lowest', 'engine'})

    def test_sphere_queries_use_combined_engine_and_preserve_contact_material(self):
        base = collision.World(wall(-5, 6, 5, 6), surfaces=[17] * 4)
        moving = collision.World(wall(-5, 2, 5, 2), surfaces=[23] * 4)
        layered = collision.LayeredWorld(base, moving)
        before = world_fingerprint(layered)
        distance = layered.sweep_sphere((0., 1., 0.), (0., 0., 1.), 10., .25)
        self.assertAlmostEqual(distance, 1.75, places=5)
        hit = layered.cast_sphere((0., 1., 0.), (0., 0., 1.), 10., .25)
        self.assertAlmostEqual(hit.distance, 1.75, places=5)
        self.assertEqual(hit.surface, 23)
        hit = layered.cast_sphere((0., 1., 10.), (0., 0., -1.), 10., .25)
        self.assertAlmostEqual(hit.distance, 3.75, places=5)
        self.assertEqual(hit.surface, 17)
        self.assertEqual(world_fingerprint(layered), before)
        self.assertEqual(set(vars(layered)), {'base', 'moving', 'lowest', 'engine'})

    @unittest.skipUnless(volskaya.PLATFORM_DATA.is_file(), 'requires original platform input')
    def test_actual_fire_strike_and_ray_cover_on_live_volskaya_layer(self):
        runtime_dir = Path(os.environ.get('ORPHEUS_RUNTIME_DIR', '../combined-server-runtime'))
        base = collision.World.load(runtime_dir / 'collision/01DB.col')
        game = content.game_map(volskaya.MAP, volskaya.ASSAULT, 'Assault')
        with patch.object(fixtures, 'flat_world', return_value=base):
            run = fixtures.Run(game, ('Reinhardt',))
        match, player = run.match, run.players[0]
        self.assertTrue(match.switch_hero(player, content.heroes()[0x02E0000000000007]))
        for _ in range(20):
            run.step({})
        lift = match.scene_host.lift
        x, y, z = lift.position
        floor = lift.collider.ground(x, z, y + 4, y - 4)
        self.assertIsNotNone(floor)
        rays = match.collision_world.ray_geometry
        self.assertAlmostEqual(combat.ray_cover(rays, (x, floor.y + 2, z), (0., -1., 0.), 3., ()), 2., places=4)
        match.combat.walls.place_player(player, (x, floor.y + 2, z), ((0., 1., 0.),))
        player.mover.velocity = (0., 0., 0.)
        calls, launches = [], []
        cast = collision.LayeredWorld.cast_sphere
        shoot = match.combat._shoot
        def sphere(world, *args, **kwargs):
            calls.append(world)
            return cast(world, *args, **kwargs)
        def volley(*args, **kwargs):
            launches.append(args[2].instance.graph.index)
            return shoot(*args, **kwargs)
        angles = fixtures.aim(match.combat._eye(player), (x, floor.y, z))
        with patch.object(collision.LayeredWorld, 'cast_sphere', new=sphere), patch.object(match.combat, '_shoot', side_effect=volley):
            run.step({player: (*angles, 16)})
            for _ in range(50):
                run.step({player: (*angles, 0)})
        self.assertIn(0x00E2, launches)
        self.assertTrue(calls)
        self.assertTrue(all(type(world) is collision.LayeredWorld for world in calls))
        self.assertFalse(match.combat.rockets)
        self.assertIsNone(match.scene_host_error)
        self.assertIsNone(player.body_script.component.fault)
