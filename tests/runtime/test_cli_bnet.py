"""CLI checks with the prepared Battle.net descriptors."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174 import cli, paths
from ow174.accounts.registry import account_id_for


@unittest.skipUnless(paths.BNET_DESCRIPTORS.is_file(), "requires prepared Battle.net descriptors")
class BnetCliTests(unittest.TestCase):
    def test_the_retail_bnet_service_uses_the_configured_stall_port_and_fixed_rpc_port(self):
        from ow174.bnet.service import RPC_PORT

        server = SimpleNamespace(settings=SimpleNamespace(port=3724))
        for port in (1119, 1118):
            with self.subTest(port=port), patch("ow174.bnet.service.start_bnet") as start_bnet:
                cli._start_bnet(server, port)
                self.assertEqual(start_bnet.call_args.args[1], "127.0.0.1:3724")
                self.assertEqual(start_bnet.call_args.kwargs, {"ports": (port, RPC_PORT)})

    def test_a_join_logs_in_with_the_name_and_goes_to_that_server(self):
        # The game gets the full menu from a Battle.net emulator of its own, which puts the name in
        # the session key and sends the game on to the server's lobby.
        self.check_join(1119)

    def test_a_join_uses_the_custom_local_bnet_port_for_the_service_and_game(self):
        self.check_join(1118)

    def check_join(self, port):
        from ow174.bnet.service import RPC_PORT

        args = cli.parse_args([
            "--mode", "join", "--server", "lobby.example.org:12357", "--name", "Jinxzi",
            "--bnet-port", str(port),
        ])
        with (
            patch.object(cli, "find_game", return_value=Path("Overwatch.exe")),
            patch.object(cli, "close_running_copy"),
            patch.object(cli, "ensure_requirements"),
            patch.object(cli, "ensure_relay_dll", return_value=Path("relay.dll")),
            patch.object(cli.socket, "gethostbyname", return_value="1.2.3.4"),
            patch.object(cli, "ask_battle_tag", return_value="XQC#6691") as ask,
            patch("ow174.bnet.service.start_bnet") as start_bnet,
            patch.object(cli, "RetailGames") as games,
        ):
            cli.join(args)
        player, lobby = start_bnet.call_args.args
        self.assertEqual(start_bnet.call_args.kwargs, {"ports": (port, RPC_PORT)})
        self.assertEqual(lobby, "1.2.3.4:12357")
        account = player(("127.0.0.1", 50000))
        self.assertEqual((account.account, account.name), (account_id_for("Jinxzi"), "Jinxzi"))
        ask.assert_called_once_with("1.2.3.4", 12357, "Jinxzi")
        self.assertEqual(account.battle_tag, "XQC#6691")  # the nickname the host shows
        games.assert_called_once_with(Path("Overwatch.exe"), Path("relay.dll"), "auto", 30.0, bnet_port=port)
        games.return_value.start.return_value.wait.assert_called_once()
