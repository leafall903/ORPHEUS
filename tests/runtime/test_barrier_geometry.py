"""External input validation and triangle geometry, without a game data dependency."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import barrier_geometry as geometry


class BarrierGeometryTests(unittest.TestCase):
    def test_two_sided_edges_limits_and_non_rectangular_silhouette(self):
        mesh = (((0, 0, 0), (2, 0, 0), (0, 2, 0)),)
        self.assertEqual(geometry.ray_mesh(mesh, (1, 1, -2), (0, 0, 1), 2), 2)
        self.assertEqual(geometry.ray_mesh(mesh, (1, 1, 2), (0, 0, -1), 2), 2)
        self.assertEqual(geometry.ray_mesh(mesh, (0, 0, 0), (0, 0, 1), 2), 0)
        self.assertIsNone(geometry.ray_mesh(mesh, (1.5, 1.5, -2), (0, 0, 1), 3))
        self.assertIsNone(geometry.ray_mesh(mesh, (1, 1, -2), (0, 0, 1), 1.9))
        self.assertIsNone(geometry.ray_mesh(mesh, (1, 1, 2), (0, 0, 1), 3))
        self.assertIsNone(geometry.ray_mesh(mesh, (1, 1, -2), (1, 0, 0), 3))

    def test_absence_is_explicit_and_malformed_present_inputs_fail(self):
        data = {"schema": "ow174.barrier-geometry.v1", "build": "1.74.0.0.104319",
                "definition": "0x040000000000011A", "model": "0x0D00000000000A3C",
                "meshes": [{"mask": 0x1FDF, "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                            "triangles": [[0, 1, 2, 65535, 0]]}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(geometry, "DATA_DIR", Path(folder)):
            self.assertEqual(geometry.load(), ((), None))
            path = Path(folder) / geometry.GEOMETRY_FILE
            path.write_text(json.dumps(data), encoding="utf-8")
            mesh, digest = geometry.load()
            self.assertEqual(mesh, (((0, 0, 0), (1, 0, 0), (0, 1, 0)),))
            self.assertEqual(len(digest), 64)
            for defect in ("identity", "mask", "index", "nonfinite", "empty"):
                with self.subTest(defect=defect):
                    changed = copy.deepcopy(data)
                    if defect == "identity":
                        changed["model"] = "other"
                    elif defect == "mask":
                        changed["meshes"][0]["mask"] = 0
                    elif defect == "index":
                        changed["meshes"][0]["triangles"][0][0] = 3
                    elif defect == "nonfinite":
                        changed["meshes"][0]["vertices"][0][0] = float("nan")
                    else:
                        changed["meshes"] = []
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        geometry.load()


if __name__ == "__main__":
    unittest.main()
