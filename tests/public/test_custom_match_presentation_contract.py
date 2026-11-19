import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.game import combat, heroselect, pve
from ow174.game.statescript import Asset, Int
from ow174.jam.groups import MATCH_LOBBY
from ow174.lobby.custom_games import CustomRoom
from ow174.lobby.handlers import leaving

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class CustomMatchPresentationTests(unittest.TestCase):
    def setUp(self):
        path = FIXTURES / "custom-match-presentation-v1.json"
        self.contract = json.loads(path.read_text(encoding="utf-8"))

    def test_mode_and_local_team_drive_the_objective(self):
        contract = self.contract
        expected = contract["expected"]
        for case, result in zip(contract["input"]["cases"], expected["cases"], strict=True):
            with self.subTest(case=case):
                mode = int(case["mode"], 16)
                variables = heroselect.objective_variables(mode, case["team"])
                with patch.object(heroselect, "owner_full_frame") as encode:
                    heroselect.controller_frame(heroselect.PVP, 1, True, objective=variables)
                screen = next(item for item in encode.call_args.args[1] if item.graph.index == 0x288B)
                self.assertEqual(screen.extra, {
                    expected["objective_variable"]: Asset(int(result["objective"], 16)),
                    expected["color_team_variable"]: Int(result["color_team"]),
                })

    def test_hybrid_mode_graph_gets_the_first_round_roles_in_both_pregame_phases(self):
        expected = self.contract["expected"]["pregame"]
        for phase in expected["phase_states"]:
            with (self.subTest(phase=phase), patch.object(combat.graph, "graph"),
                  patch.object(pve, "entrance", return_value=None),
                  patch.object(combat, "remote_full_frame") as encode):
                combat.mode_frame({}, int(expected["mode"], 16), int(expected["map"], 16), phase_state=phase)
                values = encode.call_args.args[1]
                self.assertEqual(values[expected["attacking_variable"]], (expected["attacking_team"], ()))
                self.assertEqual(values[expected["defending_variable"]], (expected["defending_team"], ()))

    def test_custom_title_path_stays_enabled_after_the_client_connects(self):
        expected = self.contract["expected"]
        room = CustomRoom.create("Room")
        room.match_id = (101, 102)
        session = SimpleNamespace(custom_room=room, send=Mock(), log=Mock())
        server = SimpleNamespace(session_of=Mock(return_value=session), match_lobbies={})
        self.assertEqual(room.lobby_state()["+0x78"]["+0x60"], expected["room_lobby_type"])
        for match_id, lobby_type in (
            (room.match_id, expected["custom_match_lobby_type"]),
            ((303, 404), expected["other_match_lobby_type"]),
        ):
            match = SimpleNamespace(id=match_id, label=lambda: "test match")
            player = SimpleNamespace(account_lo=42, match=match)
            leaving.game_joined(server, player)
            crc, message, payload = session.send.call_args.args
            self.assertEqual((crc, message), (MATCH_LOBBY, 53000))
            state = payload["+0x78"]
            self.assertEqual(state["+0x60"], lobby_type)
            self.assertEqual(state["+0x30"]["+0x0"], list(match_id))
            self.assertNotIn("+0x0", state)
            leaving.game_left(server, session, player)
            self.assertEqual(session.send.call_args.args[:2], (MATCH_LOBBY, 53003))
