"""Native-backed geometry boundaries; candidate/filter/LOS hosting is separate."""

import unittest
from dataclasses import replace

from ow174.game.script.target_volume import Volume


class TargetVolumeTests(unittest.TestCase):
    def stock(self):
        return Volume(
            (0, -5, 0), (-7.64274186065882e-15, 1.0, -4.371138828673793e-8),
            (0.8944272, 0.8944272), (7.500000953674316, 7.500000953674316),
            (0, 0), (1, 1), 15, parallel=True,
        )

    def test_flux_transverse_boundary_includes_capsule_radius(self):
        for x, accepted in ((7.5, True), (7.89, True), (7.91, False), (8, False), (10, False)):
            with self.subTest(x=x):
                actual, metric = self.stock().capsule((x, 0, 0), 0.4, 1.8)
                self.assertEqual(actual, accepted)
                self.assertGreater(metric, 0)

    def test_spherical_depth_limit_is_separate_from_transverse_extent(self):
        self.assertTrue(self.stock().capsule((0, 10, 0), 0.4, 1.8)[0])
        self.assertEqual(self.stock().capsule((0, 15, 0), 0.4, 1.8), (False, -1))

    def test_capsule_below_the_forward_half_volume_is_excluded(self):
        self.assertFalse(self.stock().capsule((0.2, -6, 0), 0.4, 1.8)[0])
        self.assertTrue(self.stock().capsule((0.2, -5, 0), 0.4, 1.8)[0])

    def test_short_capsule_uses_half_height_instead_of_inverted_segment(self):
        self.assertTrue(self.stock().capsule((7.9, 0, 0), 0.7, 0.1)[0])
        self.assertFalse(self.stock().capsule((8.3, 0, 0), 0.7, 0.1)[0])

    def test_margin_expands_the_capsule_surface_before_transverse_test(self):
        self.assertFalse(self.stock().capsule((8.5, 0, 0), 0.4, 1.8)[0])
        self.assertTrue(replace(self.stock(), margin=1).capsule((8.5, 0, 0), 0.4, 1.8)[0])

    def test_full_sphere_has_no_forward_or_aperture_exclusion(self):
        volume = replace(self.stock(), outer_cos=(-1, -1))
        self.assertTrue(volume.capsule((0.2, -6, 0), 0.4, 1.8)[0])
        self.assertTrue(volume.capsule((10, 0, 0), 0.4, 1.8)[0])

    def test_inner_radial_limit_rejects_before_writing_a_distance(self):
        volume = replace(self.stock(), outer_cos=(-1, -1), near=10)
        self.assertEqual(volume.capsule((0, 0, 0), 0.4, 1.8), (False, -1))

    def test_inner_aperture_removes_the_axis_and_keeps_the_annulus(self):
        volume = replace(self.stock(), inner_cos=(0.9, 0.9), inner_extent=(2, 2))
        self.assertFalse(volume.capsule((0, 0, 0), 0.4, 1.8)[0])
        self.assertFalse(volume.capsule((2, 0, 0), 0.4, 1.8)[0])
        self.assertTrue(volume.capsule((3, 0, 0), 0.4, 1.8)[0])

    def test_conical_volume_uses_cosine_and_not_parallel_extent(self):
        volume = Volume((0, 0, 0), (0, 0, 1), (0.5, 0.5), (0, 0), (0, 0), (1, 1), 10)
        self.assertTrue(volume.capsule((0, 0, 8), 0.4, 1.8)[0])
        self.assertFalse(volume.capsule((0, 0, -8), 0.4, 1.8)[0])
        self.assertFalse(volume.capsule((8, 0, 0), 0.4, 1.8)[0])

    def test_native_nonvertical_parallel_interpolation_uses_both_surface_points(self):
        # Original C8E660 sample515. The vertical surface gap exceeds1, so
        # interpolation selects the second extent even though feet were flattened.
        volume = Volume(
            (-3.6542672693821165, -0.24365078864833123, -1.3227972007900157),
            (-0.36610445440288963, -0.6048868750198305, 0.7071629210409058),
            (-1, -0.6), (7.814986379493194, 5.442127253045837),
            (0.4648207437913152, 1.0044590739044146), (1, 0.3), 14.202008494473061,
            parallel=True, ignore_height=True,
        )
        accepted, metric = volume.capsule(
            (-8.495295140989729, 0.7698906947249036, 7.724847288396374),
            0.1775880502884929, 3.6164328771091925,
        )
        self.assertFalse(accepted)
        self.assertAlmostEqual(metric, 10.08376693725586, delta=0.0001)

    def test_native_conical_margin_sample(self):
        volume = Volume(
            (-0.5874104891913712, -1.7910045712662392, -3.3145274293196882),
            (0.1053013009656887, -0.7166083798467082, 0.6894810120289095),
            (-0.6, 1), (3.8515716620057274, 7.204211157001378),
            (0.6298441765754637, 1.6575924445589616), (0.3, 0.3), 17.84234583015239,
            near=2, margin=1, ignore_height=True,
        )
        accepted, metric = volume.capsule(
            (8.187183127893135, 9.851584810892557, -17.38665315144696),
            0.8225292560160731, 2.5246866276271445,
        )
        self.assertFalse(accepted)
        self.assertAlmostEqual(metric, 15.761144638061523, delta=0.0001)


if __name__ == '__main__':
    unittest.main()
