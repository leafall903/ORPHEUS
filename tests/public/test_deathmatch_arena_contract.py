import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import content

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class DeathmatchArenaContractTests(unittest.TestCase):
    def test_team_deathmatch_on_an_arena_with_shared_spawns(self):
        contract = json.loads((FIXTURES / "deathmatch-arena-spawns-v1.json").read_text(encoding="utf-8"))
        request, expected = contract["input"], contract["expected"]
        map_guid = int(request["map"], 16)
        team_mode, solo_mode = (int(request[key], 16) for key in ("team_mode", "solo_mode"))
        arena = {
            "name": "Test Arena",
            "modes": [request["team_mode"], request["solo_mode"]],
            "spawns": request["spawns"],
        }
        with patch.object(content, "_map_entries", return_value={map_guid: arena}):
            team_map = content.game_map(map_guid, team_mode, team_sizes=tuple(expected["team_sizes"]))
            solo_map = content.game_map(map_guid, solo_mode, free_for_all=True, team_sizes=(8,))

        self.assertEqual(team_map.team_sizes, tuple(expected["team_sizes"]))
        self.assertFalse(team_map.free_for_all)
        self.assertEqual(len(solo_map.spawns), expected["solo_spawn_count"])
        self.assertTrue(solo_map.free_for_all)
        for index, key in enumerate(("team_first_spawns", "team_second_spawns")):
            self.assertEqual(
                [list(content.spawn_point(team_map, team, index)[0]) for team in (0, 1)],
                expected[key],
            )
