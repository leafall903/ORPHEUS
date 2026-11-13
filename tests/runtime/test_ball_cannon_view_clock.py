"""Distinct targets prove view-time identity; no native rendering acceptance."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import ball, combat
from ow174.game.commands import Command
from ow174.game.content import heroes

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/ball-cannon-view-clock-v1.json'


class BallCannonViewClockTests(unittest.TestCase):
    def test_viewed_left_target_identity_survives_different_owner_epoch(self):
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        inputs, expected = fixture['input'], fixture['expected']
        for owner_birth in inputs['owner_births']:
            with self.subTest(owner_birth=owner_birth):
                run = Run(KANEZAKA_DM, ('Ball', 'OriginallyLeft', 'OriginallyRight'))
                player, originally_left, originally_right = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                player.commands.last_frame = owner_birth
                run.match.switch_hero(player, heroes()[ball.HERO])
                self.assertEqual(player.body_script.ball_birth, (inputs['world_birth'], owner_birth))
                start = run.match.tick
                origin = tuple(player.mover.position)
                left = tuple(p + d for p, d in zip(origin, inputs['left_offset'], strict=True))
                right = tuple(p + d for p, d in zip(origin, inputs['right_offset'], strict=True))
                for offset in range(1, inputs['shot_offset'] + 1):
                    swapped = offset > inputs['swap_after_offset']
                    originally_left.mover.position[:] = right if swapped else left
                    originally_right.mover.position[:] = left if swapped else right
                    tick = start + offset
                    run.match.update(tick * .016, tick)
                    if offset < inputs['shot_offset']:
                        player.take_commands([Command(owner_birth + offset)])
                fight = run.match.combat
                targets = {t.owner: t for t in fight._targets()}
                left_vitals = fight.vitals_of(targets[originally_left])
                right_vitals = fight.vitals_of(targets[originally_right])
                yaw, pitch = aim(fight._eye(player), (left[0], left[1] + inputs['aim_height'], left[2]))
                frame = owner_birth + inputs['shot_offset']
                original = fight._submit_attack
                submitted = []

                def submit(shooter, at, payload, *, source=player, records=submitted, forward=original):
                    if shooter is source:
                        records.append((at, payload.view_tick))
                    return forward(shooter, at, payload)

                with patch.object(fight, '_submit_attack', submit):
                    player.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=1,
                                                  extra=inputs['view_delay_ms'])])
                    player.take_commands([Command(frame + 1, yaw=yaw, pitch=pitch,
                                                  extra=inputs['view_delay_ms'])])
                    for offset in range(inputs['shot_offset'] + 1, inputs['shot_offset'] + 10):
                        tick = start + offset
                        run.match.update(tick * .016, tick)
                self.assertEqual(submitted, [(frame, expected['view_world_tick'])])
                self.assertEqual(left_vitals.total, expected['originally_left_health'])
                self.assertEqual(right_vitals.total, expected['originally_right_health'])
                hits = player.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], originally_left.body)
                self.assertEqual(hits[0]['+0x84'], frame)

    def test_view_mapping_keeps_fractional_world_ticks(self):
        data = json.loads(FIXTURE.read_text(encoding='utf-8'))['fractional_case']
        script = SimpleNamespace(ball_birth=(data['world_birth'], data['owner_birth']),
                                 frame_ms=data['frame_ms'])
        command = Command(data['command_frame'], extra=data['view_delay_ms'])
        self.assertEqual(ball.view_world_tick(script, command, data['quanta_us']),
                         data['expected_world_view'])
