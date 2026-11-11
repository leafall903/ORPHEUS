"""Native-generated spread math identities and cone bounds; no caller/muzzle parity claim."""

import hashlib
import math
import struct
import unittest

from ow174.game import weapon_spread


class WeaponSpreadMathTests(unittest.TestCase):
    def test_generated_tables_match_complete_native_capture_fingerprints(self):
        self.assertEqual(hashlib.sha256(struct.pack('<16384I', *weapon_spread.uniform_table())).hexdigest(),
                         '4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6')
        self.assertEqual(hashlib.sha256(struct.pack('<16384f', *weapon_spread.float_table())).hexdigest(),
                         'b3d0a3ea584bfc99c11bc39a490e7a2765c9ef74e55db6d8aafb1803c6a25281')

    def test_same_event_and_cold_cache_produce_identical_vectors(self):
        def vectors():
            return [weapon_spread.direction((0, 0, 1), math.radians(6), 77, i,
                                             gaussian=bool(i % 2)) for i in range(32)]
        expected = vectors()
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        self.assertEqual(expected, vectors())
        self.assertGreater(len(set(expected)), 24)

    def test_uniform_samples_remain_inside_authored_six_degree_cone(self):
        for forward in ((0, 0, 1), (1, 0, 0), (0, 1, 0), (0, -1, 0)):
            for count in range(128):
                vector = weapon_spread.direction(forward, math.radians(6), count, 0)
                self.assertAlmostEqual(sum(v*v for v in vector), 1, delta=5e-7)
                self.assertGreaterEqual(sum(a*b for a,b in zip(forward, vector, strict=True)),
                                        math.cos(math.radians(6)) - 1e-7)

    def test_ninety_degree_camera_keeps_native_tiny_horizontal_basis(self):
        # Captured-code D80090/884090 result, scalar libm stubs only. The
        # previous1e-7 length cutoff flipped both horizontal components.
        vector = weapon_spread.direction((0, 1, math.cos(math.pi/2)),
                                         math.radians(6), 284, 46, 3)
        expected = (-0.023236999288201332, 0.9946451783180237, -0.10070230811834335)
        for actual, native in zip(vector, expected, strict=True):
            self.assertAlmostEqual(actual, native, delta=2e-6)
