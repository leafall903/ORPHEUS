"""Synthetic fixed-step endpoint contacts through the production ray consumer."""

import json
import os
import unittest
from pathlib import Path

from ow174.game.collision import World


class ProjectileEndpointContractTests(unittest.TestCase):
    def test_wall_contact_stays_within_the_requested_step(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        fixture = json.loads((root / "projectile-endpoint-v1.json").read_text())
        z = fixture["wall_z"]
        world = World([(-2, 0, z, 2, 0, z, 2, 5, z), (-2, 0, z, 2, 5, z, -2, 5, z)])
        for case in fixture["cases"]:
            with self.subTest(case=case):
                hit = world.cast_ray((0, 2, case["origin_z"]), (0, 0, 1), case["length"])
                self.assertEqual(hit is not None, case["hit"])
                if hit is not None:
                    self.assertLessEqual(hit.distance, case["length"])
                    self.assertAlmostEqual(hit.distance, case["distance"], places=9)
