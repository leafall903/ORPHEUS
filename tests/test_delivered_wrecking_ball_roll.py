"""Roll transform and discrete headshot policy through real Match input/shot execution.

Capsules remain the existing approximation; this does not accept sphere collision
or Roll/grapple movement. Ball's missing critical scalar is a labelled 2x reconstruction.
"""

import unittest

import test_delivered_wrecking_ball_combat as ball_tests
from test_game_combat import aim

from ow174.game import ball, combat
from ow174.game.commands import Command


class RollTests(unittest.TestCase):
    def test_roll_cooldown_rejects_rapid_toggle_edges_then_allows_ready_toggle(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        for tick in range(8):
            run.step({player: (0, 0, 8 if tick % 2 == 0 else 0)})
            self.assertTrue(ball.rolling(player))
        ability = ball.instance(player, 0x1203).states[2]
        self.assertGreater(ability.remaining(), 0.35)
        for _ in range(40):
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertTrue(ball.rolling(player))
        run.step({player: (0, 0, 8)})
        self.assertFalse(ball.rolling(player))

    def test_shift_toggles_form_and_held_shift_does_not_repeat(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        self.assertFalse(ball.rolling(player))
        for _ in range(40):
            run.step({player: (0, 0, 8)})
        self.assertTrue(ball.rolling(player))
        run.step({})
        run.step({player: (0, 0, 8)})
        self.assertFalse(ball.rolling(player))

    def test_primary_exits_roll_and_resumes_real_cannon_damage(self):
        run, player, _, target, _ = ball_tests.BallCombatTests().scenario()
        run.step({player: (0, 0, 8)})
        for _ in range(40):
            run.step({})
        self.assertTrue(ball.rolling(player))
        fight = run.match.combat
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        for _ in range(30):
            run.step({player: (*angles, 1)})
        self.assertFalse(ball.rolling(player))
        self.assertLess(fight.vitals_of(target).total, 200)

    def test_quad_cannon_head_damage_uses_reconstructed_two_times_body_damage(self):
        for height, expected, critical in ((1.0, 5, False), (1.5, 10, True)):
            with self.subTest(height=height):
                run, player, _, target, _ = ball_tests.BallCombatTests().scenario()
                fight = run.match.combat
                angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + height, target.feet[2]))
                run.step({player: (*angles, 1)})
                self.assertEqual(fight.vitals_of(target).total, 200 - expected)
                hit = player.client.messages(combat.HIT)[-1]['+0x78']
                self.assertEqual(bool(hit['+0x8'] & combat.CRITICAL), critical)

    def test_rolled_ball_rejects_head_critical_while_normal_form_accepts_it(self):
        for rolled, expected_damage in ((False, 35), (True, 15)):
            with self.subTest(rolled=rolled):
                run, victim, shooter, _, vitals = ball_tests.BallCombatTests().scenario()
                if rolled:
                    run.step({victim: (0, 0, 8)})
                for _ in range(3):
                    run.step({})
                x, y, z = victim.mover.position
                angles = aim(run.match.combat._eye(shooter), (x, y + combat.eye_height(victim.hero.body), z))
                run.step({shooter: (*angles, 1)})
                self.assertEqual(vitals.total, 600 - expected_damage)

    def test_rewind_uses_form_at_view_time_instead_of_current_rolling_form(self):
        run, victim, shooter, _, vitals = ball_tests.BallCombatTests().scenario()
        run.step({victim: (0, 0, 8)})
        for _ in range(2):
            run.step({})
        self.assertTrue(ball.rolling(victim))
        x, y, z = victim.mover.position
        angles = aim(run.match.combat._eye(shooter), (x, y + combat.eye_height(victim.hero.body), z))
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        shooter.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1, extra=64)])
        self.assertTrue(ball.rolling(victim))
        self.assertEqual(vitals.total, 565)  # normal-form head40 minus armour5 at the earlier view time


if __name__ == '__main__':
    unittest.main()
