"""A PvE map whose spawn rows are four team-0 start markers can be hosted, and four players start apart."""

import unittest
from unittest.mock import patch

from ow174.game import content, pve

MODE, MAP = 0x023000000000001A, 0x08000000000006FE


class UprisingStartTests(unittest.TestCase):
    def test_four_team_zero_rows_give_four_distinct_starts(self):
        rows = [{'team': 0, 'position': [-65.0 + i, -4.4, -93.0 + i], 'yaw': -75.0,
                 'tags': ['0x0D60000000000310']} for i in range(4)]
        entry = {'name': 'Uprising', 'modes': [f'{MODE:X}'], 'placeables': [1420, 1421, 1422, 1423],
                 'spawns': rows}
        with (patch.object(content, '_map_entries', return_value={MAP: entry}),
              patch.object(pve, 'entrance', return_value=True)):
            game_map = content.game_map(MAP, MODE, team_sizes=(4,))
        self.assertIsNotNone(game_map)
        starts = [content.spawn_point(game_map, 0, number) for number in range(4)]
        self.assertEqual(len({position for position, _ in starts}), 4)
        self.assertEqual(starts[0], (tuple(rows[0]['position']), -75.0))


if __name__ == "__main__":
    unittest.main()
