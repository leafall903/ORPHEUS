import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ow174.game.match import Match
from ow174.lobby.custom_games import START_GAME, CustomRoom, start_room

FIXTURES = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES") or Path(__file__).resolve().parents[2]
                / "fixtures/behavior")


class CustomModeStartContractTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads((FIXTURES / "custom-mode-start-v1.json").read_text(encoding="utf-8"))
        self.input, self.expected = fixture["input"], fixture["expected"]
        self.room = CustomRoom.create("Demo")

    def test_mode_start_uses_map_then_mode_then_defaults(self):
        rules = self.room.rules
        map_rules = rules.maps[0]["+0x0"]
        mode_rules = rules.modes[0]
        choices = self.input["choices"]

        def override(value):
            return {"+0x0": value, "+0x4": self.input["setting_id"], "+0x6": self.input["setting_value_type"]}

        self.assertEqual(rules.start_mode(map_rules), self.expected["default"])
        for choice in choices.values():
            with self.subTest(choice=choice):
                mode_rules["+0xD8"] = [override(choice)]
                self.assertEqual(rules.start_mode(map_rules), choice)
        map_rules["+0xD8"] = [override(100), override(choices["immediate"])]
        self.assertEqual(rules.start_mode(map_rules), choices["immediate"])

    def test_live_start_mode_request_keeps_the_current_match(self):
        room = self.room
        room.match_id = (101, 102)
        game = SimpleNamespace(start_custom_mode=Mock(), take_out=Mock())
        session = SimpleNamespace(custom_room=room, server=SimpleNamespace(game=game),
                                  account=SimpleNamespace(account_lo=42))
        self.assertEqual(START_GAME, self.input["room_start_action"])
        start_room(session, {"+0x78": self.input["room_start_action"]})
        game.start_custom_mode.assert_not_called()
        start_room(session, {"+0x78": self.input["admin_start_mode_action"]})
        game.start_custom_mode.assert_called_once_with(42, (101, 102))
        game.take_out.assert_not_called()
        self.assertEqual(room.match_id, (101, 102))
        self.assertIsNone(room.leaving_action)

    def test_room_owner_can_override_either_waiting_policy_once(self):
        for choice in (None, *self.input["choices"].values()):
            with self.subTest(choice=choice):
                match = Match.__new__(Match)
                match.game_map = SimpleNamespace(custom_start=choice)
                match.assemble_ends = None

                def begin(now, target=match):
                    target.assemble_ends = now + self.expected["assembly_seconds"]

                match._begin_assembly = Mock(side_effect=begin)
                allowed = choice in self.expected["manual_override_while_waiting"]
                self.assertEqual(match.start_custom_mode(1), allowed)
                self.assertFalse(match.start_custom_mode(2))
                self.assertEqual(match._begin_assembly.call_count, int(allowed))
