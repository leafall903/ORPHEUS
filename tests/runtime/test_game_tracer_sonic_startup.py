"""Launch configuration and real shots in server-created Hanzo matches."""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_cli as cli_tests
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_tracer_hanzo import FULL_SONIC, HANZO, SHORT_SONIC

from ow174 import cli
from ow174.game import dragon, sonic
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST
from ow174.game.server import GameServer
from ow174.launcher import LaunchError
from ow174.lobby.server import LobbyServer

TRACER = 0x02E0000000000003
OPTION = "--reconstructed-hanzo-sonic-profile"


class TracerSonicStartupTests(unittest.TestCase):
    def test_default_does_not_use_test_profile_environment(self):
        with (
            patch.dict(os.environ, ORPHEUS_HANZO_SONIC_PROFILE="missing.json"),
            cli_tests.NoLaunchTests().host_services("server") as (args, mocks, _),
        ):
            cli.run(args)
        self.assertIsNone(mocks["LobbyServer"].call_args.args[0].sonic_profile)

    def test_invalid_profile_fails_before_lobby_or_client_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.json"
            path.write_text('{"format_version": 1}', encoding="utf-8")
            options = (OPTION, str(path))
            with cli_tests.NoLaunchTests().host_services("server", options=options) as (args, mocks, _):
                with self.assertRaisesRegex(LaunchError, "Invalid reconstructed Sonic profile"):
                    cli.run(args)
                for name in ("find_game", "ensure_requirements", "load_or_create_profile",
                             "LobbyServer", "_bind", "_start_game_server"):
                    mocks[name].assert_not_called()

    def test_profile_requires_local_game_server(self):
        for flags in (["--mode", "join"], ["--mode", "join-tournament"],
                      ["--mode", "server", "--game-port", "0"]):
            with self.subTest(flags=flags):
                args = cli.parse_args([*flags, OPTION, "missing.json"])
                with self.assertRaisesRegex(LaunchError, "requires a local game server"):
                    cli.run(args)

    def configured_server(self, extra_options=()):
        path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
        if not path:
            self.skipTest("requires explicitly extracted Sonic profile")
        options = (OPTION, path, *extra_options)
        with cli_tests.NoLaunchTests().host_services("server", options=options) as (args, mocks, _):
            cli.run(args)
            settings = mocks["LobbyServer"].call_args.args[0]
        lobby = SimpleNamespace(settings=settings, game_left=None, skin_of=lambda *_: (0, False),
                                game_joined=None, game_clock_ready=None)
        with patch.object(GameServer, "start") as start:
            LobbyServer.start_game_server(lobby)
        start.assert_called_once()
        server = lobby.game
        self.assertIs(server.sonic_profile, settings.sonic_profile)
        self.assertIs(server.dragon_profile, settings.dragon_profile)
        server.collision_provider_factory = lambda: StaticCollisionProvider(flat_world(
            KANEZAKA_DM.spawns[0].position[1],
        ))
        return server

    def arena(self, server, distance):
        game_map = replace(KANEZAKA_DM, free_for_all=False)
        server.create_match(game_map, [(1, "Hanzo", HANZO, 0, False), (2, "Tracer", TRACER, 1, False)])
        run = Run.__new__(Run)
        run.match = server.matches[-1]
        run.players = run.match.players
        run.frame = run.match.tick = 1000
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertIs(run.match.combat.sonic_profile, server.sonic_profile)
        self.assertIs(run.match.combat.dragon_profile, server.dragon_profile)
        for player in run.players:
            in_world(run.match, player)
            self.assertTrue(run.match.switch_hero(player, heroes()[player.hero.guid]))
        hanzo, tracer = run.players
        x, y, z = hanzo.mover.position
        tracer.mover.position = [x, y, z + distance]
        for _ in range(20):
            run.step({})
        return run, hanzo, tracer, aim(run.match.combat._eye(hanzo), (x, y + 1, z + distance))

    def test_new_matches_execute_short_and_combined_sonic_reveal(self):
        server = self.configured_server()
        for combined, distance in ((False, 6), (True, 10)):
            with self.subTest(combined=combined):
                run, hanzo, tracer, direction = self.arena(server, distance)
                fight = run.match.combat
                run.step({hanzo: (*direction, 24 if combined else 8)})
                for _ in range(25 if combined else 1):
                    run.step({})
                for _ in range(24 if combined else 5):
                    run.step({hanzo: (*direction, FIRE)})
                    if fight.rockets:
                        break
                if not combined:
                    self.assertFalse(fight.rockets)
                    run.step({hanzo: (*direction, 0)})
                arrow, = fight.rockets
                self.assertEqual(arrow.definition, FULL_SONIC if combined else SHORT_SONIC)
                if combined:
                    self.assertEqual((arrow.direct.amount, arrow.bounces), (70, 1))
                for _ in range(35):
                    run.step({})
                    if fight.sonars:
                        break
                beacon, = fight.sonars
                self.assertEqual(beacon.attached, tracer.body)
                self.assertTrue(fight.revealed_to(hanzo, tracer))
                self.assertTrue(expr.truthy(tracer.body_script.component.entity_var(sonic.REVEALED).value()))
                health = fight.vitals[tracer.body].total
                self.assertEqual(health, 150 - arrow.direct.amount)
        self.assertEqual(len(server.matches), 2)


    def test_new_match_runs_dragonstrike_with_both_profiles_configured(self):
        path = os.environ.get("ORPHEUS_HANZO_DRAGON_PROFILE")
        if not path:
            self.skipTest("requires explicitly extracted Dragonstrike profile")
        with patch.object(dragon, "load_profile", wraps=dragon.load_profile) as load:
            server = self.configured_server(("--reconstructed-hanzo-dragon-profile", path))
            run, hanzo, tracer, direction = self.arena(server, 20)
            self.arena(server, 20)
        load.assert_called_once_with(Path(path), reconstructed_rates=True)
        self.assertIsNotNone(server.sonic_profile)
        self.assertIsNotNone(server.dragon_profile)
        body = hanzo.body_script
        body.ult_seconds = 0
        body.set_ult_charge(body.component.entity_var(ULT_COST).value())
        run.step({hanzo: (*direction, 32)})
        fight = run.match.combat
        for _ in range(170):
            run.step({hanzo: (*direction, 0)})
            if fight.dragons and fight.vitals[tracer.body].total < 150:
                break
        self.assertEqual(len(fight.dragons), 1)
        self.assertAlmostEqual(fight.vitals[tracer.body].total, 145.2)
        self.assertFalse(server.matches[-1].combat.dragons)
