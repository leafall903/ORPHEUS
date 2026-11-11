"""Boundary contracts for the independently native-checked scalar launch stage."""

import math
import unittest

from ow174.game import launch_ring


class LaunchRingTests(unittest.TestCase):
    rings = ((0.0, 1), (0.5, 8), (1.0, 16))

    def test_enforcer_center_and_both_ring_boundaries(self):
        samples = [launch_ring.angles(self.rings, i, 10, 2) for i in range(25)]
        self.assertEqual(samples[0], (0.0, 0.0))
        self.assertEqual(samples[1][0], 0.0)
        self.assertEqual(samples[9][0], 0.0)
        self.assertEqual(samples[1][1] * 2, samples[9][1])
        self.assertEqual(len(set(samples)), 25)

    def test_residual_index_is_not_clamped_to_last_pellet(self):
        angle, radius = launch_ring.angles(self.rings, 25, 10, 2)
        self.assertEqual(angle, launch_ring.TAU)
        self.assertEqual(radius, launch_ring.angles(self.rings, 24, 10, 2)[1])

    def test_noise_larger_than_spread_collapses_ring(self):
        self.assertEqual(launch_ring.angles(self.rings, 24, 1, 2)[1], 0.0)

    def test_empty_compiled_table_has_no_offset(self):
        self.assertEqual(launch_ring.angles((), 0, 10, 2), (0.0, 0.0))

    def test_native_max_preserves_unordered_width(self):
        self.assertTrue(math.isnan(launch_ring.angles(self.rings, 9, float('nan'), 2)[1]))

    def test_rejects_inputs_outside_declared_compiled_contract(self):
        for rings, index in (([], 0), (((1, 0),), 0), (((1, -1),), 0), ((), -1), ((), True)):
            with self.subTest(rings=rings, index=index), self.assertRaises(ValueError):
                launch_ring.angles(rings, index, 10, 2)

    def sample(self, **kwargs):
        defaults = {'rings': self.rings, 'index': 0, 'forward': (0, 0, 1), 'spread_degrees': 10,
                    'noise_degrees': 2, 'azimuth_radians': .3, 'noise_words': (0, 0), 'aspect': 1}
        return launch_ring.direction(**(defaults | kwargs))

    def test_full_vector_matches_original_noisy_center_reference(self):
        vector = self.sample()
        for actual, native in zip(vector, (.04363395646214485, -.023033540695905685,
                                          .9987820386886597), strict=True):
            self.assertAlmostEqual(actual, native, delta=1e-6)

    def test_aspect_shrinks_axis_without_renormalizing(self):
        vector = self.sample(aspect=.25)
        reference = self.sample()
        self.assertAlmostEqual(vector[0], reference[0], delta=1e-6)
        self.assertAlmostEqual(vector[1], reference[1] * .25, delta=1e-6)
        self.assertLess(sum(axis * axis for axis in vector), .9999)

    def test_up_axis_fallback_retains_finite_ring_offsets(self):
        vectors = [self.sample(index=i, forward=(0, 1, 0)) for i in range(25)]
        self.assertEqual(len(set(vectors)), 25)
        self.assertTrue(all(math.isfinite(axis) for vector in vectors for axis in vector))

    def test_uniform_can_reach_high_endpoint_after_uint32_float_rounding(self):
        self.assertEqual(launch_ring.uniform(0, -2, 2), -2)
        self.assertEqual(launch_ring.uniform(0x80000000, -2, 2), 0)
        self.assertEqual(launch_ring.uniform(0xFFFFFFFF, -2, 2), 2)
        with self.assertRaises(ValueError):
            launch_ring.uniform(-1, 0, 1)

    def test_vector_requires_resolved_engine_inputs_and_unit_forward(self):
        for changes in ({'forward': (0, 0, 2)}, {'forward': (0, float('nan'), 1)},
                        {'noise_words': (1,)}, {'azimuth_radians': -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.sample(**changes)
