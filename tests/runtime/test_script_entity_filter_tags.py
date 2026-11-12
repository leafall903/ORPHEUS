"""Native tag-list stage; no implicit resource GUID or gameplay-label mapping."""

import unittest

from ow174.game.script.entity_filter import TagSet, matches_tags


class EntityFilterTagTests(unittest.TestCase):
    def test_static_and_added_tags_obey_native_removal_precedence(self):
        tags = TagSet(static={1, 2}, added={2, 3}, removed={2, 3})
        self.assertTrue(tags.contains(1))
        self.assertFalse(tags.contains(2))  # static removal wins over addition
        self.assertTrue(tags.contains(3))  # removals only consult static membership
        self.assertFalse(tags.contains(4))

    def test_any_all_and_excluded_lists_are_independent(self):
        tags = TagSet(static={1, 2}, added={3})
        self.assertTrue(matches_tags(tags, any_tags=(4, 3), all_tags=(1, 2, 3)))
        self.assertFalse(matches_tags(tags, any_tags=(4, 5)))
        self.assertFalse(matches_tags(tags, all_tags=(1, 4)))
        self.assertFalse(matches_tags(tags, any_tags=(1,), excluded_tags=(2,)))
        self.assertTrue(matches_tags(tags, excluded_tags=(4, 5)))

    def test_absent_component_and_null_or_empty_lists(self):
        for tags in (None, TagSet()):
            with self.subTest(tags=tags):
                self.assertTrue(matches_tags(tags))
                self.assertTrue(matches_tags(tags, None, None, (1,)))
                self.assertFalse(matches_tags(tags, any_tags=(1,)))
                self.assertFalse(matches_tags(tags, all_tags=(1,)))

    def test_query_tokens_compare_low32_bits_and_duplicate_requirements_do_not_consume(self):
        tags = TagSet(static={7})
        self.assertTrue(matches_tags(tags, all_tags=(7, (0xFFFFFFFF << 32) | 7, 7)))
        self.assertFalse(matches_tags(tags, excluded_tags=((123 << 32) | 7,)))

    def test_unavailable_or_non_native_tag_data_is_rejected(self):
        for value in (True, -1, 1 << 32, 1.0):
            with self.subTest(static=value), self.assertRaises(ValueError):
                TagSet(static=(value,))
        for value in (True, -1, 1 << 64, 1.0):
            with self.subTest(query=value), self.assertRaises(ValueError):
                matches_tags(TagSet(), all_tags=(value,))
        with self.assertRaises(ValueError):
            matches_tags(NotImplemented)
        with self.assertRaises(ValueError):
            TagSet(static=(1, True))
