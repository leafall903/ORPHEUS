"""Delayed original primary samples retained world poses consistently at impact."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, pharah
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run

FIXTURES = Path(os.environ.get('ORPHEUS_BEHAVIOR_FIXTURES',
                              Path(__file__).resolve().parents[2] / 'fixtures/behavior'))
DATA = json.loads((FIXTURES / 'pharah-primary-target-time-v1.json').read_text(encoding='utf-8'))


class PharahPrimaryTargetTimeTests(unittest.TestCase):
    def play(self, *, delayed, replace_b=False):
        inputs = DATA['input']
        run = Run(KANEZAKA_DM, ('Pharah', 'A', 'B'))
        source, a, b = run.players
        original_npcs = tuple(bot.entity for bot in run.match.bots)
        source.commands.last_frame = inputs['owner_birth']
        self.assertTrue(run.match.switch_hero(source, heroes()[pharah.HERO]))
        run.match.collision_world = flat_world(0)
        near, far = inputs['near_position'], inputs['far_position']
        for owner, position in ((source, inputs['source_position']), (a, near), (b, far)):
            owner.mover.position = list(position)
            owner.mover.body = None
            owner.body_script.create_arrived = True
        fight = run.match.combat
        events = []
        original = fight.knockbacks.apply

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            events.append((target.entity, accepted, kwargs))
            return accepted

        birth = None
        frame = DATA['expected']['shot_owner_frame']
        with patch.object(fight.knockbacks, 'apply', side_effect=apply):
            for at in range(inputs['world_birth'] + 1, inputs['receipt_world'] + 1):
                if replace_b and at == inputs['replacement_world']:
                    self.assertTrue(run.match.switch_hero(b, heroes()[pharah.HERO]))
                    b.body_script.create_arrived = True
                a.mover.position[:] = far if at > inputs['swap_after'] else near
                b.mover.position[:] = near if at > inputs['swap_after'] else far
                a.mover.body = b.mover.body = None
                run.match.update(at * combat.TICK, at)
                if at < inputs['shot_world']:
                    source.take_commands([Command(inputs['owner_birth'] + at - inputs['world_birth'])])
                elif at == inputs['shot_world'] and not delayed:
                    source.take_commands([Command(frame, buttons=1)])
                    rocket, = fight.rockets
                    birth = (rocket.shot.frame, rocket.last_tick)
            if delayed:
                source.take_commands([Command(frame, buttons=1)])
                rocket, = fight.rockets
                birth = (rocket.shot.frame, rocket.last_tick)
            at = inputs['receipt_world'] + 1
            run.match.update(at * combat.TICK, at)
        targets = {t.owner: t for t in fight._targets()}
        hits = source.client.messages(combat.HIT)
        row = {'delayed': delayed, 'replaceB': replace_b, 'birth': birth,
               'A': a.body, 'B': b.body,
               'hp': [fight.vitals_of(targets[p]).total for p in (a, b)],
               'hits': [(h['+0x78']['+0x0']['+0x0'], h['+0x78']['+0x4'], h['+0x84']) for h in hits],
               'acceptedForce': [(entity, kw) for entity, accepted, kw in events if accepted]}
        self.observations.append(row)
        expected = DATA['expected']
        self.assertEqual(birth, (frame, inputs['shot_world']))
        self.assertEqual(row['hp'], [expected['health_a'], expected['health_b']])
        self.assertEqual(row['hits'], [(a.body, amount, expected['existing_notice_frame'])
                                       for amount in expected['damage']])
        for entity, accepted, kwargs in events:
            if accepted and entity == a.body:
                self.assertEqual(kwargs['impact_tick'], expected['physical_impact_tick'])
                self.assertEqual(kwargs['source_entity'], source.body)
        accepted = [entity for entity, ok, _ in events if ok]
        self.assertEqual(accepted.count(a.body), expected['force_events_per_hit_body'])
        self.assertNotIn(b.body, accepted)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)
        self.assertEqual(len(original_npcs), expected['retained_original_npcs'])
        self.assertFalse(fight.rockets)
        if delayed:
            self.assertEqual(a.mover.position[0], far[0], 'history sampling must not rewind the live mover')

    def test_delayed_and_current_primary_hit_same_physical_target_once(self):
        self.observations = []
        for delayed in (False, True):
            with self.subTest(delayed=delayed):
                self.play(delayed=delayed)

    def test_future_replacement_body_cannot_receive_the_earlier_impact(self):
        self.observations = []
        self.play(delayed=True, replace_b=True)


if __name__ == '__main__':
    unittest.main()
