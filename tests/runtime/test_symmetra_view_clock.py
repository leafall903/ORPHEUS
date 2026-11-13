"""Actual beam/melee input against exchanged target identities; no native claim."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, symmetra
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/symmetra-view-clock-v1.json'


def exercise(mode, birth):
    data = json.loads(FIXTURE.read_text())
    inputs = data['input']
    run = Run(names=('Symmetra', 'TargetA', 'TargetB'))
    owner, a, b = run.players
    original_npcs = tuple(bot.entity for bot in run.match.bots)
    assert len(original_npcs) == 15
    owner.commands.last_frame = birth
    run.match.switch_hero(owner, heroes()[symmetra.HERO])
    assert owner.body_script.symmetra_birth == (inputs['world_birth'], birth)
    run.match.collision_world = flat_world(0)
    pos_a, pos_b = inputs[mode]['a'], inputs[mode]['b']
    for actor, position in ((owner, (1000, 0, 1000)), (a, pos_a), (b, pos_b)):
        actor.mover.position = list(position)
        actor.mover.body = None
        actor.body_script.create_arrived = True
    start = run.match.tick
    for offset in range(1, inputs['shot_offset'] + 1):
        swapped = offset > inputs['swap_after']
        a.mover.position[:] = pos_b if swapped else pos_a
        b.mover.position[:] = pos_a if swapped else pos_b
        tick = start + offset
        run.match.update(tick * .016, tick)
        if offset < inputs['shot_offset']:
            owner.take_commands([Command(birth + offset)])
    fight = run.match.combat
    targets = {target.owner: target for target in fight._targets()}
    pools = [fight.vitals_of(targets[actor]) for actor in (a, b)]
    before = [pool.total for pool in pools]
    yaw, pitch = aim(fight._eye(owner), (pos_a[0], 1.1, pos_a[2]))
    queries = []
    seen = fight.seen

    def observe(target, view_tick):
        if target.owner in (a, b):
            queries.append({'target': target.entity, 'query': view_tick})
        return seen(target, view_tick)

    frame = birth + inputs['shot_offset']
    anchor = owner.body_script.world_frame_anchor
    with patch.object(fight, 'seen', observe):
        owner.take_commands([
            Command(frame, yaw=yaw, pitch=pitch, buttons=inputs[mode]['buttons'], extra=inputs['delay_ms']),
            Command(frame+1, yaw=yaw, pitch=pitch, extra=inputs['delay_ms']),
        ])
        for offset in range(inputs['shot_offset']+1, inputs['shot_offset']+10):
            tick = start + offset
            run.match.update(tick*.016, tick)
    hits = owner.client.messages(combat.HIT)
    assert tuple(bot.entity for bot in run.match.bots) == original_npcs
    return {'mode': mode, 'ownerBirth': birth, 'worldBirth': start, 'anchorBefore': anchor,
            'npcCountBefore': len(original_npcs), 'npcCountAfter': len(run.match.bots),
            'A': a.body, 'B': b.body, 'beforeHealth': before,
            'afterHealth': [pool.total for pool in pools], 'queries': queries,
            'hits': [{'target': hit['+0x78']['+0x0']['+0x0'], 'ownerFrame': hit['+0x84']} for hit in hits],
            'ownerFrameExpected': frame, 'expectedQuery': 1033}


class SymmetraViewClockTests(unittest.TestCase):
    def test_actual_beam_and_melee_keep_target_identity_and_owner_hit_frames(self):
        data = json.loads(FIXTURE.read_text())
        for mode in ('beam', 'melee'):
            for birth in data['input']['owner_births']:
                with self.subTest(mode=mode, owner_birth=birth):
                    row = exercise(mode, birth)
                    self.assertTrue(row['queries'])
                    self.assertEqual({entry['query'] for entry in row['queries']}, {1033.0})
                    expected = data['expected'][mode]
                    self.assertAlmostEqual(row['afterHealth'][0], expected['health_a'])
                    self.assertEqual(row['afterHealth'][1], expected['health_b'])
                    self.assertEqual(row['hits'], [
                        {'target': row['A'], 'ownerFrame': row['ownerFrameExpected']},
                    ])
