"""Damage policy with a controlled command ray; scatter is tested separately."""

import unittest
from unittest.mock import patch

import test_mccree_combat as melee_cases
from test_game_combat import aim

from ow174.game import combat


class McCreeGunTests(unittest.TestCase):
    def test_primary_critical_policy_and_explicit_fan_no_critical_with_falloff(self):
        for npc in (False, True):
            for button, base in ((1, 70), (2, 50)):
                for head in (False, True):
                    for distance in (10, 41):
                        with (self.subTest(npc=npc, button=button, head=head, distance=distance),
                              patch('ow174.game.mccree.fan_direction',
                                    side_effect=lambda shooter, state, forward, *args: forward)):
                            run, shooter, target = melee_cases.McCreeMeleeTests().arena(
                                npc=npc, distance=distance)
                            for other in run.players:
                                if other is not shooter and other is not target:
                                    other.mover.position[0] += 20
                            fight = run.match.combat
                            body = next(t for t in fight._targets() if t.owner is target)
                            health = fight.vitals_of(body)
                            before = health.total
                            y = body.height - 0.05 if head else 1.0
                            point = body.feet[0], body.feet[1] + y, body.feet[2]
                            yaw, pitch = aim(fight._eye(shooter), point)
                            for _ in range(50):
                                run.step({shooter: (yaw, pitch, button)})
                                if health.total < before:
                                    break
                            critical = head and button == 1
                            expected = base * (2 if critical else 1) * (0.3 if distance == 41 else 1)
                            self.assertAlmostEqual(before - health.total, expected, delta=0.01)
                            hits = shooter.client.messages(combat.HIT)
                            self.assertTrue(hits)
                            self.assertEqual(bool(hits[-1]['+0x78']['+0x8'] & combat.CRITICAL), critical)
