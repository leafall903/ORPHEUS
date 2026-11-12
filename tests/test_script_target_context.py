"""Temporal context requires actual component input, not inferred controller/body aliases."""

import unittest

from ow174.game.script.target_aggregate import RootQuery
from ow174.game.script.target_context import temporal_context


class TargetContextTests(unittest.TestCase):
    def query(self):
        return RootQuery((1, 2, 3, 99), 7.5, 0, 1, 4, (0, 0), (), (), ())

    def test_explicit_owner_status_and_seconds_become_temporal_query(self):
        owner = object()
        context = temporal_context(self.query(), first_seconds=0.016, second_seconds=0.032,
                                   component24_owner=owner, status25_flags=0xFFFFFFFF)
        self.assertIs(context.subject, owner)
        self.assertEqual(context.sphere, (1, 2, 3, 7.5))
        self.assertEqual((context.first_tick, context.second_tick), (16000, 32000))
        self.assertEqual((context.any_bits, context.excluded_bits, context.history_flag_bits),
                         (1, 4, 0x07800000))

    def test_known_absent_component24_is_distinct_from_unknown(self):
        context = temporal_context(self.query(), first_seconds=0., second_seconds=0.,
                                   component24_owner=None, status25_flags=0)
        self.assertIsNone(context.subject)
        for owner, status in ((NotImplemented, 0), (None, NotImplemented), (None, None)):
            with self.subTest(owner=owner, status=status), self.assertRaises(ValueError):
                temporal_context(self.query(), first_seconds=0., second_seconds=0.,
                                 component24_owner=owner, status25_flags=status)


if __name__ == "__main__":
    unittest.main()
