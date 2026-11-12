"""Base tag initialization does not guess complete runtime tag state."""

import unittest

from ow174.game.script.tag_initialization import base_tags


class TagInitializationTests(unittest.TestCase):
    def test_owner_order_precedes_descriptor_and_duplicates_do_not_move_keys(self):
        self.assertEqual(base_tags((9, 7, 9), (7, 0, 9, 0xFFFFFFFF)),
                         (9, 7, 0, 0xFFFFFFFF))

    def test_known_empty_and_absent_descriptor_are_supported(self):
        self.assertEqual(base_tags((), None), ())
        self.assertEqual(base_tags((7, 7), None), (7,))
        self.assertEqual(base_tags((), (8, 8)), (8,))

    def test_unknown_sources_and_non_native_keys_are_rejected(self):
        for owner, descriptor in ((None, ()), (NotImplemented, None), ([], None),
                                  ((), []), ((True,), None), ((-1,), ()),
                                  ((), (1 << 32,)), ((), (7.0,))):
            with self.subTest(owner=owner, descriptor=descriptor), self.assertRaises(ValueError):
                base_tags(owner, descriptor)


if __name__ == '__main__':
    unittest.main()
