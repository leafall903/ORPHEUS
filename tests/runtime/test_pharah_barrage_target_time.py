"""Actual Barrage uses retained target poses during physical catch-up."""
import json
import unittest
from pathlib import Path

from ow174.game import combat, pharah
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run

DATA = json.loads((Path(__file__).parents[2] /
                   'fixtures/behavior/pharah-barrage-target-time-v1.json').read_text())


class PharahBarrageTargetTimeTests(unittest.TestCase):
    def play(self, delayed):
        cfg = DATA['input']
        run = Run(KANEZAKA_DM, ('Pharah', 'A', 'B'))
        source, a, b = run.players
        original_npcs = tuple(bot.entity for bot in run.match.bots)
        source.commands.last_frame = cfg['owner_birth']
        self.assertTrue(run.match.switch_hero(source, heroes()[pharah.HERO]))
        run.match.collision_world = flat_world(0)
        for owner, position in ((source, cfg['source_position']),
                                (a, cfg['near_position']), (b, cfg['far_position'])):
            owner.mover.position = list(position)
            owner.mover.body = None
            owner.body_script.create_arrived = True
        birth = None
        for at in range(cfg['world_birth'] + 1, cfg['receipt_world_tick'] + 1):
            swapped = at > cfg['swap_after_world_tick']
            a.mover.position[:] = cfg['far_position'] if swapped else cfg['near_position']
            b.mover.position[:] = cfg['near_position'] if swapped else cfg['far_position']
            a.mover.body = b.mover.body = None
            run.match.update(at * combat.TICK, at)
            if at < cfg['fire_world_tick']:
                source.take_commands([Command(cfg['owner_birth'] + at - cfg['world_birth'])])
            if at == cfg['fire_world_tick'] - 1:
                script = source.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
            if at == cfg['fire_world_tick'] and not delayed:
                source.take_commands([Command(cfg['fire_owner_frame'], buttons=cfg['button'])])
                rocket, = run.match.combat.rockets
                birth = rocket.last_tick
        fight = run.match.combat
        if delayed:
            source.take_commands([Command(cfg['fire_owner_frame'], buttons=cfg['button'])])
            rocket, = fight.rockets
            birth = rocket.last_tick
        run.match.update(cfg['end_world_tick'] * combat.TICK, cfg['end_world_tick'])
        targets = {target.owner: target for target in fight._targets()}
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)
        self.assertEqual(tuple(a.mover.position), tuple(cfg['far_position']))
        self.assertEqual(tuple(b.mover.position), tuple(cfg['near_position']))
        return {'delayed': delayed, 'birth': birth,
                'health': [fight.vitals_of(targets[owner]).total for owner in (a, b)],
                'hits': source.client.messages(combat.HIT),
                'remaining': len(fight.rockets)}

    def test_delayed_barrage_preserves_physical_targets_and_full_hit_payloads(self):
        normal, late = self.play(False), self.play(True)
        self.observation = [normal, late]
        expected = DATA['expected']
        self.assertEqual(normal['health'], expected['health'])
        self.assertEqual(len(normal['hits']), expected['hit_count'])
        self.assertEqual([h['+0x78']['+0x4'] for h in normal['hits']],
                         expected['hit_pair'] * (expected['hit_count'] // 2))
        self.assertEqual(late['health'], normal['health'])
        self.assertEqual(late['hits'], normal['hits'])
        self.assertEqual(late['remaining'], normal['remaining'])
        self.assertEqual(normal['remaining'], expected['remaining_rockets'])
        self.assertEqual(normal['birth'], DATA['input']['fire_world_tick'])
        self.assertEqual(late['birth'], normal['birth'])
