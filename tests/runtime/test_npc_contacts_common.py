"""Ordinary player movement uses the existing NPC capsule contact provider."""

import json
import unittest
from pathlib import Path

from ow174.game import combat, npc_console
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import find_hero
from tests.runtime.test_game_combat import Run


class CommonNpcPeerTests(unittest.TestCase):
    def test_ordinary_heroes_block_on_living_enemies_and_preserve_exclusions(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/common-npc-peer-v1.json'
        contract = json.loads(path.read_text())
        inputs, expected = contract['input'], contract['expected']
        for hero in inputs['heroes']:
            for kind in inputs['target_kinds']:
                with self.subTest(hero=hero, target=kind):
                    run = Run()
                    player = run.players[0]
                    self.assertTrue(run.match.switch_hero(player, find_hero(hero)))
                    run.match.bots.clear()
                    run.match._pending_bot_scripts.clear()
                    run.match.collision_world = flat_world(inputs['ground_height'])
                    player.mover.position = list(inputs['owner_position'])
                    player.mover.body = None
                    player.body_script.create_arrived = True
                    if kind != 'none':
                        npc_console.Console({}).press('hero', run.match, player, hero=inputs['target_hero'])
                        target = run.match.bots[-1]
                        target.position = tuple(inputs['target_position'])
                        if kind == 'ally':
                            target.team = player.team
                        if kind == 'dead':
                            target.dead = True
                    first = inputs['first_frame']
                    for tick in range(first, first + inputs['commands']):
                        run.match.update(tick * combat.TICK, tick)
                        player.take_commands([Command(tick, yaw=inputs['yaw'], forward=inputs['forward'])])
                    stop = expected['enemy_stop_z'] if kind == 'enemy' else expected['unobstructed_z']
                    self.assertAlmostEqual(player.mover.position[2], stop)
                    self.assertFalse(player.mover._engine_body().contacts)
