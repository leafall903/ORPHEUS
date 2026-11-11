"""Real1514 inputs/rays; seed identities, uniform mode and eye origins are reconstructed."""

import math
import unittest
from unittest.mock import patch

import test_wrecking_ball_combat as ball_tests
from test_game_combat import aim

from ow174.game import ball, combat
from ow174.game.commands import Command
from ow174.game.script import expr


class BallSpreadTests(unittest.TestCase):
    def fire(self, *, distance=6, miss=False, shots=80):
        run, player, _, target, _ = ball_tests.BallCombatTests().scenario(distance=distance)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        angles = (-32768, 0) if miss else aim(
            fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        samples = []
        original = ball.cannon_direction

        def observe(shooter, state, forward, number, time, activation):
            vector = original(shooter, state, forward, number, time, activation)
            if shooter is player:
                self.assertEqual(vector, original(shooter, state, forward, number, time, activation))
                samples.append((number, state.spread_at(time - state.start - state.offset), vector,
                                forward, activation))
            return vector

        with patch.object(ball, 'cannon_direction', side_effect=observe):
            for _ in range(250):
                run.step({player: (*angles, 1)})
                player.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                if len(samples) >= shots:
                    break
        self.assertEqual(len(samples), shots)
        weapon = ball.instance(player, 0x1514)
        self.assertEqual(weapon.vars[53].value(), 80 - shots)
        return run, player, target, vitals, samples

    def test_actual_twenty_shot_ramp_changes_rays_and_duplicate_inputs_spend_no_extra_ammo(self):
        run, player, _, _, samples = self.fire(miss=True)
        self.assertEqual([number for number, *_ in samples], list(range(80)))
        for number, normalized, vector, forward, _ in samples:
            self.assertEqual(normalized, min(1, expr.f32((number + 1) / 20)))
            self.assertNotEqual(vector, forward)
            self.assertGreaterEqual(sum(a*b for a, b in zip(vector, forward, strict=True)),
                                    math.cos(math.radians(2.001)))
        self.assertEqual(len({activation for *_, activation in samples}), 1)
        self.assertGreater(len({vector for _, _, vector, _, _ in samples}), 70)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertEqual(ball.instance(player, 0x1514).vars[231].value(), 1)
        # The empty magazine still reaches the authored automatic reload path.
        for _ in range(150):
            run.step({})
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 80)

    def test_release_recovers_spread_and_new_activation_starts_a_fresh_ramp(self):
        run, player, _, _, samples = self.fire(miss=True, shots=12)
        old_activation = samples[-1][-1]
        weapon = ball.instance(player, 0x1514)
        run.step({})
        self.assertGreater(weapon.vars[231].value(), 0)
        for _ in range(100):
            run.step({})
        self.assertEqual(weapon.vars[231].value(), 0)
        run.step({player: (-32768, 0, 1)})
        volley = weapon.states[9]
        self.assertNotEqual((volley.counter, volley.start), old_activation)
        self.assertEqual(volley.spread0, 0)
        self.assertEqual(volley.spread_ramp, 20)
        self.assertEqual(volley.output(2), expr.f32(1 / 20))

    def test_near_enemy_takes_real_damage_and_far_crosshair_is_not_a_guaranteed_hit(self):
        _, player, _, health, _ = self.fire(shots=5)
        self.assertEqual(health.total, 175)
        self.assertEqual(len(player.client.messages(combat.HIT)), 5)
        _, player, _, health, _ = self.fire(distance=80)
        count = len(player.client.messages(combat.HIT))
        self.assertGreater(count, 0)
        self.assertLess(count, 80)
        self.assertGreater(health.total, 0)  # misses are not explained by an early corpse
