"""Actual delayed E keeps the physical recipient and existing self push."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, pharah
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run

DATA = json.loads((Path(__file__).parents[2] /
                   'fixtures/behavior/pharah-concussive-target-time-v1.json').read_text())


class PharahConcussiveTargetTimeTests(unittest.TestCase):
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
        fight, events, birth = run.match.combat, [], None
        original = fight.knockbacks.apply

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            events.append({'target': target.entity, 'vector': list(impulse),
                           'accepted': accepted, **kwargs})
            return accepted

        with patch.object(fight.knockbacks, 'apply', side_effect=apply):
            for at in range(cfg['world_birth'] + 1, cfg['receipt_world'] + 1):
                swapped = at > cfg['swap_after']
                a.mover.position[:] = cfg['far_position'] if swapped else cfg['near_position']
                b.mover.position[:] = cfg['near_position'] if swapped else cfg['far_position']
                a.mover.body = b.mover.body = None
                run.match.update(at * combat.TICK, at)
                if at < cfg['press_world'] or not delayed:
                    source.take_commands([Command(cfg['owner_birth'] + at - cfg['world_birth'],
                                                   buttons=16 if at == cfg['press_world'] else 0)])
                if birth is None and fight.rockets:
                    rocket, = fight.rockets
                    birth = [rocket.shot.frame, rocket.last_tick]
            if delayed:
                source.take_commands([
                    Command(frame, buttons=16 if frame == cfg['press_owner'] else 0)
                    for frame in range(cfg['press_owner'],
                                       cfg['owner_birth'] + cfg['receipt_world'] - cfg['world_birth'] + 1)
                ])
                rocket, = fight.rockets
                birth = [rocket.shot.frame, rocket.last_tick]
            run.match.update(cfg['end_world'] * combat.TICK, cfg['end_world'])
        targets = {target.owner: target for target in fight._targets()}
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)
        self.assertGreater(a.mover.position[0], 1010)
        return {'delayed': delayed, 'A': a.body, 'B': b.body, 'source': source.body,
                'birth': birth, 'events': events, 'remaining': len(fight.rockets),
                'health': [fight.vitals_of(targets[owner]).total for owner in (a, b)],
                'hits': source.client.messages(combat.HIT)}

    def test_delayed_e_preserves_enemy_recipient_and_one_self_force(self):
        normal, late = self.play(False), self.play(True)
        self.observation = [normal, late]
        expected = DATA['expected']
        self.assertEqual(normal['birth'], [expected['launch_owner'], expected['launch_world']])
        self.assertEqual(late['birth'], normal['birth'])
        self.assertEqual(normal['health'], expected['health'])
        self.assertEqual(late['health'], normal['health'])
        self.assertEqual(normal['hits'], [])
        self.assertEqual(late['hits'], [])
        self.assertEqual(normal['remaining'], 0)
        self.assertEqual(late['remaining'], 0)
        self.assertEqual(len(normal['events']), 2)
        self.assertEqual([e['target'] for e in normal['events']], [normal['A'], normal['source']])
        self.assertEqual([e['vector'] for e in normal['events']],
                         [expected['enemy_force'], expected['self_force']])
        self.assertTrue(all(e['accepted'] and e['impact_tick'] == expected['impact_world']
                            and e['source_entity'] == normal['source'] for e in normal['events']))
        self.assertEqual(late['events'], normal['events'])
