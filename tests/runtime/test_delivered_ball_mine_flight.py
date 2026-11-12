"""Archived 01CA native flight checks, with original assertions unchanged."""
import math
import unittest
from unittest.mock import patch
from ow174.game import ball_mines
from ow174.game.script import expr

class NativeFlightTests(unittest.TestCase):
    def setUp(self):
        # Retain the archived emulator fixtures with their explicit primitive.
        # Production uses host RSQRTSS; original-instruction/host-bound parity
        # is separately qualified instead of relabeling these old outputs.
        primitive = patch.object(ball_mines.scalar_math, 'reciprocal_sqrt',
                                 side_effect=lambda v: expr.f32(1 / expr.f32(math.sqrt(v))))
        primitive.start()
        self.addCleanup(primitive.stop)

    def test_captured_sse_gravity_and_position_trajectory(self):
        # Original104319 D71A70 and D70454, no world/type service adapters.
        # These are captured output values, not a second implementation of
        # the kernel. In particular100 steps differ from binary64 arithmetic.
        expected = {
            1: (9.520000457763672, 4.15231990814209),
            10: (5.2000017166137695, 5.177600383758545),
            50: (-13.999994277954102, 2.208001136779785),
            100: (-37.99997329711914, -18.783985137939453),
        }
        position, velocity = (0, 4, 0), (0, 10, 0)
        for number in range(1, 101):
            velocity, _, position = ball_mines.flight_step(position, velocity, 30, 0.016)
            if number in expected:
                self.assertEqual((velocity[1], position[1]), expected[number])
        velocity, _, position = ball_mines.flight_step((1.125, 4.5, -7.25), (11.25, -3.125, -13.75), 0, .016)
        self.assertEqual(velocity, (11.25, -3.125, -13.75))
        self.assertEqual(position, (1.3050000667572021, 4.449999809265137, -7.46999979019165))

    def test_captured_distance_and_partial_budget_outputs(self):
        # Original5E2610 and D72C80 outputs under the characterized static
        # RSQRT primitive; hardware approximation qualification remains open.
        self.assertEqual(ball_mines.flight_distance((0, 0, 0), (3, 4, 0)), 4.999999523162842)
        self.assertEqual(ball_mines.flight_distance((5, 6, 7), (5, 6, 7)), 0)
        time = ball_mines.clip_flight((0, 0, 0), (0, 10, 0), (3, 4, 0), (0, 9, 0), .25, 500, 0, 0, .5)
        self.assertEqual(time, ((1.5, 2, 0), (0, 9.5, 0), .25, 2.499999761581421, True))
        reach = ball_mines.clip_flight((0, 0, 0), (0, 10, 0), (3, 4, 0), (0, 9, 0), 1, 1, .5, 0, .5)
        self.assertEqual(reach, (
            (.6000000238418579, .8000000715255737, 0), (0, 9.800000190734863, 0),
            .10000000894069672, 1, True,
        ))

    def test_exact_budget_boundary_and_disabled_time_limit(self):
        for lifetime in (-1, 0, ball_mines.FLIGHT_EPSILON / 2):
            with self.subTest(lifetime=lifetime):
                result = ball_mines.clip_flight(
                    (0, 0, 0), (1, 0, 0), (1, 0, 0), (1, 0, 0), lifetime, 1, 999, 0, 1,
                )
                self.assertFalse(result[-1])  # distance equals reach, not beyond
        result = ball_mines.clip_flight((1, 0, 0), (1, 0, 0), (2, 0, 0), (1, 0, 0), 0, 1, 999, 1, 1)
        self.assertEqual(result, ((1, 0, 0), (1, 0, 0), 0, 0, True))
        self.assertFalse(ball_mines.clip_flight(
            (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), 1, 0, 1, 0, 0,
        )[-1])  # zero movement at the exact time/reach boundary
        self.assertTrue(ball_mines.clip_flight(
            (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), 1, 0, 1, 0, .016,
        )[-1])
