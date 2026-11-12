"""Ring policy invariants; original-instruction vector comparisons live externally."""
import math
import unittest

from ow174.game import enforcer
from ow174.game import weapon_spread as spread


class RingSpreadTests(unittest.TestCase):
    def test_missing_and_explicit_phase_preserve_distinct_native_modes(self):
        self.assertEqual(spread.ring_phase(None), -1)
        self.assertEqual(spread.ring_phase(0), 0)
        self.assertAlmostEqual(spread.ring_phase(45), math.pi / 4, places=6)
        self.assertAlmostEqual(spread.ring_phase(-45), math.pi * 7 / 4, places=6)
        self.assertAlmostEqual(spread.ring_phase(-360), math.tau, places=6)

    def test_fixed_zero_jitter_rings_retain_the_authored_count_and_angles(self):
        rays = [spread.ring_direction((0, 0, 1), 4, 0, 0, ((0, 1), (.5, 8), (1, 16)), 123, 0, i)
                for i in range(25)]
        self.assertEqual(rays[0], (0, 0, 1))
        for ray, degrees in zip(rays[1:], [2] * 8 + [4] * 16, strict=True):
            self.assertAlmostEqual(math.degrees(math.acos(ray[2])), degrees, places=4)
        self.assertEqual(len(set(rays)), 25)

    def test_cache_reset_and_duplicate_input_keep_identical_rays_but_new_activation_changes_them(self):
        def rays(counter):
            base = spread.seed_base(0xC0001001, 3, 24, counter)
            return tuple(spread.ring_direction((0, 0, 1), 4, 2, -1,
                                               ((0, 1), (.5, 8), (1, 16)), base, 0, i) for i in range(25))
        first = rays(1)
        spread.uniform_table.cache_clear()
        self.assertEqual(first, rays(1))
        self.assertNotEqual(first, rays(2))
        for ray in first:
            self.assertAlmostEqual(math.hypot(*ray), 1, places=6)
            self.assertLessEqual(math.acos(ray[2]), math.radians(6))

    def test_other_heroes_cannot_enter_the_scoped_enforcer_adapter(self):
        from types import SimpleNamespace

        self.assertIs(enforcer.directions(SimpleNamespace(hero=0), None, 0, (1, 0), (0, 0, 1)),
                      NotImplemented)
