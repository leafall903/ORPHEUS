"""Actual owned Fusion input must query target identities in world time."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_dva_delivery_created_body as created
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, dva
from ow174.game.commands import Command
from ow174.game.content import find_hero


class DvaFusionViewClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    def test_view_conversion_retains_fractional_world_time(self):
        actor = SimpleNamespace(hero=dva.HERO, body_script=SimpleNamespace(dva_birth=(1000, 9000)))
        state = SimpleNamespace(node=SimpleNamespace(pos=6), instance=SimpleNamespace(
            graph=SimpleNamespace(index=dva.FUSION_CANNONS), component=SimpleNamespace(frame_ms=15)))
        self.assertEqual(dva.fusion_view_tick(actor, state, Command(9041, extra=8), 15000),
                         1037.9375)

    def test_owned_fusion_uses_viewed_identity_and_preserves_owner_hit_frame(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/dva-fusion-view-clock-v1.json'
        contract = json.loads(path.read_text(encoding='utf-8'))
        data = contract['input']
        for owner_birth in data['owner_births']:
            with self.subTest(owner_birth=owner_birth):
                run = Run(KANEZAKA_DM, ('DVa', 'OriginallyLeft', 'OriginallyRight'))
                pilot, left_target, right_target = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                pilot.commands.last_frame = owner_birth
                self.assertTrue(run.match.switch_hero(pilot, find_hero('dva')))
                mech = pilot.controlled_mech
                self.assertEqual(mech.body_script.dva_birth, (1000, owner_birth))
                x, y, z = mech.position
                left, right = (x - 3, y, z + 4), (x + 3, y, z + 4)
                for offset in range(1, data['shot_offset'] + 1):
                    swapped = offset > data['swap_after_offset']
                    left_target.mover.position[:] = right if swapped else left
                    right_target.mover.position[:] = left if swapped else right
                    tick = 1000 + offset
                    run.match.update(tick * .016, tick)
                    if offset < data['shot_offset']:
                        pilot.take_commands([Command(owner_birth + offset)])
                fight = run.match.combat
                vitals = [fight.vitals_of(fight.player_target(p)) for p in (left_target, right_target)]
                before = [v.total for v in vitals]
                yaw, pitch = aim(fight._eye(mech), (left[0], left[1] + .75, left[2]))
                frame = owner_birth + data['shot_offset']
                submitted = []
                forward = fight._submit_attack

                def submit(shooter, at, payload, *, source=mech, records=submitted, original=forward):
                    if shooter is source:
                        records.append((at, payload.view_tick))
                    return original(shooter, at, payload)

                with patch.object(fight, '_submit_attack', submit):
                    pilot.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=1,
                                                 extra=data['view_delay_ms'])])
                    pilot.take_commands([Command(frame + 1, yaw=yaw, pitch=pitch,
                                                 extra=data['view_delay_ms'])])
                    for offset in range(data['shot_offset'] + 1, data['shot_offset'] + 10):
                        tick = 1000 + offset
                        run.match.update(tick * .016, tick)
                hits = pilot.client.messages(combat.HIT)
                observed = {'owner_birth': owner_birth, 'submitted': submitted,
                            'health_before': before, 'health_after': [v.total for v in vitals],
                            'hit_targets': [h['+0x78']['+0x0']['+0x0'] for h in hits],
                            'hit_frames': [h['+0x84'] for h in hits]}
                print(json.dumps(observed))
                self.assertTrue(submitted)
                self.assertTrue(all(at == frame and view == contract['expected']['world_view']
                                    for at, view in submitted), observed)
                self.assertLess(vitals[0].total, before[0], observed)
                self.assertEqual(vitals[1].total, before[1], observed)
                self.assertTrue(hits, observed)
                self.assertTrue(all(h['+0x78']['+0x0']['+0x0'] == left_target.body
                                    and h['+0x84'] == frame for h in hits), observed)
