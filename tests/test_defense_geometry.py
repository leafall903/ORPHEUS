"""Actual target-build shield model probes; raw assets remain external and optional."""

import os
import unittest
from pathlib import Path

from ow174.game import props
from ow174.game.defense_geometry import DefenseGeometryError, attached_meshes


class AttachedShieldGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = os.environ.get("ORPHEUS_DEFENSE_ASSET_DIR")
        if not folder:
            raise unittest.SkipTest("Supply external target model using ORPHEUS_DEFENSE_ASSET_DIR")
        cls.data = (Path(folder) / "000000000A3C.00C").read_bytes()

    def test_native_reinhardt_model_has_one_attached_mesh_and_is_not_static_map_collision(self):
        self.assertEqual(props.model_shapes(self.data), [])
        meshes = attached_meshes(self.data)
        self.assertEqual(len(meshes), 1)
        self.assertEqual((len(meshes[0].vertices), len(meshes[0].triangles)), (98, 192))
        self.assertEqual(meshes[0].source_sha256,
                         "56d88e87a4e404264bbbbb2e0b2a42248abedfd4dea5358f1d96c826b0aec9fa")

    def test_real_mesh_blocks_center_ray_and_misses_outside_actual_width(self):
        mesh, = attached_meshes(self.data)
        world = mesh.world()
        hit = world.cast_ray((0, 0, -5), (0, 0, 1), 10)
        self.assertIsNotNone(hit)
        self.assertGreater(hit.distance, 4)
        self.assertLess(hit.distance, 5)
        self.assertIsNone(world.cast_ray((3.5, 0, -5), (0, 0, 1), 10))
        self.assertIsNone(world.cast_ray((0, 3, -5), (0, 0, 1), 10))

    def test_truncation_and_missing_collision_never_manufacture_a_shield(self):
        for data in (self.data[:14], self.data[:128], b"not a model"):
            with self.subTest(length=len(data)), self.assertRaises(DefenseGeometryError):
                attached_meshes(data)


if __name__ == "__main__":
    unittest.main()
