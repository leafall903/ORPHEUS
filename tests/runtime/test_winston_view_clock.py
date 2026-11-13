"""Actual inputs with exchanged target identities, distinct owner/world epochs."""

import json
import unittest
from pathlib import Path
from unittest import mock

from test_game_combat import Run, aim

from ow174.game import combat, winston
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/winston-view-clock-v1.json'


def sample(owner_birth, attack):
    data = json.loads(FIXTURE.read_text(encoding='utf-8'))
    inputs = data['input']
    run = Run(names=('Winston', 'A', 'B'))
    owner, a, b = run.players
    npcs = tuple(bot.entity for bot in run.match.bots)
    owner.commands.last_frame = owner_birth
    run.match.switch_hero(owner, heroes()[winston.HERO])
    assert owner.body_script.winston_birth == (inputs['world_birth'], owner_birth)
    run.match.collision_world = flat_world(0)
    source = tuple(inputs['source'])
    pos_a = tuple(x + d for x, d in zip(source, inputs['a_offset'], strict=True))
    pos_b = tuple(x + d for x, d in zip(source, inputs['b_offset'], strict=True))
    for player, position in ((owner, source), (a, pos_a), (b, pos_b)):
        player.mover.position[:] = position
        player.mover.body = None
        player.body_script.create_arrived = True
    fight = run.match.combat
    queries = []
    submitted = []
    original_seen = fight.seen
    original_submit = fight._submit_attack

    def seen(target, view):
        if target.owner in (a, b):
            queries.append(view)
        return original_seen(target, view)

    def submit(shooter, frame, payload):
        if shooter is owner:
            submitted.append((frame, payload.view_tick))
        return original_submit(shooter, frame, payload)

    start = run.match.tick
    for offset in range(1, inputs['shot_offset']):
        a.mover.position[:] = pos_b if offset > inputs['swap_after'] else pos_a
        b.mover.position[:] = pos_a if offset > inputs['swap_after'] else pos_b
        run.match.update((start + offset) * .016, start + offset)
        owner.take_commands([Command(owner_birth + offset)])
    yaw, pitch = aim(fight._eye(owner), (pos_a[0], 1.1, pos_a[2]))
    frames = 1 if attack['kind'] == 'tesla' else 24
    with mock.patch.object(fight, 'seen', seen), mock.patch.object(fight, '_submit_attack', submit):
        for offset in range(inputs['shot_offset'], inputs['shot_offset'] + frames):
            a.mover.position[:] = pos_b
            b.mover.position[:] = pos_a
            run.match.update((start + offset) * .016, start + offset)
            owner.take_commands([Command(owner_birth + offset, yaw=yaw, pitch=pitch,
                                         buttons=attack['buttons'], extra=attack['view_delay_ms'])])
            if owner.client.messages(combat.HIT):
                break
    targets = {target.owner: target for target in fight._targets()}
    hits = owner.client.messages(combat.HIT)
    return {'kind': attack['kind'], 'owner_birth': owner_birth, 'query_world_ticks': sorted(set(queries)),
            'submitted': submitted, 'health_a': fight.vitals_of(targets[a]).total,
            'health_b': fight.vitals_of(targets[b]).total, 'A': a.body, 'B': b.body,
            'hit_targets': [hit['+0x78']['+0x0']['+0x0'] for hit in hits],
            'hit_owner_frames': [hit['+0x84'] for hit in hits],
            'npcs_before': len(npcs), 'npcs_after': len(run.match.bots),
            'npc_identities_retained': tuple(bot.entity for bot in run.match.bots) == npcs}


class WinstonViewClockTests(unittest.TestCase):
    def test_actual_primary_and_melee_keep_viewed_identity_and_owner_hit_frame(self):
        data = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for attack in data['attacks']:
            for birth in data['input']['owner_births']:
                with self.subTest(attack=attack['kind'], owner_birth=birth):
                    row = sample(birth, attack)
                    self.assertAlmostEqual(row['health_a'], attack['health_a'])
                    self.assertEqual(row['health_b'], data['expected']['health_b'])
                    self.assertEqual(row['hit_targets'], [row['A']])
                    self.assertEqual(row['npcs_after'], 15)
                    self.assertTrue(row['npc_identities_retained'])
                    frame, = row['hit_owner_frames']
                    command = Command(frame, extra=attack['view_delay_ms'])
                    expected = data['input']['world_birth'] + frame - birth - command.view_delay(16000) / .016
                    self.assertEqual(row['query_world_ticks'], [expected])
                    self.assertGreaterEqual(frame, birth + data['input']['shot_offset'])
                    if attack['kind'] == 'tesla':
                        self.assertEqual(frame, birth + 41)

    def test_fractional_view_mapping_does_not_round_to_creation_tick(self):
        from types import SimpleNamespace

        script = SimpleNamespace(winston_birth=(1000, 9000), frame_ms=16)
        command = Command(9041, extra=121)
        self.assertEqual(winston.view_world_tick(script, command, 16000), 1033.4375)
