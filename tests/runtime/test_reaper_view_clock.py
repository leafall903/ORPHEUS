"""Actual shotgun commands discriminate target identity across owner/world epochs."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, reaper
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim


class ReaperViewClockTests(unittest.TestCase):
    def test_swapped_target_identity_preserves_owner_hit_frame(self):
        fixture = Path(__file__).resolve().parents[2] / 'fixtures/behavior/reaper-view-clock-v1.json'
        data = json.loads(fixture.read_text(encoding='utf-8'))
        inputs, expected = data['input'], data['expected']
        rows = []
        for birth in inputs['owner_births']:
            run = Run(names=('Reaper', 'TargetA', 'TargetB'))
            player, a, b = run.players
            original_npcs = tuple(bot.entity for bot in run.match.bots)
            self.assertEqual(len(original_npcs), 15)
            player.commands.last_frame = birth
            run.match.switch_hero(player, heroes()[reaper.HERO])
            self.assertEqual(player.body_script.world_frame_anchor, (inputs['world_birth'], birth))
            run.match.collision_world = flat_world(0)
            pos_a, pos_b = inputs['target_a_position'], inputs['target_b_position']
            for owner, position in ((player, (1000, 0, 1000)), (a, pos_a), (b, pos_b)):
                owner.mover.position = list(position)
                owner.mover.body = None
                owner.body_script.create_arrived = True
            start = run.match.tick
            for offset in range(1, inputs['shot_offset'] + 1):
                swapped = offset > inputs['swap_after']
                a.mover.position[:] = pos_b if swapped else pos_a
                b.mover.position[:] = pos_a if swapped else pos_b
                tick = start + offset
                run.match.update(tick * combat.TICK, tick)
                if offset < inputs['shot_offset']:
                    player.take_commands([Command(birth + offset)])
            fight = run.match.combat
            targets = {target.owner: target for target in fight._targets()}
            pools = [fight.vitals_of(targets[owner]) for owner in (a, b)]
            yaw, pitch = aim(fight._eye(player), (pos_a[0], 1.1, pos_a[2]))
            submitted = []
            original = fight._submit_attack

            def submit(shooter, at, payload, *, player=player, submitted=submitted, original=original):
                if shooter is player:
                    submitted.append((at, payload.view_tick))
                return original(shooter, at, payload)

            frame = birth + inputs['shot_offset']
            with patch.object(fight, '_submit_attack', submit):
                player.take_commands([
                    Command(frame, yaw=yaw, pitch=pitch, buttons=1, extra=inputs['view_delay_ms']),
                    Command(frame + 1, yaw=yaw, pitch=pitch, extra=inputs['view_delay_ms']),
                ])
                for offset in range(inputs['shot_offset'] + 1, inputs['shot_offset'] + 10):
                    tick = start + offset
                    run.match.update(tick * combat.TICK, tick)
            hits = player.client.messages(combat.HIT)
            rows.append({
                'ownerBirth': birth, 'worldBirth': start,
                'A': a.body, 'B': b.body, 'health': [pool.total for pool in pools],
                'submitted': submitted,
                'hits': [{'target': hit['+0x78']['+0x0']['+0x0'], 'ownerFrame': hit['+0x84'],
                          'damage': hit['+0x78']['+0x4']} for hit in hits],
                'npcCountBefore': len(original_npcs), 'npcCountAfter': len(run.match.bots),
            })
            self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)
        if output := os.environ.get('REAPER_VIEW_CLOCK_EVIDENCE'):
            Path(output).write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
        for row in rows:
            with self.subTest(owner_birth=row['ownerBirth']):
                frame = row['ownerBirth'] + inputs['shot_offset']
                self.assertEqual(row['submitted'],
                                 [(frame, expected['world_view_tick'])] * expected['pellet_submissions'])
                self.assertLess(row['health'][0], 200)
                self.assertEqual(row['health'][1], expected['target_b_health'])
                self.assertGreater(len(row['hits']), 0)
                self.assertTrue(all(hit['target'] == row['A'] for hit in row['hits']))
                self.assertTrue(all(hit['ownerFrame'] == frame for hit in row['hits']))
                self.assertAlmostEqual(row['health'][0], 200 - sum(hit['damage'] for hit in row['hits']))
