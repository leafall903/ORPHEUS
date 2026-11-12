"""Declared first-root spatial fixtures; no production actor inventory."""

import unittest

from ow174.game.script.target_spatial import (
    SpatialLeaf,
    SpatialNode,
    SpatialTree,
    leaf_accepts,
    overlaps,
)


class SpatialTests(unittest.TestCase):
    def test_mask_gates_and_strict_sphere_boundary(self):
        sphere = (0, 0, 0, 1)
        self.assertTrue(leaf_accepts(1, sphere, (1.999, 0, 0, 1), 1, 0))
        self.assertFalse(leaf_accepts(1, sphere, (2, 0, 0, 1), 1, 0))
        self.assertFalse(leaf_accepts(1, sphere, sphere, 2, 0))
        self.assertFalse(leaf_accepts(3, sphere, sphere, 1, 2))
        self.assertFalse(leaf_accepts(0, sphere, sphere, 0, 0))
        self.assertFalse(leaf_accepts(1, (0, 0, 0, 0), (0, 0, 0, 0), 1, 0))

    def test_aabb_contact_uses_native_sign_bits_including_negative_zero(self):
        self.assertTrue(overlaps((0, 0, 0), (1, 1, 1), (1, 0, 0), (2, 1, 1)))
        self.assertFalse(overlaps((0, 0, 0), (1, 1, 1), (1.001, 0, 0), (2, 1, 1)))
        self.assertFalse(overlaps((0.0, 0, 0), (1, 1, 1), (-1, 0, 0), (-0.0, 1, 1)))

    def test_tree_visits_right_before_left_and_keeps_duplicate_payloads(self):
        lo, hi = (-2, -2, -2), (2, 2, 2)
        def leaf(payload):
            return SpatialNode(lo, hi, leaf=SpatialLeaf(1, (0, 0, 0, 1), payload))

        tree = SpatialTree(0, (SpatialNode(lo, hi, (1, 2)), leaf("left"),
                               SpatialNode(lo, hi, (3, 4)), leaf("right"), leaf("right")))
        self.assertEqual(tree.query(lo, hi, (0, 0, 0, 1), 1, 0), ("right", "right", "left"))

    def test_unknown_data_does_not_become_an_empty_inventory(self):
        args = ((-1, -1, -1), (1, 1, 1), (0, 0, 0, 1), 1, 0)
        self.assertEqual(SpatialTree(-1).query(*args), ())
        for tree in (SpatialTree(), SpatialTree(0),
                     SpatialTree(0, (SpatialNode(args[0], args[1]),)),
                     SpatialTree(0, (SpatialNode(args[0], args[1], (0, 0)),))):
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                tree.query(*args)

    def test_unavailable_pruned_leaf_is_not_read(self):
        tree = SpatialTree(0, (SpatialNode((10, 10, 10), (11, 11, 11)),))
        self.assertEqual(tree.query((-1, -1, -1), (1, 1, 1), (0, 0, 0, 1), 1, 0), ())

    def test_sphere_wrapper_uses_bounds_and_overflowed_squared_distance(self):
        tree = SpatialTree(0, (SpatialNode((-1, -1, -1), (1, 1, 1),
                                           leaf=SpatialLeaf(1, (0, 0, 0, 1), "body")),))
        self.assertEqual(tree.query_sphere((0, 0, 0, 1), 1, 0), ("body",))
        self.assertEqual(tree.query_sphere((10, 0, 0, 1), 1, 0), ())
        # Both squared distance and squared radius overflow float32: inf < inf is false.
        self.assertFalse(leaf_accepts(1, (1e30, 0, 0, 1e30), (0, 0, 0, 1), 1, 0))


if __name__ == "__main__":
    unittest.main()
