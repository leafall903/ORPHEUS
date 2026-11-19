import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.game import heroselect
from ow174.game.match import SPECTATOR_BIT
from ow174.game.statescript import Asset, Bool
from ow174.jam.values import id16
from ow174.lobby.custom_games import (
    CustomRoom,
    begin_rules,
    match_left,
    move_player,
    receive_rules_part,
    start_room,
)

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class CustomRoomLiveControlsTests(unittest.TestCase):
    def setUp(self):
        contract = json.loads((FIXTURES / "custom-room-live-controls-v1.json").read_text(encoding="utf-8"))
        self.input, self.expected = contract["input"], contract["expected"]
        match_id = tuple(self.input["match_id"])
        owner = self.input["owner_id"]
        team = self.input["spectator_team"]
        self.room = CustomRoom.create("Test")
        self.room.match_id = match_id
        self.room.active_slots = dict(zip((0, 1, team), self.input["current_capacity"], strict=True))
        self.player = SimpleNamespace(match=SimpleNamespace(id=match_id))
        self.game = SimpleNamespace(
            player_of=Mock(return_value=self.player), take_out=Mock(),
            move_custom_player=Mock(return_value=True),
        )
        self.matchmaker = SimpleNamespace(custom=Mock())
        card = {"+0x0": id16(owner)}
        self.session = SimpleNamespace(
            custom_room=self.room,
            account=SimpleNamespace(account_lo=owner),
            ident=SimpleNamespace(account=id16(owner), party_id=(owner, 0)),
            profile=object(),
            server=SimpleNamespace(
                game=self.game, matchmaker=self.matchmaker,
                content=SimpleNamespace(player=SimpleNamespace(record=Mock(return_value=card))),
            ),
            send=Mock(),
            log=Mock(),
        )
        first_map = self.room.rules.maps[0]
        self.scene = SimpleNamespace(map_guid=first_map["+0x138"])
        self.enterContext(patch.object(
            CustomRoom, "prepared_scene", return_value=(self.scene, first_map["+0x0"]),
        ))

    def messages(self):
        return [call.args[1] for call in self.session.send.call_args_list]

    def test_live_settings_wait_for_last_part_and_keep_active_slots(self):
        room = self.room
        team = self.input["spectator_team"]
        options = {**room.rules.options, "+0x18": room.rules.modes}
        options["+0x1D0"] = {**options["+0x1D0"], "+0x8": self.input["next_spectator_capacity"]}
        begin_rules(self.session, {"+0x78": options, "+0x3D0": True})
        self.session.send.assert_not_called()
        receive_rules_part(24009, self.session, {"+0x78": room.rules.maps[0], "+0x1B8": False})
        self.assertEqual(self.messages(), self.expected["settings_messages"])
        notice = self.session.send.call_args.args[2]["+0x78"]
        self.assertEqual(notice, int(self.expected["settings_notice"], 16))
        self.assertEqual(room.active_slots[team], self.input["current_capacity"][-1])
        self.assertEqual(room.slots_for(room.preview()[1])[team], self.input["next_spectator_capacity"])

    def test_live_move_to_spectators_and_back_updates_game_and_roster(self):
        team = self.input["spectator_team"]
        payload = {"+0x78": self.session.ident.account, "+0x89": team, "+0x8A": self.input["auto_slot"]}
        move_player(self.session, payload)
        self.game.move_custom_player.assert_called_once_with(self.input["owner_id"], self.room.match_id, team)
        self.assertEqual(self.messages(), [self.expected["roster_message"]])
        self.assertEqual(self.session.send.call_args.args[2]["+0x78"][0]["+0xA8"], team)
        self.assertEqual(self.room.slot, 0)
        self.assertEqual(SPECTATOR_BIT, int(self.expected["spectator_filter"], 16))

        move_player(self.session, {**payload, "+0x89": 1, "+0x8A": 2})
        self.assertEqual((self.room.team, self.room.slot), (1, 2))
        self.game.move_custom_player.assert_called_with(self.input["owner_id"], self.room.match_id, 1)

    def test_team_move_opens_hero_selection_and_clears_the_confirmed_pick(self):
        hero = int(self.input["hero_before_team_move"], 16)
        with patch.object(heroselect, "owner_full_frame") as encode:
            heroselect.controller_frame(heroselect.PVP, 1, False, hero=hero)
            self.assertEqual(encode.call_args.args[2][heroselect.CURRENT_HERO], Asset(hero))
            heroselect.controller_frame(heroselect.PVP, 2, True)
        expected = self.expected["team_move_controller_variables"]
        self.assertEqual(encode.call_args.args[2], {
            int(key): Bool(value) if isinstance(value, bool) else Asset(value)
            for key, value in expected.items()
        })

    def test_return_preserves_room_and_waits_for_departure(self):
        self.check_departure(self.input["lobby_action"], self.expected["lobby_notice"])
        self.assertIsNone(self.room.match_id)
        self.matchmaker.custom.assert_not_called()

    def test_restart_reserves_one_new_match_after_departure(self):
        next_match_id = tuple(self.input["next_match_id"])

        def reserve(_session, room, _scene):
            room.match_id = next_match_id

        self.matchmaker.custom.side_effect = reserve
        self.check_departure(self.input["restart_action"], self.expected["restart_notice"])
        self.matchmaker.custom.assert_called_once_with(self.session, self.room, self.scene)
        self.assertEqual(self.room.match_id, next_match_id)
        team = self.input["spectator_team"]
        self.assertEqual(self.room.active_slots[team], self.input["current_capacity"][-1])

    def check_departure(self, action, notice):
        old_id, room_id = self.room.match_id, self.room.id
        start_room(self.session, {"+0x78": action})
        start_room(self.session, {"+0x78": action})
        self.game.take_out.assert_called_once()
        self.assertEqual(self.messages(), [self.expected["notice_message"]])
        self.assertEqual(self.session.send.call_args.args[2]["+0x78"], int(notice, 16))
        self.matchmaker.custom.assert_not_called()
        match_left(self.session, (96, 97))
        self.assertEqual(self.room.match_id, old_id)
        self.session.send.reset_mock()
        match_left(self.session, old_id)
        match_left(self.session, old_id)
        self.assertEqual(self.messages(), self.expected["resumed_room_messages"])
        self.assertEqual(self.room.id, room_id)
