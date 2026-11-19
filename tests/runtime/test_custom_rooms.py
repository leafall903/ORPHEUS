"""Custom room creation through the lobby router and prepared JAM schemas."""

import json
import os
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.game import heroselect, world
from ow174.game.bits import BitReader
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER, SPECTATOR_TEAM, game_map
from ow174.game.match import SPECTATOR_BIT, Match
from ow174.game.script import expr
from ow174.game.script.decode import ClientBody, read_chunk, w_u32, w_var
from ow174.game.script.expr import Asset
from ow174.game.script.graph import graph
from ow174.game.world import OP_DESTROY
from ow174.jam.codec import Schemas
from ow174.jam.groups import MATCH_LOBBY, MATCH_LOBBY_OUT
from ow174.jam.values import id16
from ow174.lobby.custom_games import HYBRID, match_left
from ow174.lobby.handlers import build_router, leaving
from ow174.lobby.matchmaker import Matchmaker
from ow174.lobby.session import Session
from tests.runtime.test_game_notices import FakeClient
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "behavior"


class CustomRoomTests(unittest.TestCase):
    def setUp(self):
        self.fixture_dir = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES", FIXTURES))
        self.case = self.fixture("custom-room-v1.json")
        self.schemas = Schemas()
        self.request = int(self.case["input"]["crc"], 16)
        self.reply = int(self.case["expected"]["crc"], 16)
        self.card = {"+0x0": id16(42), "+0x10": id16(42), "+0x40": "TestPlayer"}
        self.server = SimpleNamespace(
            schemas=self.schemas,
            router=build_router(),
            recorder=Mock(),
            state_lock=threading.RLock(),
            content=SimpleNamespace(player=SimpleNamespace(record=Mock(return_value=self.card))),
            match_lobbies={},
        )

    def fixture(self, name):
        return json.loads((self.fixture_dir / name).read_text(encoding="utf-8"))

    def connect(self, announce_reply=True):
        session = Session(self.server, Mock(), None, 1)
        session.account = SimpleNamespace(account_lo=42, name="TestPlayer", profile=object())
        session.ident = SimpleNamespace(account=id16(42), party_id=(10, 12))
        session.logged_in = True
        protocols = (
            [self.request, self.reply, MATCH_LOBBY, MATCH_LOBBY_OUT] if announce_reply else [self.request]
        )
        session.announce(protocols)
        session.send_raw = Mock()
        return session

    def create(self, session, flags=None):
        request = self.case["input"]
        self.dispatch(session, self.request, request["message"], {
            "+0x78": request["kind"], "+0x80": request["name"],
            "+0xA8": request["flags"] if flags is None else flags,
        })

    def dispatch(self, session, crc, message, value):
        payload = self.schemas.encode(crc, message, value)
        session.dispatch(session.wire_of[crc], message - self.schemas.base(crc), payload)

    def test_create_and_retry_send_the_same_room_to_its_owner(self):
        session = self.connect()
        self.create(session)
        room = session.custom_room
        expected = self.case["expected"]
        frames = [call.args[0] for call in session.send_raw.call_args_list]
        protocols = {2: self.reply, 3: MATCH_LOBBY}
        decoded = [self.schemas.split(protocols[frame[0]], frame) for frame in frames]
        self.assertEqual([message for message, _ in decoded], expected["message_order"])
        lobby, value, map_update, settings, roster = [
            self.schemas.decode(protocols[frame[0]], message, body)
            for frame, (message, body) in zip(frames, decoded, strict=True)
        ]
        self.assertEqual(decoded[1][0], expected["message"])
        self.assertEqual(value["+0x78"]["+0x88"], expected["name"])
        self.assertEqual(value["+0x78"]["+0x0"]["+0x0"], self.card["+0x0"])
        self.assertEqual(value["+0x78"]["+0x68"], self.card["+0x0"])
        self.assertEqual(value["+0x78"]["+0x78"], room.id[0])
        self.assertNotEqual(room.id[0], 0)
        self.assertIs(value["+0x498"], expected["waiting_for_map"])
        self.assertEqual(value["+0x140"]["+0x0"], [])
        self.assertEqual(map_update["+0x78"]["+0x138"], int(expected["default_map"], 16))
        self.assertIs(map_update["+0x1B8"], False)
        self.assertEqual(lobby["+0x78"]["+0x60"], expected["lobby_type"])
        self.assertEqual(lobby["+0x78"]["+0x30"]["+0x0"], list(room.id))
        self.assertEqual(settings["+0x78"]["+0x278"], id16(*room.id))
        self.assertEqual(settings["+0x78"]["+0x288"], id16(int(expected["default_map_header"], 16), 0))
        teams = settings["+0x78"]["+0x0"]["+0x0"]["+0xC0"]
        self.assertEqual([t["+0x60"] for t in teams], [0, 1])
        self.assertEqual([t["+0x61"] for t in teams], expected["team_slots"])
        self.assertEqual(roster["+0x78"][0]["+0x0"]["+0x0"], self.card["+0x0"])
        self.create(session)
        self.assertEqual(session.custom_room is room, expected["retry_reuses_room"])
        self.assertEqual([call.args[0] for call in session.send_raw.call_args_list], frames * 2)

        other = self.connect()
        self.create(other)
        self.assertNotEqual(other.custom_room.id, room.id)

    def test_bad_flags_or_unannounced_reply_leave_no_room(self):
        session = self.connect()
        self.create(session, flags=0)
        self.assertIsNone(session.custom_room)
        session.send_raw.assert_not_called()

        session = self.connect(announce_reply=False)
        self.create(session)
        self.assertIsNone(session.custom_room)
        session.send_raw.assert_not_called()

    def test_leave_only_closes_the_matching_room_and_allows_a_new_one(self):
        session = self.connect()
        self.server.game = None
        self.create(session)
        first = session.custom_room
        message = self.case["input"]["leave_message"]
        self.dispatch(session, MATCH_LOBBY_OUT, message, {"+0x78": {"+0x0": [1, 2]}})
        self.assertIs(session.custom_room, first)
        self.dispatch(session, MATCH_LOBBY_OUT, message, {"+0x78": {"+0x0": list(first.id)}})
        self.assertIsNone(session.custom_room)
        left, closed = [call.args[0] for call in session.send_raw.call_args_list[-2:]]
        actual = [self.schemas.split(MATCH_LOBBY, left)[0], self.schemas.split(self.reply, closed)[0]]
        self.assertEqual(actual, self.case["expected"]["leave_message_order"])
        self.create(session)
        self.assertNotEqual(session.custom_room.id, first.id)

    def connect_with_matchmaker(self):
        session = self.connect()
        self.server.session_of = {42: session}.get
        self.server.social = SimpleNamespace(party_of=Mock(return_value=object()))
        self.server.content.menu_hero = SimpleNamespace(picked=Mock(return_value=0))
        handoff = SimpleNamespace(match_id=(101, 102))
        self.server.game = SimpleNamespace(
            in_match=Mock(return_value=False), create_match=Mock(return_value=[handoff]),
        )
        self.server.matchmaker = Matchmaker(self.server)
        self.server.matchmaker._send_to_game = Mock()
        return session, handoff

    def test_manual_mode_starts_in_the_same_match_after_the_lobby_request(self):
        self.check_waiting_mode_starts_in_place("manual", 1, team=1)

    def test_owner_can_start_an_unfilled_room(self):
        self.check_waiting_mode_starts_in_place("full", 2, team=0)

    def check_waiting_mode_starts_in_place(self, choice, slots, *, team):
        contract = self.fixture("custom-mode-start-v1.json")
        inputs, expected = contract["input"], contract["expected"]
        run = ProtocolHarness()
        self.addCleanup(run.close)
        session, _ = self.connect_with_matchmaker()
        self.server.game = run.server
        self.create(session)
        room = session.custom_room
        mode = room.rules.modes[0]
        start = {"+0x0": inputs["choices"][choice], "+0x4": inputs["setting_id"],
                 "+0x6": inputs["setting_value_type"]}
        selected = room.rules.maps[0]
        map_rules = {**selected["+0x0"], "+0xC0": [{"+0x60": team, "+0x61": slots}]}
        self.dispatch(session, self.request, 24008, {
            "+0x78": {
                **room.rules.options,
                "+0x0": [{**selected, "+0x0": map_rules}],
                "+0x18": [{**mode, "+0xD8": [*mode["+0xD8"], start]}],
            },
            "+0x3D0": False,
        })
        self.dispatch(session, self.request, 24018, {"+0x78": inputs["room_start_action"]})
        handoff = self.server.matchmaker._send_to_game.call_args.args[1][0]
        link, player = run.connect(handoff)
        match = player.match
        self.assertEqual(room.match_id, match.id)
        self.assertIsNone(match.assemble_ends)
        self.assertTrue(player.select_open)
        self.assertEqual(player.team, team)
        self.assertEqual(match.free_places(loaded_only=True)[team], slots - 1)

        run.message(link, 21616, pick(player.entity, SOLDIER))
        run.until(lambda: player.has_body and not player.select_open and player.mode_script.delivered)
        self.assertFalse(player.assembly_pick_confirmed)
        controller, mode_ui = ClientBody(), ClientBody()

        def receive_script(entity, model):
            for _, record in link.records:
                if record.entity != entity or record.chunk is None:
                    continue
                reader = BitReader(record.chunk.getvalue())
                first = w_u32(reader)
                last = first + w_var(reader)
                if first <= model.applied + 1 and last > model.applied:
                    model.apply(record)

        def hero_select_waiting():
            receive_script(player.entity, controller)
            return controller.vars.get((3, heroselect.HIDE_ASSEMBLE))

        run.until(lambda: hero_select_waiting() is True)
        receive_script(world.GAME_MODE_ENTITY, mode_ui)
        waiting, assembling = expected["wait_sender"], expected["assembly_sender"]
        roles = self.fixture("custom-match-presentation-v1.json")["expected"]["pregame"]
        phase_graph = int(waiting["graph"], 16)
        objective = graph(phase_graph).state(roles["objective_sender_state"])
        self.assertEqual(objective.field("m_gameMessage")["m_gameMessage"], roles["objective_message"])
        text, color, _ = objective.field("m_params")

        def check_objective():
            self.assertEqual(mode_ui.vars[0, roles["attacking_variable"]], roles["attacking_team"])
            self.assertEqual(mode_ui.vars[0, roles["defending_variable"]], roles["defending_team"])
            context = Mock(spec=expr.Context)
            context.var.side_effect = lambda scope, var: mode_ui.vars[0, var]
            # The real client resolves 65ABFCCC against the local controller's team filter.
            context.native.side_effect = lambda config: team if config["$"] == "STU_65ABFCCC" else None
            self.assertEqual(
                expr.evaluate(text["m_value"], context), controller.vars[3, heroselect.OBJECTIVE_TEXT],
            )
            self.assertEqual(
                expr.evaluate(color["m_value"], context), controller.vars[3, heroselect.OBJECTIVE_COLOR_TEAM],
            )

        for sender in (waiting, assembling):
            node = graph(int(sender["graph"], 16)).state(sender["state"])
            self.assertEqual(node.field("m_gameMessage")["m_gameMessage"], sender["message"])
        self.assertEqual(mode_ui.graphs[2], phase_graph)
        self.assertIn((2, waiting["state"]), mode_ui.on())
        check_objective()
        self.assertNotIn((0, 1303), mode_ui.vars)
        self.assertIs(mode_ui.vars[0, expected["wait_enabled_variable"]], True)
        presenter_graph = int(expected["wait_graph"], 16)
        presenter_state = expected["wait_presenter_state"]
        presenter = graph(presenter_graph).state(presenter_state)
        self.assertIn(expected["wait_heading"], json.dumps(presenter.field("m_EC32138E")))
        self.assertEqual(mode_ui.graphs[4], presenter_graph)
        self.assertEqual(mode_ui.vars[4, 1923], Asset(int(expected["wait_heading_fallback"], 16)))
        self.assertIs(mode_ui.vars[4, expected["wait_enabled_variable"]], True)
        self.assertIn((4, presenter_state), mode_ui.on())

        body = player.body
        action = {"+0x78": inputs["admin_start_mode_action"]}
        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            self.dispatch(session, self.request, 24018, action)
        deadline = match.assemble_ends
        self.assertAlmostEqual(deadline - run.now, expected["assembly_seconds"])
        self.dispatch(session, self.request, 24018, action)
        self.assertEqual(match.assemble_ends, deadline)
        self.assertIsNone(room.leaving_action)
        self.assertEqual(room.match_id, match.id)
        self.assertEqual(player.body, body)
        self.assertTrue(player.select_open)
        run.until(lambda: hero_select_waiting() is False)

        def countdown_received():
            receive_script(world.GAME_MODE_ENTITY, mode_ui)
            return 4 not in mode_ui.graphs and mode_ui.vars.get((0, 1303), 0) > 0

        run.until(countdown_received)
        self.assertIn((2, assembling["state"]), mode_ui.on())
        self.assertNotIn((2, waiting["state"]), mode_ui.on())
        check_objective()
        self.assertIs(mode_ui.vars[0, expected["wait_enabled_variable"]], False)
        self.server.matchmaker._send_to_game.assert_called_once()

    def test_full_slots_wait_for_playing_clients_to_load(self):
        contract = self.fixture("custom-mode-start-v1.json")
        for free_for_all, map_id, mode, sizes in (
            (False, 0x08000000000000D4, HYBRID, (1, 1)),
            (True, 0x08000000000007A4, 0x023000000000001E, (2,)),
        ):
            with self.subTest(free_for_all=free_for_all):
                run = ProtocolHarness()
                self.addCleanup(run.close)
                scene = game_map(map_id, mode, free_for_all=free_for_all, team_sizes=sizes)
                self.assertIsNotNone(scene)
                scene = replace(scene, custom_start=contract["expected"]["default"])
                host, spectator, final_player = run.server.create_match(scene, [
                    (1, "Host", SOLDIER, 0, False),
                    (2, "Spectator", SOLDIER, SPECTATOR_TEAM, False),
                    (3, "Final Player", SOLDIER, int(not free_for_all), False),
                ])
                link, owner = run.connect(host)
                _, observer = run.connect(spectator)
                self.assertEqual(not any(owner.match.free_places(loaded_only=True)),
                                 contract["expected"]["spectators_fill_playing_slots"])
                run.message(link, 21616, pick(owner.entity, SOLDIER))
                run.until(lambda owner=owner: owner.has_body)
                self.assertIsNone(owner.match.assemble_ends)
                self.assertFalse(owner.select_open)

                run.connect(final_player)
                self.assertTrue(owner.match.assembling())
                self.assertTrue(owner.select_open)
                self.assertFalse(observer.select_open)

    def test_immediate_mode_and_practice_range_enter_on_first_player(self):
        contract = self.fixture("custom-mode-start-v1.json")
        practice = PRACTICE_RANGE
        hybrid = game_map(0x08000000000000D4, HYBRID)
        self.assertIsNotNone(hybrid)
        for scene in (replace(hybrid, custom_start=contract["input"]["choices"]["immediate"]), practice):
            with self.subTest(scene=scene.mode_name):
                run = ProtocolHarness()
                self.addCleanup(run.close)
                (handoff,) = run.server.create_match(scene, [(1, "Host", SOLDIER, 0, False)])
                link, player = run.connect(handoff)
                self.assertFalse(player.match.in_custom_warmup)
                self.assertEqual(player.match.assembling(), scene is not practice)
                run.message(link, 21616, pick(player.entity, SOLDIER))
                run.until(lambda player=player: player.has_body)
                if scene is practice:
                    self.assertEqual(
                        not player.select_open, contract["expected"]["practice_start_automatically"],
                    )
                else:
                    self.assertTrue(player.assembly_pick_confirmed)

    def test_selected_map_starts_once_and_owner_returns_to_the_room(self):
        session, handoff = self.connect_with_matchmaker()
        self.create(session)
        room = session.custom_room
        self.dispatch(session, self.request, 24017, {
            "+0x78": id16(91), "+0x88": 0, "+0x89": 1, "+0x8A": 255,
        })
        self.assertEqual(room.team, 0)
        self.dispatch(session, self.request, 24017, {
            "+0x78": session.ident.account, "+0x88": 0, "+0x89": 1, "+0x8A": 255,
        })
        self.assertEqual(room.team, 1)
        frame, = session.send_raw.call_args.args
        message, body = self.schemas.split(MATCH_LOBBY, frame)
        self.assertEqual(self.schemas.decode(MATCH_LOBBY, message, body)["+0x78"][0]["+0xA8"], 1)
        self.dispatch(session, self.request, 24008, {"+0x78": room.rules.options, "+0x3D0": True})
        request = self.case["input"]
        selected_map = int(request["changed_map"], 16)
        mode = {
            "+0xA8": room.rules.modes[0]["+0xA8"], "+0xB0": HYBRID, "+0x108": 1,
            "+0xC0": [{"+0x60": 0, "+0x61": 4}, {"+0x60": 1, "+0x61": 5}],
        }
        self.dispatch(session, self.request, 24009, {
            "+0x78": {"+0x138": selected_map, "+0x0": mode}, "+0x1B8": True,
        })
        self.assertEqual(room.revision, 0)
        self.dispatch(session, self.request, 24010, {"+0x78": mode})
        self.assertEqual(room.revision, 1)
        frames = [call.args[0] for call in session.send_raw.call_args_list[-5:]]
        mids = [self.schemas.split({2: self.reply, 3: MATCH_LOBBY}[f[0]], f)[0] for f in frames]
        self.assertEqual(mids, [23312, 23313, 23314, 53001, 53002])
        message, body = self.schemas.split(MATCH_LOBBY, frames[-2])
        settings = self.schemas.decode(MATCH_LOBBY, message, body)["+0x78"]
        self.assertEqual(settings["+0x288"], id16(int(self.case["expected"]["changed_map_header"], 16), 0))
        start = {"+0x78": request["start_action"]}
        scene = replace(PRACTICE_RANGE, map_guid=selected_map, team_sizes=(4, 5))
        with (patch("ow174.lobby.custom_games.has_collision", return_value=True),
              patch("ow174.lobby.custom_games.game_map", return_value=scene) as load):
            self.dispatch(session, self.request, request["start_message"], start)
            self.dispatch(session, self.request, request["start_message"], start)
        load.assert_called_once_with(selected_map, HYBRID, "Hybrid", free_for_all=False, team_sizes=(4, 5))
        self.server.game.create_match.assert_called_once()
        self.assertEqual(self.server.game.create_match.call_args.args[1][0][3], 1)
        self.assertEqual(
            self.server.matchmaker._send_to_game.call_count, self.case["expected"]["duplicate_start_handoffs"]
        )
        self.assertEqual(room.match_id, handoff.match_id)
        match_left(session, (1, 2))
        self.assertEqual(room.match_id, handoff.match_id)
        match_left(session, handoff.match_id)
        self.assertIs(session.custom_room, room)
        self.assertIsNone(room.match_id)
        self.assertEqual(room.team, 1)

    def test_custom_match_title_payloads_survive_encoding(self):
        contract = self.fixture("custom-match-presentation-v1.json")
        expected = contract["expected"]
        for case, result in zip(contract["input"]["cases"], expected["cases"], strict=True):
            with self.subTest(case=case):
                mode = int(case["mode"], 16)
                free_for_all = mode in (0x023000000000001E, 0x0230000000000070)
                scene = game_map(
                    int(case["map"], 16), mode, free_for_all=free_for_all,
                    team_sizes=(8,) if free_for_all else (6, 6),
                )
                match = Match(scene, collision_provider=StaticCollisionProvider(flat_world(0.0)))
                player = match.add_player(42, "Player", SOLDIER, case["team"], False)
                player.client = FakeClient(player)
                match.tick = 123
                for select_open in (True, False, True):
                    player.select_open = select_open
                    match.send_controller(player)
                    bits = player.client.entities[-1].chunk
                    frame = read_chunk(bits.getvalue(), bits.count)
                    screen = next(item for item in frame.instances.values() if item.graph == 0x288B)
                    self.assertEqual(
                        screen.vars[expected["objective_variable"]][0], Asset(int(result["objective"], 16)),
                    )
                    self.assertEqual(screen.vars[expected["color_team_variable"]][0], result["color_team"])

        session = self.connect()
        self.create(session)
        room = session.custom_room
        room.match_id = (101, 102)
        self.server.session_of = {42: session}.get
        match = SimpleNamespace(id=room.match_id, label=lambda: "test match")
        leaving.game_joined(self.server, SimpleNamespace(account_lo=42, match=match))
        frame, = session.send_raw.call_args.args
        message, payload = self.schemas.split(MATCH_LOBBY, frame)
        self.assertEqual(message, 53000)
        state = self.schemas.decode(MATCH_LOBBY, message, payload)["+0x78"]
        self.assertEqual(state["+0x60"], expected["custom_match_lobby_type"])
        self.assertEqual(state["+0x30"]["+0x0"], list(room.match_id))
        self.assertEqual(state["+0x0"]["+0x0"]["+0x0"], expected["queue_card"])

    def test_custom_mode_handoff_and_roster(self):
        contract = self.fixture("custom-room-modes-v1.json")
        expected = contract["expected"]
        for case in contract["input"]["cases"]:
            with self.subTest(preset=case["preset"]):
                session, handoff = self.connect_with_matchmaker()
                self.create(session)
                room = session.custom_room
                free_for_all = len(case["slots"]) == 1
                roster_key = "free_for_all_roster_id" if free_for_all else "team_roster_id"
                roster_team = expected[roster_key]
                team_ids = (expected["free_for_all_rules_id"],) if free_for_all else (0, 1)
                preset, mode, map_guid = (int(case[key], 16) for key in ("preset", "mode", "map"))
                rules = {
                    "+0xA8": preset, "+0xB0": mode, "+0x108": 1,
                    "+0xC0": [
                        {"+0x60": team, "+0x61": size}
                        for team, size in zip(team_ids, case["slots"], strict=True)
                    ],
                }
                self.dispatch(session, self.request, 24008, {
                    "+0x78": {**room.rules.options, "+0x0": [{"+0x138": map_guid, "+0x0": rules}],
                              "+0x18": [rules]},
                })
                self.assertEqual(room.team, team_ids[0])
                if free_for_all:
                    self.dispatch(session, self.request, expected["move_message"], {
                        "+0x78": session.ident.account, "+0x89": roster_team,
                        "+0x8A": expected["free_for_all_move_slot"],
                    })
                    self.assertEqual(room.slot, expected["free_for_all_move_slot"])
                self.assert_roster(session, room, roster_team)
                self.dispatch(session, self.request, 24018, {"+0x78": 1})
                self.dispatch(session, self.request, 24018, {"+0x78": 1})
                self.assertEqual(self.server.game.create_match.call_count, expected["handoff_count"])
                scene, players, _ = self.server.game.create_match.call_args.args
                self.assertEqual(scene.map_guid, map_guid)
                self.assertEqual((scene.mode_guid, scene.preset_guid), (mode, preset))
                self.assertEqual(scene.team_sizes, tuple(case["slots"]))
                self.assertEqual(scene.free_for_all, free_for_all)
                self.assertEqual(players[0][3], expected["game_team"])
                match = Match(scene)
                player = match.add_player(*players[0][:5])
                mode_state = match.game_struct(player)["+0x0"]["+0x0"]
                self.assertEqual((mode_state["+0xB0"], mode_state["+0xA8"]), (mode, preset))
                match._game_mode_create(match.tick)
                match_left(session, handoff.match_id)
                self.assert_roster(session, room, roster_team)

    def assert_roster(self, session, room, team):
        frame, = session.send_raw.call_args.args
        message, payload = self.schemas.split(MATCH_LOBBY, frame)
        self.assertEqual(message, 53002)
        player, = self.schemas.decode(MATCH_LOBBY, message, payload)["+0x78"]
        self.assertEqual((player["+0xA8"], player["+0xAF"]), (team, room.slot))

    def test_spectating_retires_the_pawn_and_allows_hero_selection_after_rejoining(self):
        scene = replace(PRACTICE_RANGE, mode_guid=HYBRID, team_sizes=(6, 6))
        match = Match(scene, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        player = match.add_player(42, "Test", SOLDIER, 0, False)
        player.client = FakeClient(player)
        player.spawned = True
        self.assertTrue(match.switch_hero(player, player.hero))
        old_body = player.body
        self.assertTrue(match.move_custom_player(player, SPECTATOR_TEAM))
        self.assertEqual(player.team_bit, SPECTATOR_BIT)
        self.assertIsNone(player.body_script)
        self.assertFalse(player.has_body)
        self.assertFalse(player.select_open)
        self.assertFalse(match.switch_hero(player, player.hero))
        self.assertIn((old_body, OP_DESTROY), [(r.entity, r.op) for r in player.client.entities])
        teams = player.client.reliable[-1][1]["+0x78"]["+0x0"]["+0x0"]["+0xC0"]
        self.assertTrue(all(not team["+0x18"] for team in teams))

        self.assertTrue(match.move_custom_player(player, 1))
        self.assertTrue(player.select_open)
        self.assertTrue(match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old_body)

    def test_live_team_move_resets_the_picker_and_accepts_a_new_pick_without_reopening(self):
        contract = self.fixture("custom-room-live-controls-v1.json")
        hero = int(contract["input"]["hero_before_team_move"], 16)
        expected = {
            int(key): (value if isinstance(value, bool) else Asset(value), ())
            for key, value in contract["expected"]["team_move_controller_variables"].items()
        }
        scene = replace(PRACTICE_RANGE, mode_guid=HYBRID, team_sizes=(6, 6))
        match = Match(scene, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        player = match.add_player(42, "Test", hero, 0, False)
        client = player.client = FakeClient(player)
        player.spawned = True
        pick = {"+0x80": bytes(13) + hero.to_bytes(8, "little") + bytes(8), "+0xA8": heroselect.PICK_HERO}

        self.assertTrue(match.move_custom_player(player, 1))  # switch before the first pick
        self.assertFalse(player.has_body)
        update = client.entities[-1]
        frame = read_chunk(update.chunk.getvalue(), update.chunk.count)
        self.assertEqual(frame.entity_vars[heroselect.OPEN_SELECT], (True, ()))
        self.assertEqual(frame.entity_vars[heroselect.SELECT_SHOWN], (True, ()))

        match.game_message(player, pick, 1.0)
        self.assertTrue(player.has_body)
        self.assertFalse(player.select_open)
        old_body = player.body
        client.entities.clear()

        self.assertTrue(match.move_custom_player(player, 0))
        self.assertFalse(player.has_body)
        self.assertTrue(player.select_open)
        self.assertIn((old_body, OP_DESTROY), [(item.entity, item.op) for item in client.entities])
        update = client.entities[-1]
        self.assertEqual(update.entity, player.entity)
        self.assertEqual(read_chunk(update.chunk.getvalue(), update.chunk.count).entity_vars, expected)

        match.game_message(player, pick, 2.0)
        self.assertTrue(player.has_body)
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(player.select_open)

    def test_rename_updates_current_room_and_survives_match_return(self):
        contract = self.fixture("custom-room-names-v1.json")
        request = contract["input"]
        expected = contract["expected"]
        session = self.connect()
        self.create(session)
        room = session.custom_room
        original_id = room.id
        session.send_raw.reset_mock()
        for kind, name in request["edits"]:
            self.dispatch(session, self.request, request["message"], {"+0x78": kind, "+0x80": name})

        frames = [call.args[0] for call in session.send_raw.call_args_list]
        updates = [self.schemas.split(self.reply, frame) for frame in frames]
        self.assertEqual([message for message, _ in updates], [expected["message"]] * len(request["edits"]))
        message, payload = updates[-1]
        update = self.schemas.decode(self.reply, message, payload)
        self.assertEqual(update["+0x88"]["+0x88"], expected["room_name"])
        self.assertEqual(update["+0x88"]["+0x78"], original_id[0])
        self.assertEqual(
            [[entry["+0x18"], entry["+0x20"]] for entry in update["+0x138"]], expected["team_names"]
        )

        room.match_id = (91, 92)
        session.send_raw.reset_mock()
        self.dispatch(session, self.request, request["message"], {
            "+0x78": 1, "+0x80": request["in_game_name"],
        })
        message, payload = self.schemas.split(self.reply, session.send_raw.call_args.args[0])
        self.assertEqual(message, expected["message"])
        update = self.schemas.decode(self.reply, message, payload)
        self.assertEqual(update["+0x88"]["+0x88"], request["in_game_name"])
        session.send_raw.reset_mock()
        match_left(session, (91, 92))
        message, payload = self.schemas.split(self.reply, session.send_raw.call_args_list[1].args[0])
        self.assertEqual(message, 23304)
        joined = self.schemas.decode(self.reply, message, payload)
        self.assertEqual(joined["+0x78"], update["+0x88"])
        self.assertEqual(joined["+0x128"], update["+0x138"])

        kind, name = request["clear_team"]
        self.dispatch(session, self.request, request["message"], {"+0x78": kind, "+0x80": name})
        message, payload = self.schemas.split(self.reply, session.send_raw.call_args.args[0])
        self.assertEqual(message, expected["message"])
        rows = self.schemas.decode(self.reply, message, payload)["+0x138"]
        self.assertEqual([[row["+0x18"], row["+0x20"]] for row in rows], [[0, ""], expected["team_names"][1]])
