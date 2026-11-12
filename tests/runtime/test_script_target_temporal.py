"""Second-root candidate control flow with explicit relationship/history services."""

import unittest

from ow174.game.script.target_spatial import (
    SpatialNode,
    SpatialTree,
    TemporalLeaf,
    TemporalQuery,
    temporal_tick,
)


class TemporalSpatialTests(unittest.TestCase):
    def test_seconds_to_microseconds_preserves_native_unsigned_conversion(self):
        self.assertEqual(temporal_tick(0.016), 16000)
        self.assertEqual(temporal_tick(-0.000001), (1 << 64) - 1)
        self.assertEqual(temporal_tick(0.0000009), 0)
        self.assertEqual(temporal_tick(float("nan")), 1 << 63)
        self.assertEqual(temporal_tick(float("inf")), 1 << 63)
        with self.assertRaises(ValueError):
            temporal_tick(1)

    def fixture(self, *, mask=1, lo=(-2, -2, -2), hi=(2, 2, 2), history_flag=0):
        tree = SpatialTree(0, (SpatialNode((-10, -10, -10), (10, 10, 10),
                                          leaf=TemporalLeaf(mask, lo, hi, "candidate")),))
        return tree, TemporalQuery((0, 0, 0, 1), 1, 2, history_flag, 123, 456, "source")

    def test_relationship_selects_time_and_mask_selects_history_flag_flag(self):
        for relationship in (False, True):
            for history_flag in (0, 1):
                with self.subTest(relationship=relationship, history_flag=history_flag):
                    tree, query = self.fixture(history_flag=history_flag)
                    calls = []

                    def related(payload, source, calls=calls, relationship=relationship):
                        calls.append(("related", payload, source))
                        return relationship

                    def history(payload, tick, use_floor, calls=calls):
                        calls.append(("history", payload, tick, use_floor))
                        return (0, 0, 0, 1)

                    self.assertEqual(query.scan(tree, related=related, history=history), ("candidate",))
                    self.assertEqual(calls, [("related", "candidate", "source"),
                                             ("history", "candidate", 456 if relationship else 123,
                                              not bool(history_flag))])

    def test_mask_and_payload_bounds_prune_before_services(self):
        for settings in ({"mask": 0}, {"mask": 3}, {"lo": (5, 5, 5), "hi": (6, 6, 6)}):
            with self.subTest(settings=settings):
                tree, query = self.fixture(**settings)

                def unexpected(*args):
                    self.fail("pruned candidate reached a relationship/history service")

                self.assertEqual(query.scan(tree, related=unexpected, history=unexpected), ())

    def test_known_history_failure_forwards_but_success_obeys_strict_sphere(self):
        for sphere, expected in ((None, ("candidate",)), ((2, 0, 0, 1), ()),
                                 ((1.999, 0, 0, 1), ("candidate",))):
            with self.subTest(sphere=sphere):
                tree, query = self.fixture()
                self.assertEqual(query.scan(tree, related=lambda *_: False,
                                            history=lambda *_, sphere=sphere: sphere), expected)

    def test_unknown_services_do_not_masquerade_as_known_history_failure(self):
        tree, query = self.fixture()
        with self.assertRaises(ValueError):
            query.scan(tree, related=lambda *_: NotImplemented, history=lambda *_: None)
        with self.assertRaises(ValueError):
            query.scan(tree, related=lambda *_: False, history=lambda *_: NotImplemented)


if __name__ == "__main__":
    unittest.main()
