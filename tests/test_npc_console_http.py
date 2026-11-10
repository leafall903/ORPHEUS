"""Real loopback HTTP actions reach a locked GameServer/Match lifecycle."""

import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.dashboard.server import start_dashboard
from ow174.game import npc
from ow174.game.script import graph
from ow174.game.server import GameServer


class NpcConsoleHttpTests(unittest.TestCase):
    def setUp(self):
        body_patch = patch.object(graph, 'entity_body', return_value=None)
        body_patch.start()
        self.addCleanup(body_patch.stop)
        self.run = Run()
        self.match, self.player = self.run.match, self.run.players[0]
        sample = self.match.bots[0]
        self.match.bots.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'npcs.json'
        path.write_text(json.dumps({'format_version': 1, 'definitions': [
            {'key': '1', 'hero': f'0x{sample.hero:016X}', 'body': f'0x{sample.body:016X}',
             'name': 'Synthetic enemy', 'health': 200, 'armour': 0, 'shields': 0}]}), encoding='utf-8')
        env = patch.dict(os.environ, {'ORPHEUS_NPC_CATALOG': str(path)})
        env.start()
        self.addCleanup(env.stop)
        self.game = GameServer()
        self.game.matches.append(self.match)
        account = SimpleNamespace(account_lo=self.player.account_lo)
        lobby = SimpleNamespace(game=self.game, dashboard_account=lambda: account)
        self.http = start_dashboard(lobby, port=0)
        self.addCleanup(self.close)
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def close(self):
        self.http.shutdown()
        self.http.server_close()

    def request(self, key, **options):
        request = urllib.request.Request(self.url + '/api/npc_console',
            data=json.dumps({'key': key, **options}).encode(), headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_http_menu_spawn_and_remove_change_only_the_selected_match(self):
        status, menu = self.request('=')
        self.assertEqual(status, 200)
        self.assertTrue(menu['menu_open'])
        self.assertEqual(self.request('1')[1]['handled'], False)
        self.player.mover.yaw = 0
        real_spawn = npc.spawn

        def locked_spawn(*args, **kwargs):
            self.assertTrue(self.game.lock._is_owned())
            return real_spawn(*args, **kwargs)

        with patch.object(npc, 'spawn', side_effect=locked_spawn):
            status, created = self.request('1', menu_open=True)
        self.assertEqual(status, 200)
        self.assertEqual(self.match.bots[0].entity, created['entity'])
        self.assertTrue(self.match.bots[0].retain_corpse)
        status, removed = self.request('-')
        self.assertEqual(status, 200)
        self.assertEqual(removed['entities'], [created['entity']])
        self.assertEqual(self.match.bots, [])

    def test_refused_keys_clock_and_missing_catalog_never_spawn(self):
        self.assertEqual(self.request('unknown')[0], 400)
        self.assertEqual(self.request('1', menu_open='true')[0], 400)
        self.game._clock_ticks_left = 0
        self.assertEqual(self.request('1', menu_open=True)[0], 400)
        with patch.dict(os.environ, {'ORPHEUS_NPC_CATALOG': ''}):
            self.assertEqual(self.request('=')[0], 409)
        self.assertEqual(self.match.bots, [])

    def test_controls_require_online_player_and_unknown_primary_is_not_success(self):
        self.player.mover.yaw = 0
        _, created = self.request('1', menu_open=True)
        bot = self.match.bots[0]
        x, y, z = bot.position
        self.player.mover.yaw, self.player.mover.pitch = aim(
            self.match.combat._eye(self.player), (x, y + 1, z))
        self.assertEqual(self.request('0')[0], 400)  # synthetic fixture intentionally has no weapon script
        self.assertFalse(bot.firing)
        self.assertEqual(self.request('{')[1]['count'], 1)
        self.player.gone = True
        self.assertEqual(self.request('-')[0], 400)
        self.assertEqual(bot.entity, created['entity'])

    def test_frontend_and_module_are_served(self):
        for path, expected in (('/npc-console', b'PVE NPC'),
                               ('/assets/npc-console.mjs', b'NpcConsoleClient')):
            with urllib.request.urlopen(self.url + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(expected, response.read())

    def test_named_account_operations_cannot_remove_another_matchs_npcs(self):
        other = Run()
        other.match.bots.clear()
        other.players[0].account_lo = 999
        other.players[0].mover.yaw = 0
        self.game.matches.append(other.match)
        accounts = {'A': SimpleNamespace(account_lo=self.player.account_lo),
                    'B': SimpleNamespace(account_lo=999)}
        self.http.dashboard_api.lobby.accounts = SimpleNamespace(
            all_saved=lambda: list(accounts), get=lambda name: accounts[name])
        status, _ = self.request('1', name='B', menu_open=True)
        self.assertEqual(status, 200)
        self.assertEqual(len(other.match.bots), 1)
        self.assertEqual(self.match.bots, [])
        self.assertEqual(self.request('-', name='A')[1]['entities'], [])
        self.assertEqual(len(other.match.bots), 1)
        self.assertEqual(self.request('-', name='B')[0], 200)
        self.assertEqual(other.match.bots, [])

    def test_graph_bundle_override_loads_external_entities_without_replacing_runtime(self):
        path = Path(self.temp.name) / 'external.json.gz'
        path.write_bytes(gzip.compress(json.dumps({'graphs': [], 'bodies': {},
            'entities': {'0x0400000000001403': {'name': 'synthetic marker'}}}).encode()))
        env = {**os.environ, 'ORPHEUS_GRAPH_BUNDLE': str(path)}
        result = subprocess.check_output([sys.executable, '-c',
            'from ow174.game.script import graph; print(graph.entity_body(0x0400000000001403)["name"])'],
            cwd=Path(__file__).resolve().parents[1], env=env, text=True)
        self.assertEqual(result.strip(), 'synthetic marker')
