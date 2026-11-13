"""Picking the mode when START.bat is double-clicked, and joining someone else's server."""

import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ow174 import cli, paths
from ow174.jam import handshake
from ow174.launcher import LaunchError


class AskModeTests(unittest.TestCase):
    def ask(self, *answers):
        with patch("builtins.input", side_effect=answers), patch("builtins.print"):
            return cli.ask_mode()

    def test_enter_picks_retail(self):
        self.assertEqual(self.ask(""), "retail")

    def test_numbers_pick_the_modes(self):
        self.assertEqual(self.ask("1"), "retail")
        self.assertEqual(self.ask(" 2 "), "tournament")
        self.assertEqual(self.ask("3"), "host")
        self.assertEqual(self.ask("4"), "server")
        self.assertEqual(self.ask("5"), "join")
        self.assertEqual(self.ask("6"), "join-tournament")

    def test_anything_else_asks_again(self):
        self.assertEqual(self.ask("tournament", "9", "0", "2"), "tournament")

    def test_a_mode_on_the_command_line_is_not_asked(self):
        self.assertEqual(cli.parse_args(["--mode", "tournament"]).mode, "tournament")
        self.assertIsNone(cli.parse_args([]).mode)


class BnetPortTests(unittest.TestCase):
    def test_the_local_bnet_port_defaults_to_1119(self):
        self.assertEqual(cli.parse_args([]).bnet_port, 1119)

    def test_a_custom_port_and_the_valid_boundaries_are_accepted(self):
        for port in (1, 1118, 65535):
            with self.subTest(port=port):
                self.assertEqual(cli.parse_args(["--bnet-port", str(port)]).bnet_port, port)

    def test_invalid_ports_are_rejected_before_startup(self):
        for port in ("0", "-1", "65536", "not-a-port"):
            with self.subTest(port=port), patch("sys.stderr"), self.assertRaises(SystemExit):
                cli.parse_args(["--bnet-port", port])



class NoLaunchTests(unittest.TestCase):
    @contextmanager
    def host_services(self, mode, *, no_launch=True, options=()):
        flags = ["--mode", mode, *options]
        if no_launch:
            flags.append("--no-launch")
        args = cli.parse_args(flags)
        names = (
            "find_game", "close_running_copy", "start_game", "ensure_requirements", "ensure_relay_dll",
            "load_or_create_profile", "LobbyServer", "_bind", "_start_game_server", "start_dashboard",
            "RetailGames", "_start_bnet", "_log_banner", "_require_runtime",
        )
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(cli, name)) for name in names}
            mocks["thread"] = stack.enter_context(patch.object(cli.threading, "Thread"))
            server = mocks["LobbyServer"].return_value
            server.games = None  # the real LobbyServer starts without a client manager
            mocks["find_game"].return_value = Path("Overwatch.exe")
            mocks["ensure_relay_dll"].return_value = Path("relay.dll")
            yield args, mocks, server

    def test_no_launch_is_explicit_and_defaults_off(self):
        self.assertFalse(cli.parse_args([]).no_launch)
        self.assertTrue(cli.parse_args(["--no-launch"]).no_launch)

    def test_all_host_modes_keep_services_and_never_touch_a_game(self):
        for mode in ("retail", "host", "tournament", "server"):
            with self.subTest(mode=mode), self.host_services(mode) as (args, mocks, server):
                cli.run(args)
                for name in (
                    "find_game", "close_running_copy", "start_game", "ensure_relay_dll", "RetailGames",
                ):
                    mocks[name].assert_not_called()
                self.assertIsNone(server.games)  # also disables dashboard client launching
                self.assertFalse(mocks["LobbyServer"].call_args.args[0].build_missing_collision)
                mocks["_bind"].assert_called_once_with(server)
                mocks["_start_game_server"].assert_called_once_with(server)
                mocks["start_dashboard"].assert_called_once_with(server, port=args.dashboard_port)
                mocks["ensure_requirements"].assert_called_once()
                mocks["thread"].return_value.start.assert_called_once()
                server.serve_forever.assert_called_once_with(mocks["_bind"].return_value)
                if mode in ("retail", "host"):
                    mocks["_start_bnet"].assert_called_once_with(server, args.bnet_port)
                else:
                    mocks["_start_bnet"].assert_not_called()

    def test_no_launch_preserves_the_service_disable_options(self):
        with self.host_services("server", options=("--game-port", "0", "--dashboard-port", "0")) as values:
            args, mocks, _server = values
            cli.run(args)
            mocks["_start_game_server"].assert_not_called()
            mocks["start_dashboard"].assert_not_called()
            mocks["ensure_requirements"].assert_not_called()

    def test_join_modes_are_refused_before_network_or_client_work(self):
        for mode in ("join", "join-tournament"):
            with (
                self.subTest(mode=mode),
                patch.object(cli, "join") as join,
                patch.object(cli, "join_tournament") as tournament,
                self.assertRaisesRegex(LaunchError, "join mode"),
            ):
                cli.run(cli.parse_args(["--mode", mode, "--no-launch"]))
            join.assert_not_called()
            tournament.assert_not_called()

    def test_a_client_path_is_refused_instead_of_silently_ignored(self):
        with self.host_services("retail", options=("--game-exe", "Overwatch.exe")) as (args, mocks, _server):
            with self.assertRaisesRegex(LaunchError, "--game-exe"):
                cli.run(args)
            mocks["LobbyServer"].assert_not_called()
            mocks["find_game"].assert_not_called()

    def test_no_launch_without_a_mode_does_not_ask_an_interactive_question(self):
        with (
            patch.object(cli.sys.stdin, "isatty", return_value=True),
            patch.object(cli, "ask_mode") as ask,
            patch.object(cli, "setup_logging"),
            patch.object(cli, "run") as run,
        ):
            self.assertEqual(cli.main(["--no-launch"]), 0)
        ask.assert_not_called()
        self.assertEqual(run.call_args.args[0].mode, "retail")
        self.assertTrue(run.call_args.args[0].no_launch)

    def test_normal_retail_still_uses_its_client_manager(self):
        with self.host_services("retail", no_launch=False) as (args, mocks, server):
            cli.run(args)
            mocks["find_game"].assert_called_once_with(None)
            mocks["close_running_copy"].assert_called_once_with(Path("Overwatch.exe"))
            mocks["ensure_relay_dll"].assert_called_once()
            self.assertTrue(mocks["LobbyServer"].call_args.args[0].build_missing_collision)
            mocks["RetailGames"].assert_called_once_with(
                Path("Overwatch.exe"), Path("relay.dll"), "auto", 30.0, bnet_port=args.bnet_port,
            )
            self.assertIs(server.games, mocks["RetailGames"].return_value)
            server.games.start.assert_called_once()

    def test_missing_retail_relay_does_not_close_a_running_game_or_start_services(self):
        with self.host_services("retail", no_launch=False) as (args, mocks, _server):
            mocks["ensure_relay_dll"].side_effect = LaunchError("Missing Windows retail relay")
            with self.assertRaisesRegex(LaunchError, "Missing Windows retail relay"):
                cli.run(args)
            mocks["close_running_copy"].assert_not_called()
            mocks["LobbyServer"].assert_not_called()
            mocks["RetailGames"].assert_not_called()

    def test_normal_tournament_still_starts_the_requested_client(self):
        with self.host_services("tournament", no_launch=False) as (args, mocks, _server):
            cli.run(args)
            mocks["start_game"].assert_called_once_with(
                Path("Overwatch.exe"), ["--tank_TournamentMode", "--lobbyServer=127.0.0.1:3724"], "auto",
            )


class RuntimeDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.local = self.root / "runtime-data"
        self.local.mkdir()

    def test_empty_runtime_directory_preserves_legacy_lookup(self):
        self.assertIsNone(paths._runtime_directory(self.root, ""))

    def test_local_pack_is_discovered_with_a_manifest_or_data_directory(self):
        marker = self.local / "manifest.json"
        marker.write_text("{}", encoding="utf-8")
        self.assertEqual(paths._runtime_directory(self.root, None), self.local)
        marker.unlink()
        (self.local / "data").mkdir()
        self.assertEqual(paths._runtime_directory(self.root, None), self.local)

    def test_explicit_path_takes_precedence_even_if_it_is_missing(self):
        (self.local / "data").mkdir()
        override = self.root / "other-runtime"
        self.assertEqual(paths._runtime_directory(self.root, str(override)), override)
        self.assertFalse(override.exists())


class RuntimeCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.setup_logging = self.enterContext(patch.object(cli, "setup_logging"))
        self.enterContext(patch.object(cli.sys.stdin, "isatty", return_value=True))
        for name in (
            "ask_mode", "find_game", "close_running_copy", "start_game", "ensure_requirements",
            "ensure_relay_dll", "load_or_create_profile", "LobbyServer", "_bind", "_start_bnet",
            "_start_game_server", "start_dashboard", "RetailGames", "join", "join_tournament",
        ):
            error = AssertionError(f"Runtime check called {name}")
            self.enterContext(patch.object(cli, name, side_effect=error))
        handshake.lobby_state_blob_key.cache_clear()
        self.addCleanup(handshake.lobby_state_blob_key.cache_clear)

    @contextmanager
    def runtime_layout(self, *, external):
        root = self.folder / ("runtime" if external else "checkout")
        data = root / "data"
        data.mkdir(parents=True, exist_ok=True)
        runtime = root if external else None
        bnet = root if external else root / "ow174"
        with (
            patch.object(cli, "RUNTIME_DIR", runtime),
            patch.object(paths, "ROOT", root),
            patch.object(paths, "RUNTIME_DIR", runtime),
            patch.object(paths, "DATA_DIR", data),
            patch.object(paths, "BNET_DESCRIPTORS", bnet / "bnet" / "descriptors.pb"),
            patch.object(handshake, "LOBBY_STATE_BLOB_KEY", data / "lobby_state_blob.key"),
        ):
            yield data

    def test_incomplete_runtime_lists_missing_inputs_before_game_lookup_or_services(self):
        for external in (False, True):
            for checking in (False, True):
                with (
                    self.subTest(external=external, checking=checking),
                    self.runtime_layout(external=external),
                ):
                    args = ["--check-runtime"] if checking else [
                        "--mode", "retail", "--game-exe", "Overwatch",
                    ]
                    with self.assertLogs("ow174", level="ERROR") as messages:
                        self.assertEqual(cli.main(args), 1)
                    error = "\n".join(messages.output)
                    source = "the configured runtime directory" if external else "the source checkout"
                    self.assertIn(f"Missing runtime files in {source}", error)
                    self.assertIn("data/retail_templates.json", error)
                    self.assertIn("data/game_graphs_174.json.gz", error)
                    self.assertIn("data/lobby_state_blob.key", error)
                    descriptor = "bnet/descriptors.pb" if external else "ow174/bnet/descriptors.pb"
                    self.assertIn(descriptor, error)
                    if external:
                        self.assertNotIn("ow174/bnet/descriptors.pb", error)

    def test_lobby_only_runtime_check_does_not_require_match_data_or_descriptors(self):
        with self.runtime_layout(external=True) as data:
            for name in paths.LOBBY_RUNTIME_DATA:
                (data / name).write_bytes(bytes(64))
            args = ["--mode", "server", "--game-port", "0", "--check-runtime"]
            with self.assertLogs("ow174", level="INFO") as messages:
                self.assertEqual(cli.main(args), 0)
        self.assertIn("signing input has the expected length", "\n".join(messages.output))
        self.setup_logging.assert_called_once_with(None)

    def test_runtime_check_refuses_a_wrong_length_signing_input(self):
        with self.runtime_layout(external=True) as data:
            for name in paths.LOBBY_RUNTIME_DATA:
                (data / name).write_bytes(b"synthetic fixture")
            args = ["--mode", "server", "--game-port", "0", "--check-runtime"]
            with self.assertLogs("ow174", level="ERROR") as messages:
                self.assertEqual(cli.main(args), 1)
        self.assertIn("signing key must be 64 bytes", "\n".join(messages.output))

    def test_runtime_check_rejects_join_modes_without_contacting_the_host(self):
        for mode in ("join", "join-tournament"):
            with self.subTest(mode=mode), self.assertRaisesRegex(LaunchError, "checks host modes"):
                cli.run(cli.parse_args(["--mode", mode, "--check-runtime"]))


class JoinTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.saved = Path(temp.name) / "server_address.txt"

    def ask(self, *answers):
        with patch("builtins.input", side_effect=answers), patch("builtins.print"):
            return cli.ask_server(self.saved)

    def test_server_addresses(self):
        self.assertTrue(cli.is_server_address("1.2.3.4:12357"))
        self.assertTrue(cli.is_server_address("lobby.example.org:3724"))
        for text in ("", "1.2.3.4", ":12357", "1.2.3.4:port", "1.2.3.4:0", "1.2.3.4:70000", "a b:1"):
            self.assertFalse(cli.is_server_address(text), text)

    def test_the_address_is_remembered_and_enter_reuses_it(self):
        self.assertEqual(self.ask("nope", "1.2.3.4:12357"), "1.2.3.4:12357")
        self.assertEqual(self.ask(""), "1.2.3.4:12357")

    def test_a_tournament_join_starts_only_the_game_on_that_server(self):
        game = Path("Overwatch")
        with ExitStack() as stack:
            listener = stack.enter_context(cli.socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(1.0)
            address = f"127.0.0.1:{listener.getsockname()[1]}"
            stack.enter_context(patch.object(cli, "setup_logging"))
            find = stack.enter_context(patch.object(cli, "find_game", return_value=game / "Overwatch.exe"))
            stack.enter_context(patch.object(cli, "close_running_copy"))
            start_game = stack.enter_context(patch.object(cli, "start_game"))
            skipped = [
                stack.enter_context(patch.object(cli, name))
                for name in ("_require_runtime", "LobbyServer", "ensure_requirements", "ensure_relay_dll")
            ]
            result = cli.main(["--mode", "join-tournament", "--server", address, "--game-exe", str(game)])
            listener.accept()[0].close()
        self.assertEqual(result, 0)
        find.assert_called_once_with(game)
        start_game.assert_called_once_with(
            game / "Overwatch.exe", ["--tank_TournamentMode", f"--lobbyServer={address}"], "auto"
        )
        for operation in skipped:
            operation.assert_not_called()

    def test_a_bad_address_on_the_command_line_is_refused(self):
        args = SimpleNamespace(server="1.2.3.4", name="Jinxzi", game_exe=None, locale="auto")
        for join in (cli.join, cli.join_tournament):
            with self.assertRaises(LaunchError):
                join(args)

    def test_a_join_to_a_server_that_does_not_answer_starts_no_game(self):
        # Checked before anything else: a running game is not closed for nothing.
        args = SimpleNamespace(server="1.2.3.4:12357", name="Jinxzi", game_exe=None, locale="auto")
        refused = ConnectionRefusedError("refused")
        with (
            patch.object(cli.socket, "gethostbyname", return_value="1.2.3.4"),
            patch.object(cli.socket, "create_connection", side_effect=refused),
            patch.object(cli, "close_running_copy") as close,
        ):
            with self.assertRaisesRegex(LaunchError, "Can't reach"):
                cli.join_tournament(args)
            with patch.object(cli, "ask_battle_tag", side_effect=refused), self.assertRaises(LaunchError):
                cli.join(args)
            older_server = patch.object(cli, "ask_battle_tag", return_value="")
            with older_server, self.assertRaisesRegex(LaunchError, "older version"):
                cli.join(args)
        close.assert_not_called()

    def test_hosting_listens_for_other_pcs(self):
        self.assertEqual(cli.listen_host(cli.parse_args(["--mode", "server"])), "0.0.0.0")
        self.assertEqual(cli.listen_host(cli.parse_args(["--mode", "host"])), "0.0.0.0")
        self.assertEqual(cli.listen_host(cli.parse_args(["--mode", "retail"])), "127.0.0.1")
        self.assertEqual(
            cli.listen_host(cli.parse_args(["--mode", "server", "--host", "10.0.0.2"])), "10.0.0.2"
        )

    def test_the_public_address_is_remembered_and_dash_drops_it(self):
        saved = self.saved.with_name("public_address.txt")

        def ask(*answers):
            with patch("builtins.input", side_effect=answers), patch("builtins.print"):
                return cli.ask_public_address(saved)

        self.assertEqual(ask(""), "")  # nothing saved yet: only the host's network plays
        self.assertEqual(ask("1.2.3.4:3724", "1.2.3.4"), "1.2.3.4")
        self.assertEqual(ask(""), "1.2.3.4")
        self.assertEqual(ask("-"), "")
        self.assertFalse(saved.exists())

    def test_names(self):
        for name in ("Jinxzi", "Игрок_2", "a-b"):
            self.assertTrue(cli.is_player_name(name), name)
        for name in ("", "two words", "Tag#1234", "a" * 25):
            self.assertFalse(cli.is_player_name(name), name)

    def test_the_name_is_remembered_and_enter_reuses_it(self):
        saved = self.saved.with_name("player_name.txt")
        with patch("builtins.input", side_effect=["bad name", "Jinxzi", ""]), patch("builtins.print"):
            self.assertEqual(cli.ask_name(saved), "Jinxzi")
            self.assertEqual(cli.ask_name(saved), "Jinxzi")

    def test_missing_join_relay_does_not_close_a_running_game(self):
        args = cli.parse_args(["--mode", "join", "--server", "127.0.0.1:12357", "--name", "Jinxzi"])
        with (
            patch.object(cli.socket, "gethostbyname", return_value="127.0.0.1"),
            patch.object(cli, "ask_battle_tag", return_value="XQC#6691"),
            patch.object(cli, "find_game", return_value=Path("Overwatch.exe")),
            patch.object(cli, "ensure_requirements"),
            patch.object(cli, "ensure_relay_dll", side_effect=LaunchError("Missing Windows retail relay")),
            patch.object(cli, "close_running_copy") as close,
            patch.object(cli, "RetailGames") as games,
            self.assertRaisesRegex(LaunchError, "Missing Windows retail relay"),
        ):
            cli.join(args)
        close.assert_not_called()
        games.assert_not_called()



if __name__ == "__main__":
    unittest.main()
