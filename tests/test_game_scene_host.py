"""The real Match owner starts, advances, rejects and retires shared scene scripts."""

import dataclasses
import unittest
from types import SimpleNamespace
from unittest import mock

from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.match import Match
from ow174.game.script import scene_host
from ow174.game.server import GameServer
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state
from tests.test_script_runtime_budget import graph, node, plug
from tests.test_script_scene_host import A, ValueWorld
from tests.test_script_startup import definition, spec

MAP = dataclasses.replace(PRACTICE_RANGE, name='shared script fixture',
                          map_guid=0x0800000000FFCC01, placeables=())


def host_for(match, model=None):
    world = ValueWorld(mode=match.game_map.mode_guid)
    world.map_guid = match.game_map.map_guid
    world.bind(A)
    host = scene_host.SceneHost(world, graph_loader={1: graph() if model is None else model}.get)
    host.add(A, definition(spec(1)))
    return host


class HomeClient:
    def __init__(self):
        self.home = []

    def go_home(self, now):
        self.home.append(now)


class MatchSceneHostTests(unittest.TestCase):
    def match(self, factory):
        return Match(MAP, collision_provider=StaticCollisionProvider(flat_world(0)),
                     scene_host_factory=factory)

    def test_match_starts_factory_once_and_advances_on_the_simulation_ticks(self):
        factory = mock.Mock(side_effect=host_for)
        match = self.match(factory)
        match.update(1.0, 100)
        match.update(1.016, 101)
        factory.assert_called_once_with(match)
        self.assertEqual(match.scene_host.frame, 101)
        self.assertFalse(match.ended)
        self.assertEqual(match.snapshot()['scene_script']['host']['frame'], 101)

    def test_script_failure_returns_players_home_before_spawn_or_combat_runs(self):
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'UnknownAction')], entries=[0])
        match = self.match(lambda match: host_for(match, model))
        player = match.add_player(1, 'Player', SOLDIER, 0, False)
        client = HomeClient()
        player.client = client
        with (mock.patch.object(match.combat, 'update', side_effect=AssertionError('must not run')),
              self.assertLogs('ow174.game', level='ERROR')):
            match.update(1.0, 100)
        self.assertTrue(match.ended and player.gone and match.scene_host.closed)
        self.assertEqual(client.home, [1.0])
        self.assertIn('UnknownAction', match.scene_host_error)
        self.assertFalse(match.scene_host.world.entities)

    def test_last_player_leave_closes_scene_and_cannot_send_from_old_components(self):
        match = self.match(host_for)
        player = match.add_player(1, 'Player', SOLDIER, 0, False)
        match.update(1.0, 100)
        match.leave(player)
        self.assertTrue(match.scene_host.closed and match.ended)
        self.assertFalse(match.scene_host.world.script_components)

    def test_wrong_map_or_missing_placeable_creates_are_rejected(self):
        for mismatch in ('map', 'creates'):
            def factory(match, mismatch=mismatch):
                host = host_for(match)
                if mismatch == 'map':
                    host.world.map_guid += 1
                return host

            match = self.match(factory)
            if mismatch == 'creates':
                match.game_map = dataclasses.replace(MAP, placeables=(123,))
            with self.assertLogs('ow174.game', level='ERROR'):
                match.update(1.0, 100)
            self.assertTrue(match.ended and match.scene_host.closed)

    def test_factory_error_is_terminal_and_is_not_retried(self):
        factory = mock.Mock(side_effect=ValueError('invalid explicit world'))
        match = self.match(factory)
        with self.assertLogs('ow174.game', level='ERROR'):
            match.update(1.0, 100)
        match.update(1.016, 101)
        self.assertEqual(factory.call_count, 1)
        self.assertTrue(match.ended)

    def test_game_server_passes_the_factory_to_each_real_match(self):
        server = GameServer(scene_host_factory=host_for,
            collision_provider_factory=lambda: StaticCollisionProvider(flat_world(0)))
        server.create_match(MAP, [])
        server.create_match(MAP, [])
        for match in server.matches:
            match.update(1.0, 100)
        self.assertIsNot(server.matches[0].scene_host, server.matches[1].scene_host)
        self.assertIsNot(server.matches[0].scene_host.world, server.matches[1].scene_host.world)

    def test_stored_server_export_refuses_unimplemented_shared_runtime_storage(self):
        server = GameServer(scene_host_factory=host_for)
        with self.assertRaisesRegex(ServerSnapshotError, 'shared scene script runtime storage'):
            export_server_state(server)

    def test_server_stop_retires_owned_scene_runtime(self):
        server = GameServer(scene_host_factory=host_for,
            collision_provider_factory=lambda: StaticCollisionProvider(flat_world(0)))
        server.create_match(MAP, [])
        match = server.matches[0]
        match.update(1.0, 100)
        server.stop()
        self.assertTrue(match.ended and match.scene_host.closed)

    def test_successful_vm_pass_does_not_send_the_prepared_entrance_readiness(self):
        match = self.match(host_for)
        player = match.add_player(1, 'Player', SOLDIER, 0, False)
        match.update(1.0, 100)
        player.loaded_at, player.steps_done = 0.0, 2
        client = SimpleNamespace(ownership_delivered=lambda: True,
            queue_entities=mock.Mock(side_effect=AssertionError('scene replication is not ready')))
        match._spawn_steps(player, client, 2.0)
        self.assertEqual(player.steps_done, 2)
        self.assertFalse(match.scene_host.client_ready)
        client.queue_entities.assert_not_called()


if __name__ == '__main__':
    unittest.main()
