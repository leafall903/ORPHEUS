"""Original15 NPCs remain; A/B exchange identities at one aim point, no screen inference."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import ashe, combat
from ow174.game.commands import Command
from ow174.game.content import heroes

FIXTURE_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-view-clock-v1.json'
FIXTURE = json.loads(FIXTURE_PATH.read_text())


class AsheViewClockTests(unittest.TestCase):
    def test_fractional_world_view_is_not_truncated_to_an_integer(self):
        data = FIXTURE['fractional_case']
        script = SimpleNamespace(ashe_birth=tuple(data['birth']))
        command = Command(data['command_frame'], extra=data['view_delay_ms'])
        self.assertEqual(ashe.view_world_tick(script, command, data['quanta_us']), data['world_view'])

    def test_real_viper_hits_the_viewed_identity_in_a_distinct_owner_epoch(self):
        self.probe_rows = []
        for owner_birth in FIXTURE['input']['owner_births']:
            with self.subTest(owner_birth=owner_birth):
                run = Run(names=('Ashe', 'TargetA', 'TargetB'))
                owner, target_a, target_b = run.players
                self.assertEqual(len(run.match.bots), 15)
                owner.commands.last_frame = owner_birth
                run.match.switch_hero(owner, heroes()[0x02E0000000000200])
                origin = tuple(owner.mover.position)
                aimed = (origin[0]-3, origin[1], origin[2]+8)
                other = (origin[0]+3, origin[1], origin[2]+8)
                for offset in range(1, 42):
                    target_a.mover.position[:] = aimed if offset <= 33 else other
                    target_b.mover.position[:] = other if offset <= 33 else aimed
                    run.frame = 1000+offset
                    run.match.update(run.frame*.016, run.frame)
                    if offset < 41:
                        owner.take_commands([Command(owner_birth+offset)])
                fight = run.match.combat
                targets = {target.owner: target for target in fight._targets()}
                vitals_a, vitals_b = (fight.vitals_of(targets[player]) for player in (target_a, target_b))
                yaw, pitch = aim(fight._eye(owner), (aimed[0], aimed[1]+1.1, aimed[2]))
                forward = fight._submit_attack
                submitted = []
                def submit(shooter, frame, contact, *, source=owner, records=submitted, resolve=forward):
                    if shooter is source:
                        records.append((frame, contact.view_tick))
                    return resolve(shooter, frame, contact)
                frame = owner_birth+41
                with patch.object(fight, '_submit_attack', side_effect=submit):
                    owner.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=1, extra=128)])
                    owner.take_commands([Command(frame+1, yaw=yaw, pitch=pitch, extra=128)])
                    for offset in range(42, 46):
                        run.frame = 1000+offset
                        run.match.update(run.frame*.016, run.frame)
                hits = owner.client.messages(combat.HIT)
                self.probe_rows.append({'ownerBirth': owner_birth, 'submitted': submitted,
                                        'A': target_a.body, 'B': target_b.body,
                                        'health': (vitals_a.total, vitals_b.total),
                                        'hits': [(hit['+0x78']['+0x0']['+0x0'], hit['+0x84'])
                                                 for hit in hits],
                                        'npcCount': len(run.match.bots)})
                self.assertEqual(submitted, [(frame, FIXTURE['expected']['view_world_tick'])])
                self.assertEqual((vitals_a.total, vitals_b.total),
                                 (FIXTURE['expected']['health_A'], FIXTURE['expected']['health_B']))
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], target_a.body)
                self.assertEqual(hits[0]['+0x84'], frame)
                self.assertEqual(len(run.match.bots), 15)
