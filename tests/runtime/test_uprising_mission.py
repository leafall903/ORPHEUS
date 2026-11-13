"""Uprising shows its defend and escort phases: a director graph on the mode entity and the clock."""

import dataclasses
import math
import unittest
from unittest.mock import patch

from ow174.game import combat, pve, statescript
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE
from ow174.game.match import COUNTDOWN_EVERY, Match
from ow174.game.script import decode, graph
from tests.runtime.test_game_notices import into_world

MODE, MAP = 0x023000000000000F, 0x08000000000006FE
JUNKENSTEIN, JUNKENSTEIN_MAP = 0x0230000000000003, 0x08000000000006C9
UPRISING = dataclasses.replace(PRACTICE_RANGE, mode_guid=MODE, map_guid=MAP, mode_name="Uprising")


def read(frame):
    wire = statescript.chunk(frame, 0, 1)
    return decode.read_chunk(wire.getvalue(), wire.count)


def graphs_of(decoded) -> dict:
    return {number: item.graph for number, item in decoded.instances.items()}


class DirectorFrameTests(unittest.TestCase):
    def test_no_mission_keeps_the_entrance_frame(self):
        self.assertEqual(combat.mode_frame({}, MODE, MAP, mission=None).getvalue(),
                         combat.mode_frame({}, MODE, MAP).getvalue())
        self.assertEqual(graphs_of(read(combat.mode_frame({}, MODE, MAP))), {1: pve.MODE_UI, 2: pve.UPRISING})

    def test_defend_lists_its_director_and_the_clock(self):
        decoded = read(combat.mode_frame({}, MODE, MAP, mission=200.0))
        self.assertEqual(graphs_of(decoded), {1: pve.MODE_UI, 2: pve.UPRISING, 3: pve.DEFEND})
        self.assertEqual(decoded.entity_vars[pve.CLOCK][0], 200.0)
        ready = pve.entrance(MODE, MAP).ready_state
        self.assertEqual(decoded.instances[2].states[ready], (True, {}))  # the loading gate stays open
        self.assertFalse(any(on for on, _ in decoded.instances[3].states.values()))

    def test_escort_takes_a_new_index_so_the_client_drops_the_defend_director(self):
        decoded = read(combat.mode_frame({}, MODE, MAP, mission=0.0))
        self.assertEqual(graphs_of(decoded), {1: pve.MODE_UI, 2: pve.UPRISING, 4: pve.ESCORT})
        self.assertEqual(decoded.entity_vars[pve.CLOCK][0], 0.0)

    def test_other_modes_get_no_director(self):
        self.assertIsNone(pve.defend_seconds(JUNKENSTEIN, JUNKENSTEIN_MAP))
        self.assertIsNone(pve.defend_seconds(PRACTICE_RANGE.mode_guid, PRACTICE_RANGE.map_guid))
        self.assertEqual(combat.mode_frame({}, JUNKENSTEIN, JUNKENSTEIN_MAP, mission=200.0).getvalue(),
                         combat.mode_frame({}, JUNKENSTEIN, JUNKENSTEIN_MAP).getvalue())

    def test_a_pack_without_the_directors_shows_no_phase(self):
        original = graph.graph
        with patch.object(
            graph, "graph", side_effect=lambda index: None if index == pve.ESCORT else original(index),
        ):
            self.assertIsNone(pve.defend_seconds(MODE, MAP))
        self.assertEqual(pve.defend_seconds(MODE, MAP), pve.DEFEND_SECONDS)


class MissionClockTests(unittest.TestCase):
    def setUp(self):
        self.match = Match(UPRISING, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        self.player = self.match.add_player(1, "Alpha", 0, 0, False)
        into_world(self.match, self.player, 100)
        self.player.client.entities.clear()

    def frames(self) -> list:
        return [read_update(update) for update in self.player.client.entities if update.chunk is not None]

    def test_no_clock_before_the_mission_is_shown(self):
        self.assertIsNone(self.match.mission_left(50.0))

    def test_the_clock_waits_for_the_heroes_to_assemble(self):
        self.match.assemble_ends = 130.0
        self.match._start_mission(100.0)
        self.assertIsNone(self.match.mission_ends)  # the hero select is counting down on the same variable
        self.match._end_assemble(130.0)
        self.assertEqual(self.match.mission_ends, 130.0 + pve.DEFEND_SECONDS)
        self.match._start_mission(200.0)
        self.assertEqual(self.match.mission_ends, 130.0 + pve.DEFEND_SECONDS)  # started once

    def test_a_mode_without_the_directors_starts_no_clock(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        match._start_mission(100.0)
        self.assertIsNone(match.mission_ends)

    def test_the_defend_clock_is_sent_as_it_runs(self):
        self.match.mission_ends = 300.0
        self.match._send_mission(self.player, 100.0)
        self.assertEqual(self.player.next_countdown, 100.0 + COUNTDOWN_EVERY)
        (decoded,) = self.frames()
        self.assertEqual(graphs_of(decoded)[3], pve.DEFEND)
        self.assertEqual(decoded.entity_vars[pve.CLOCK][0], 200.0)

    def test_the_escort_frame_goes_once(self):
        self.match.mission_ends = 300.0
        self.match._send_mission(self.player, 300.5)
        self.assertEqual(self.match.mission_left(300.5), 0.0)
        self.assertEqual(self.player.next_countdown, math.inf)
        (decoded,) = self.frames()
        self.assertEqual(graphs_of(decoded)[4], pve.ESCORT)
        self.assertNotIn(3, decoded.instances)


def read_update(update):
    return decode.read_chunk(update.chunk.getvalue(), update.chunk.count)


if __name__ == "__main__":
    unittest.main()
