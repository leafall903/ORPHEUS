"""The career-stats contract: what played matches add to a profile and what the career profile then lists."""

import json
import os
import unittest
from pathlib import Path

from ow174.accounts.profile import Profile
from ow174.content.career import count_match, played_stats

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


def _numbers(table: dict) -> dict[int, dict[int, float]]:
    return {
        int(hero, 16): {int(stat, 16): value for stat, value in stats.items()}
        for hero, stats in table.items()
    }


class CareerStatsContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((FIXTURES / "career-stats-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["case_id"], "career-stats.played-matches.v1")
        self.assertEqual(self.contract["target_build"], 104319)
        self.assertEqual(self.contract["evidence"], "synthetic-reference")
        self.profile = Profile()
        for match in self.contract["input"]["matches"]:
            count_match(self.profile, _numbers(match))

    def test_matches_add_up_to_the_stored_career(self):
        self.assertEqual(_numbers(self.profile.career), _numbers(self.contract["expected"]["career"]))

    def test_the_career_survives_a_save(self):
        saved = Profile.from_dict(json.loads(json.dumps(self.profile.to_dict())))
        self.assertEqual(saved.career, self.profile.career)

    def test_the_profile_lists_each_hero_and_all_heroes(self):
        listed = {
            hero: {stat["+0x0"]: stat["+0x8"] for stat in stats}
            for hero, stats in played_stats(self.profile).items()
        }
        self.assertEqual(listed, _numbers(self.contract["expected"]["all_modes"]))

    def test_a_profile_without_a_match_lists_nothing(self):
        self.assertEqual(played_stats(Profile()), {})


if __name__ == "__main__":
    unittest.main()
