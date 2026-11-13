"""Moving target identities discriminate owner and world epochs offline."""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.soldier_view_clock import view_world_tick
from tests.runtime.test_game_combat import Run, aim

FIXTURES = Path(os.environ.get(
    'ORPHEUS_BEHAVIOR_FIXTURES', Path(__file__).resolve().parents[2] / 'fixtures/behavior',
))
FIXTURE = FIXTURES / 'soldier-view-clock-v1.json'


class SoldierViewClockTests(unittest.TestCase):
    def test_swapped_target_identity_and_owner_hit_frame(self):
        data = json.loads(FIXTURE.read_text(encoding='utf-8'))
        inputs, expected = data['input'], data['expected']
        for birth in inputs['owner_births']:
            with self.subTest(owner_birth=birth):
                run = Run(names=('Shooter', 'TargetA', 'TargetB'))
                player, a, b = run.players
                original_npcs = tuple(bot.entity for bot in run.match.bots)
                self.assertEqual(len(original_npcs), 15)
                player.commands.last_frame = birth
                run.match.switch_hero(player, heroes()[SOLDIER])
                self.assertEqual(player.body_script.soldier_birth, (inputs['world_birth'], birth))
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
                    run.match.update(tick * .016, tick)
                    if offset < inputs['shot_offset']:
                        player.take_commands([Command(birth + offset)])
                fight = run.match.combat
                targets = {target.owner: target for target in fight._targets()}
                pool_a, pool_b = (fight.vitals_of(targets[owner]) for owner in (a, b))
                yaw, pitch = aim(fight._eye(player), (pos_a[0], 1.1, pos_a[2]))
                submitted = []
                original = fight._submit_attack

                def submit(shooter, at, payload, *, source=player, records=submitted, forward=original):
                    if shooter is source:
                        records.append((at, payload.view_tick))
                    return forward(shooter, at, payload)

                frame = birth + inputs['shot_offset']
                with patch.object(fight, '_submit_attack', submit):
                    # A single packet retains its preceding pose anchor for both commands.
                    player.take_commands([
                        Command(frame, yaw=yaw, pitch=pitch, buttons=1, extra=inputs['view_delay_ms']),
                        Command(frame + 1, yaw=yaw, pitch=pitch, extra=inputs['view_delay_ms']),
                    ])
                    for offset in range(inputs['shot_offset'] + 1, inputs['shot_offset'] + 10):
                        tick = start + offset
                        run.match.update(tick * .016, tick)
                self.assertEqual(submitted, [(frame, expected['world_view_tick'])])
                self.assertEqual((pool_a.total, pool_b.total), (expected['health_a'], expected['health_b']))
                hits = player.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], a.body)
                self.assertEqual(hits[0]['+0x84'], frame)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)

    def test_fractional_view_and_creation_fallback(self):
        case = json.loads(FIXTURE.read_text(encoding='utf-8'))['fractional']
        command = Command(case['command_frame'], extra=case['view_delay_ms'])
        for anchor in (None, tuple(case['anchor'])):
            with self.subTest(anchor=anchor):
                script = SimpleNamespace(world_frame_anchor=anchor, soldier_birth=tuple(case['anchor']),
                                         frame_ms=case['frame_ms'])
                self.assertEqual(view_world_tick(script, command, case['quanta']), case['expected'])
