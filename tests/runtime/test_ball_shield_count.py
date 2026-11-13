"""The actual E graph count and its tagged pool use one existing capsule query."""
import unittest

import test_delivered_wrecking_ball_combat as scenarios

from ow174.game import ball


class ShieldCountTests(unittest.TestCase):
    def test_actual_e_count_matches_pool_at_capsule_boundary(self):
        for distance, count, health in ((8.25, 1, 775), (8.75, 0, 700)):
            with self.subTest(distance=distance):
                run, player, _, *_ = scenarios.BallCombatTests().scenario(distance=distance)
                run.step({player: (0, 0, 16)})
                shield = ball.instance(player, 0x1566)
                self.assertEqual(shield.vars[11398].value(), count)
                self.assertEqual(run.match.combat.vitals[player.body].total, health)
