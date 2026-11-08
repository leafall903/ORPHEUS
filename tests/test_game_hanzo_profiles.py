"""Explicit CLI -> lobby -> GameServer -> real match Hanzo host configuration."""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_cli as cli_tests
import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_sonic as sonic_tests
from test_game_combat import KANEZAKA_DM, Run, in_world

from ow174 import cli
from ow174.game import bodytrace, movelog
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER, heroes
from ow174.game.server import GameServer
from ow174.game.server_snapshot import export_server_state, snapshot_digest
from ow174.launcher import LaunchError
from ow174.lobby.server import LobbyServer
from ow174.lobby.settings import Settings

HANZO = 0x02E0000000000005


class ProfileArgumentTests(unittest.TestCase):
    def test_default_does_not_load_test_environment_profiles(self):
        with patch.dict(os.environ, ORPHEUS_HANZO_PROFILE="missing",
                        ORPHEUS_HANZO_SONIC_PROFILE="missing"):
            self.assertIsNone(cli._combat_profiles(cli.parse_args(["--mode", "server"])))

    def test_bad_profile_fails_before_client_profile_or_service_work(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "invalid.json"
            profile.write_text('{"format_version":1}', encoding="utf-8")
            case = cli_tests.NoLaunchTests()
            with case.host_services("server", options=(
                "--reconstructed-hanzo-sonic-profile", str(profile),
            )) as (args, mocks, _server):
                with self.assertRaisesRegex(LaunchError, "Invalid reconstructed Hanzo profile"):
                    cli.run(args)
                for name in ("find_game", "ensure_requirements", "load_or_create_profile",
                             "LobbyServer", "_bind", "_start_game_server"):
                    mocks[name].assert_not_called()

    def test_local_profiles_are_refused_for_join_or_disabled_game_server(self):
        for mode, flags in (("join", []), ("join-tournament", []), ("server", ["--game-port", "0"])):
            with self.subTest(mode=mode), patch.object(cli, "join") as join:
                args = cli.parse_args(["--mode", mode, *flags,
                                       "--reconstructed-hanzo-dragon-profile", "missing.json"])
                with self.assertRaisesRegex(LaunchError, "local game server"):
                    cli.run(args)
                join.assert_not_called()


class ProfileStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = [os.environ.get(name) for name in
                 ("ORPHEUS_HANZO_SONIC_PROFILE", "ORPHEUS_HANZO_PROFILE")]
        if not all(paths):
            raise unittest.SkipTest("requires explicitly extracted Sonic and dragon profiles")
        cls.flags = ["--reconstructed-hanzo-sonic-profile", paths[0],
                     "--reconstructed-hanzo-dragon-profile", paths[1]]

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def configured_server(self):
        case = cli_tests.NoLaunchTests()
        with case.host_services("server", options=self.flags) as (args, mocks, _server):
            cli.run(args)
            settings = mocks["LobbyServer"].call_args.args[0]
        lobby = SimpleNamespace(settings=settings, game_left=None, skin_of=lambda *_: (0, False),
                                game_joined=None, game_clock_ready=None)
        with patch.object(GameServer, "start") as start:
            LobbyServer.start_game_server(lobby)
        start.assert_called_once()
        server = lobby.game
        self.assertIs(server.combat_profiles, settings.combat_profiles)
        server.collision_provider_factory = lambda: StaticCollisionProvider(flat_world(
            KANEZAKA_DM.spawns[0].position[1],
        ))
        return server

    def arena(self, server, distance):
        server.create_match(KANEZAKA_DM, [(1, "Hanzo", HANZO, 0, False),
                                          (2, "Target", SOLDIER, 1, False)])
        run = Run.__new__(Run)
        run.match = server.matches[-1]
        run.players = run.match.players
        run.frame = run.match.tick = 1000
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in run.players:
            in_world(run.match, player)
            run.match.switch_hero(player, heroes()[player.hero.guid])
        hero, target = run.players
        x, y, z = hero.mover.position
        target.mover.position = [x, y, z + distance]
        for _ in range(20):
            run.step({})
        return run, hero, target

    def test_normal_server_created_matches_execute_sonic_and_dragon_without_manual_mount(self):
        server = self.configured_server()
        run, hero, target = self.arena(server, 5)
        sonic_tests.SonicPlayerTests.fire(run, hero, target)
        self.assertEqual(run.match.combat.vitals[target.body].total, 75)
        self.assertTrue(sonic_tests.SonicPlayerTests.flagged(target))
        self.assertTrue(run.match.combat.revealed_to(hero, target))
        run, hero, target = self.arena(server, 20)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        dragon_tests.DragonPlayerTests.cast(run, hero)
        self.assertTrue(run.match.combat.vitals[target.body].dead)
        self.assertEqual(len(run.match.combat.dragons), 1)
        self.assertIs(run.match.combat.dragon_profile, server.combat_profiles.dragon)
        self.assertIs(run.match.combat.sonic_profile, server.combat_profiles.sonic)

    def test_future_match_profile_quantities_and_sources_participate_in_snapshot(self):
        server = self.configured_server()
        first = snapshot_digest(export_server_state(server))
        profiles = server.combat_profiles
        server.combat_profiles = replace(profiles, sonic=replace(profiles.sonic, radius=8))
        self.assertNotEqual(first, snapshot_digest(export_server_state(server)))
        server.combat_profiles = replace(profiles, dragon=replace(profiles.dragon, source_digest="0" * 64))
        self.assertNotEqual(first, snapshot_digest(export_server_state(server)))
        self.assertIsNone(Settings().combat_profiles)
