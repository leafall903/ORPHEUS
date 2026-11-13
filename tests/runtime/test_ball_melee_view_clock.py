"""Actual melee must damage and push the historically viewed entity once."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import ball, combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from test_game_combat import KANEZAKA_DM, Run, aim


class BallMeleeViewClockTests(unittest.TestCase):
    def test_actual_melee_preserves_viewed_identity_hit_frame_and_one_force(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/ball-melee-view-clock-v1.json').read_text())
        for owner_birth in fixture['ownerBirths']:
            with self.subTest(owner_birth=owner_birth):
                run = Run(KANEZAKA_DM, ('Ball', 'ViewedLeft', 'ViewedRight'))
                player, left_actor, right_actor = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                player.commands.last_frame = owner_birth
                run.match.switch_hero(player, heroes()[ball.HERO])
                birth = player.body_script.ball_birth
                self.assertEqual(birth, (fixture['worldBirth'], owner_birth))
                origin = tuple(player.mover.position)
                left = tuple(p + d for p, d in zip(origin, fixture['leftOffset'], strict=True))
                right = tuple(p + d for p, d in zip(origin, fixture['rightOffset'], strict=True))
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(player), (left[0], left[1] + 1, left[2]))
                events, submitted = [], []
                original_apply, original_submit = fight.knockbacks.apply, fight._submit_attack

                def apply(target, impulse, *, records=events, forward=original_apply, **kwargs):
                    accepted = forward(target, impulse, **kwargs)
                    records.append((target.entity, tuple(impulse), dict(kwargs), accepted))
                    return accepted

                def submit(shooter, frame, contact, *, source=player, records=submitted, forward=original_submit):
                    if shooter is source:
                        records.append((frame, contact.view_tick))
                    return forward(shooter, frame, contact)

                with patch.object(fight.knockbacks, 'apply', apply), patch.object(fight, '_submit_attack', submit):
                    for offset in range(1, 72):
                        swapped = offset > fixture['swapAfterOffset']
                        left_actor.mover.position[:] = right if swapped else left
                        right_actor.mover.position[:] = left if swapped else right
                        at = birth[0] + offset
                        run.match.update(at * .016, at)
                        player.take_commands([Command(owner_birth + offset, yaw=yaw, pitch=pitch,
                                                      buttons=0x800 if offset == fixture['attackOffset'] else 0,
                                                      extra=fixture['viewDelayMs'])])
                targets = {target.owner: target for target in fight._targets()}
                self.assertEqual(fight.vitals_of(targets[left_actor]).total, 170)
                self.assertEqual(fight.vitals_of(targets[right_actor]).total, 200)
                frame = owner_birth + fixture['attackOffset']
                self.assertEqual(submitted, [(frame, fixture['expectedWorldView'])])
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][0], left_actor.body)
                self.assertAlmostEqual(sum(axis*axis for axis in events[0][1]), 1)
                self.assertEqual(events[0][2]['impact_tick'], fixture['worldBirth'] + fixture['attackOffset'])
                self.assertEqual(events[0][2]['source_entity'], player.body)
                self.assertTrue(events[0][3])
                hits = player.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], left_actor.body)
                self.assertEqual(hits[0]['+0x84'], frame)
