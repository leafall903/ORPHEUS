"""Which created map entities the map extractor lists as spawn points."""

import unittest

from tools import extract_game_maps as extractor

SPAWN, REQUIRED = extractor.SPAWN, extractor.REQUIRED


class SpawnRuleTests(unittest.TestCase):
    def test_spawn_component_is_a_spawn_whatever_its_tags(self):
        self.assertTrue(extractor.is_spawn({SPAWN, REQUIRED}, []))
        self.assertTrue(extractor.is_spawn({SPAWN}, [0x341, 0x42C]))

    def test_marker_with_only_the_pve_start_tag_is_a_spawn(self):
        self.assertTrue(extractor.is_spawn({REQUIRED}, [0x310]))
        self.assertTrue(extractor.is_spawn({REQUIRED}, [0x310, 0x42C]))  # non-team tags do not matter

    def test_other_team_tags_without_the_spawn_component_are_not(self):
        for tags in ([], [0x42C], [0x30D], [0x341], [0x310, 0x341], [0x310, 0x30D]):
            with self.subTest(tags=tags):
                self.assertFalse(extractor.is_spawn({REQUIRED}, tags))


if __name__ == "__main__":
    unittest.main()
