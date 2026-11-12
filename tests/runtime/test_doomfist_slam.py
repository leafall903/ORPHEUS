"""Run grounded Seismic Slam through real command input and static collision."""

import unittest
from unittest.mock import patch

from ow174.game import bodytrace, movelog
from ow174.game.collision import flat_world
from ow174.game.script import nodes
from tests.runtime import test_game_move_to as movement


class GroundedSeismicSlamTests(unittest.TestCase):
    def test_ground_press_enters_authored_launch_and_lands(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))
        harness = movement.FixedTargetMoveToTests()
        player, slam = harness.spawn(movement.DOOMFIST, 0x0B27, flat_world(1.0))
        cast = movement.START + 2
        harness.command(player, cast - 1)
        start = harness.command(player, cast, buttons=0x10)
        self.assertTrue(slam.state(0).flags & nodes.Ability.RUNNING)
        self.assertEqual(slam.vars[59].value(), 1.0)
        self.assertTrue(slam.state(1).active)
        self.assertEqual(start.position, (0.0, 1.0, 0.0))

        launch = harness.command(player, cast + 1)
        self.assertGreater(launch.velocity[1], 5.0)
        self.assertGreater(launch.velocity[2], 18.0)
        self.assertTrue(player.mover.airborne)
        self.assertGreater(launch.position[2], start.position[2])

        for frame in range(cast + 2, cast + 37):
            landing = harness.command(player, frame)
            if slam.state(58).active:
                break
        else:
            self.fail("Grounded Seismic Slam did not enter its impact phase")
        self.assertFalse(slam.state(1).active)
        self.assertEqual(slam.vars[59].value(), 3)
        self.assertAlmostEqual(landing.position[1], 1.0)
        self.assertGreater(landing.position[2], 7.0)
        self.assertLess(landing.position[2], 9.0)
