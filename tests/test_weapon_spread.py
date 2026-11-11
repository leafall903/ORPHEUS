"""Generated104319 table fingerprints and bounded native circular-cone geometry."""

import hashlib
import math
import struct
import unittest

from ow174.game import weapon_spread


class WeaponSpreadTests(unittest.TestCase):
    def test_generated_tables_match_complete_native_capture_fingerprints(self):
        self.assertEqual(hashlib.sha256(struct.pack('<16384I', *weapon_spread.uniform_table())).hexdigest(),
                         '4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6')
        self.assertEqual(hashlib.sha256(struct.pack('<16384f', *weapon_spread.float_table())).hexdigest(),
                         'b3d0a3ea584bfc99c11bc39a490e7a2765c9ef74e55db6d8aafb1803c6a25281')

    def test_event_vectors_survive_cold_cache_without_mutable_rng(self):
        def vectors():
            return [weapon_spread.direction((0, 0, 1), math.radians(2), 77, i,
                                             gaussian=bool(i % 2)) for i in range(32)]

        expected = vectors()
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        self.assertEqual(expected, vectors())
        self.assertGreater(len(set(expected)), 24)

    def test_uniform_cone_handles_horizontal_and_vertical_directions(self):
        for forward in ((0, 0, 1), (1, 0, 0), (0, 1, 0), (0, -1, 0)):
            for count in range(128):
                vector = weapon_spread.direction(forward, math.radians(2), count, 0)
                self.assertAlmostEqual(sum(v*v for v in vector), 1, delta=5e-7)
                self.assertGreaterEqual(sum(a*b for a, b in zip(forward, vector, strict=True)),
                                        math.cos(math.radians(2)) - 1e-7)

    def test_near_pole_basis_matches_original_small_squared_length_threshold(self):
        # OriginalD80090/884090 under scalar-libm adapters, capture case4.
        # The old1e-7 length cutoff flips both horizontal signs here.
        vector = weapon_spread.direction((0, 1, 6.123233995736766e-17), math.radians(6), 284, 46, 3)
        expected = (-0.023236999288201332, 0.9946451783180237, -0.10070230811834335)
        for actual, original in zip(vector, expected, strict=True):
            self.assertAlmostEqual(actual, original, delta=2e-6)
