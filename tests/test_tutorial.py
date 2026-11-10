"""The native Tutorial request uses its own team, fixed hero and controller graph."""

import dataclasses
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.game import heroselect, statescript
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER, TUTORIAL_MAP, TUTORIAL_MODE
from ow174.game.match import SEND_CONTROLLER, Match
from ow174.game.script import decode, expr, graph
from ow174.lobby.handlers.matchmaking import create_game
from ow174.lobby.matchmaker import Matchmaker

CONTROLLER = 0x13A4
SCENE = dataclasses.replace(PRACTICE_RANGE, map_guid=TUTORIAL_MAP, mode_guid=TUTORIAL_MODE,
                            mode_name="Tutorial", team_sizes=(0, 1))


def source_graph():
    record = {
        "index": CONTROLLER, "header": {"m_statesBitCount": 2}, "fields": {},
        "items": 2, "containers": {}, "unknown_nodes": [], "states": [0, 1],
        "entries": [], "remote": [], "sync_vars": [], "nodes": [],
    }
    for i in range(2):
        record["nodes"].append({
            "pos": i, "rid": i, "uid": i, "remote": -1, "state": i,
            "client_only": i == 0, "server_only": False, "fields": {},
            "class": "STUStatescriptStateHero",
        })
    return graph.Graph(record)


class TutorialTests(unittest.TestCase):
    def test_native_request_routes_only_the_observed_kind_and_flags(self):
        maker = Mock()
        session = SimpleNamespace(server=SimpleNamespace(matchmaker=maker), log=Mock())
        create_game(session, {"+0x78": 3, "+0xA8": 4})
        maker.tutorial.assert_called_once_with(session)
        maker.practice.assert_not_called()
        create_game(session, {"+0x78": 3, "+0xA8": 0})
        self.assertEqual(maker.tutorial.call_count, 1)
        session.log.assert_called_once()

    def test_fixed_controller_frame_has_no_shared_picker_and_keeps_possessed_body(self):
        source = source_graph()
        definition = {"pve": True, "teams": {"1": {"controller": "0x05800000000013A4"}}}
        with (patch.object(graph, "mode_scripts", return_value=definition),
              patch.object(graph, "graph", return_value=source),
              patch.object(decode, "graph_of", return_value=source)):
            controller = heroselect.controller_of(TUTORIAL_MODE)
            self.assertIsInstance(controller, heroselect.FixedController)
            self.assertEqual(controller.hero_bit, 0)  # client-only state has no wire bit
            payload = heroselect.controller_frame(controller, 12, False, hero=SOLDIER, body=0xA0000101)
            wire = statescript.chunk(payload, 0, 1)
            frame = decode.read_chunk(wire.getvalue(), wire.count)
        self.assertEqual({i: v.graph for i, v in frame.instances.items()}, {1: CONTROLLER})
        self.assertFalse(frame.instances[1].states[1][0])
        self.assertEqual(frame.entity_vars[940], (expr.Asset(SOLDIER), ()))
        self.assertEqual(frame.instances[1].vars[8370], (expr.Entity(0xA0000101), ()))

    def test_tutorial_rejects_missing_or_substituted_controller_inputs(self):
        for definition in (None, {"pve": False},
                           {"pve": True, "teams": {"1": {"controller": "0x0580000000000C90"}}},
                           {"pve": True, "teams": {"1": {"controller": "0x05800000000013C1"}}}):
            with (self.subTest(definition=definition),
                  patch.object(graph, "mode_scripts", return_value=definition),
                  self.assertRaises(ValueError)):
                heroselect.controller_of(TUTORIAL_MODE)

    def test_request_is_solo_on_the_authored_red_team(self):
        account = SimpleNamespace(account_lo=1)
        party = SimpleNamespace(members=[account, SimpleNamespace(account_lo=2)])
        session = SimpleNamespace(account=account)
        maker = Matchmaker(SimpleNamespace(social=SimpleNamespace(party_of=lambda _: party)))
        maker._start = Mock(return_value=True)
        with patch("ow174.lobby.matchmaker.game_map", return_value=SCENE) as load:
            self.assertTrue(maker._tutorial(session))
        load.assert_called_once_with(TUTORIAL_MAP, TUTORIAL_MODE, "Tutorial", team_sizes=(0, 1))
        scene, roster = maker._start.call_args.args
        self.assertIs(scene, SCENE)
        self.assertEqual(roster.teams[0], [])
        self.assertEqual([member for _, member, _ in roster.teams[1]], [account])

    def test_fixed_hero_waits_for_controller_ack_and_spawns_once_without_a_pick(self):
        controller = heroselect.FixedController(statescript.Graph(CONTROLLER, 1, 0), SOLDIER, 0)
        with patch.object(heroselect, "controller_of", return_value=controller):
            match = Match(SCENE, collision_provider=StaticCollisionProvider(flat_world(0)))
            player = match.add_player(1, "Tutorial", 0x02E0000000000003, 1, False)
        self.assertEqual(player.hero.guid, SOLDIER)
        player.loaded_at, player.steps_done, player.spawned = 0, 3, True
        player.client = SimpleNamespace(controller_ready=False)
        match.send_controller, match.combat.spawned = Mock(), Mock()

        def possess(*_):
            player.has_body = True
            return True

        match.switch_hero = Mock(side_effect=possess)
        match._spawn_steps(player, player.client, SEND_CONTROLLER)
        self.assertEqual(player.steps_done, 3)
        match.switch_hero.assert_not_called()
        player.client.controller_ready = True
        match._spawn_steps(player, player.client, SEND_CONTROLLER + 1)
        self.assertTrue(player.has_body)
        self.assertFalse(player.select_open)
        self.assertFalse(match.assembling())
        match.switch_hero.assert_called_once_with(player, player.hero)
        self.assertEqual(player.steps_done, 4)
        player.script.data_last = 1
        match._spawn_steps(player, player.client, SEND_CONTROLLER + 2)
        self.assertEqual(match.switch_hero.call_count, 1)


if __name__ == "__main__":
    unittest.main()
