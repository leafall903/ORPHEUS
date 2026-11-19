import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.jam.values import id16
from ow174.lobby.custom_games import CONTINUATION, CustomRoom, begin_rules, receive_rules_part

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class CustomRoomRefreshContractTests(unittest.TestCase):
    def setUp(self):
        page = FIXTURES / "custom-room-preset-refresh-v1.json"
        self.contract = json.loads(page.read_text(encoding="utf-8"))
        self.room = CustomRoom.create("Test Room")
        card = {"+0x0": id16(42)}
        self.session = SimpleNamespace(
            custom_room=self.room,
            ident=SimpleNamespace(account=id16(42), party_id=(42, 0)),
            profile=object(),
            server=SimpleNamespace(content=SimpleNamespace(player=SimpleNamespace(record=Mock(return_value=card)))),
            send=Mock(),
            log=Mock(),
        )

    def test_changing_presets_publishes_the_room_and_capacity_before_start(self):
        request, expected = self.contract["input"], self.contract["expected"]
        room, session = self.room, self.session
        for index, row in enumerate(request["presets"]):
            with self.subTest(preset=row["preset"]):
                session.send.reset_mock()
                mode, preset = (int(row[key], 16) for key in ("mode", "preset"))
                rules = {
                    "+0xB0": mode, "+0xA8": preset, "+0x108": 1,
                    "+0xC0": [{"+0x60": key, "+0x61": size} for key, size in row["teams"]],
                }
                with patch("ow174.lobby.custom_games.has_collision", return_value=False):
                    begin_rules(session, {
                        "+0x78": {
                            **room.rules.options,
                            "+0x0": [{"+0x0": rules, "+0x138": int(request["map"], 16)}],
                            "+0x18": [rules],
                        },
                        "+0x3D0": False,
                    })
                self.assertEqual(
                    [call.args[1] for call in session.send.call_args_list], expected["message_order"]
                )
                state = {call.args[1]: call.args[2] for call in session.send.call_args_list}
                self.assertIsNone(room.match_id)
                self.assertEqual(state[53000]["+0x78"]["+0x30"]["+0x0"], list(room.id))
                self.assertEqual(state[23304]["+0x140"]["+0x18"][0]["+0xA8"], preset)
                lobby_settings = state[53001]["+0x78"]
                teams = lobby_settings["+0x0"]["+0x0"]["+0xC0"]
                self.assertEqual([[team["+0x60"], team["+0x61"]] for team in teams], row["teams"])
                caps = lobby_settings["+0x298"]
                self.assertEqual(
                    [caps[key] for key in ("+0x0", "+0x4", "+0x8")], expected["capacity_summary"][index]
                )
                self.assertEqual(state[53002]["+0x78"][0]["+0xA8"], expected["owner_team"][index])

    def test_total_mayhem_keeps_hero_overrides_in_their_map_and_mode_sections(self):
        case = self.contract["input"]["mayhem"]
        rules = [
            {
                "+0xA8": int(preset, 16), "+0xB0": int(mode, 16), "+0x108": 1,
                "+0xC0": [{"+0x60": 0, "+0x61": 6}, {"+0x60": 1, "+0x61": 6}],
            }
            for preset, mode in zip(case["presets"], case["modes"], strict=True)
        ]
        maps = [
            {"+0x78": {"+0x0": rule, "+0x138": int(map_guid, 16)}}
            for map_guid, rule in zip(case["maps"], rules, strict=True)
        ]
        overrides = [
            {"+0x78": {"+0x18": case["hero_override"]}, "+0x98": index, "+0x9C": 0}
            for index in range(2)
        ]
        stream = [
            (24009, maps[0]), (24011, overrides[0]), (24009, maps[1]), (24011, overrides[1]),
            (24010, {"+0x78": rules[0]}), (24010, {"+0x78": rules[1]}), (24011, overrides[1]),
        ]
        workshop = {"+0xF0": "Test description"}
        begin_rules(self.session, {"+0x78": self.room.rules.options, "+0x2B8": workshop, "+0x3D0": True})
        with patch("ow174.lobby.custom_games.has_collision", return_value=False):
            for index, (message, payload) in enumerate(stream, start=1):
                receive_rules_part(message, self.session, {
                    **payload, CONTINUATION[message]: index < len(stream),
                })

        replies = [call.args[1:] for call in self.session.send.call_args_list]
        self.assertEqual(
            [message for message, _ in replies], self.contract["expected"]["multipart_mayhem_order"]
        )
        joined = replies[1][1]
        self.assertEqual(joined["+0x380"], workshop)
        self.assertTrue(joined["+0x498"])
        for index, (incoming, outgoing) in enumerate(zip(stream, replies[2:-2], strict=True), start=1):
            message, payload = incoming
            self.assertEqual(outgoing[1], {**payload, CONTINUATION[message]: index < len(stream)})
        self.assertEqual(replies[-2][1]["+0x78"]["+0x0"]["+0x0"]["+0xA8"], rules[0]["+0xA8"])
