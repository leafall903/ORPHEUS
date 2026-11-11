"""World-velocity SET phase ordering, tracked frame boundaries and scalar isolation."""

import unittest

from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.mover import ADD, SET, SPEED, Mover, modded, quantise


class WorldVelocityModTests(unittest.TestCase):
    def test_set_overrides_acceleration_and_gravity_before_swept_displacement(self):
        mover = Mover((0, 5, 0), world=flat_world(0))
        mover.velocity = (3, 20, 10)
        mover.track_mods({(1, 2, 0): (0, SET, (2.0, -1.0, 3.0), 0.0)}, 0)
        self.assertIs(modded(mover.data, list(mover.mods.values())), mover.data)
        mover.step(Command(1, forward=127), .016, 1)
        self.assertEqual(mover.velocity, (2, -1, 3))
        self.assertEqual(mover.position, [quantise(.032), quantise(4.984), quantise(.048)])

    def test_start_and_end_frames_apply_once_without_zeroing_the_speed_scalar(self):
        mover = Mover((0, 1, 0))
        vector_key, scalar_key = (1, 2, 0), (1, 2, 1)
        scalar = (SPEED, ADD, .5, 0.0)
        mover.track_mods({vector_key: (0, SET, (0.0, 0.0, 0.0), 0.0), scalar_key: scalar}, 0)
        mover.step(Command(0, forward=127), .016, 0)
        self.assertGreater(mover.velocity[2], 0)
        before = tuple(mover.position)
        mover.step(Command(1, forward=127), .016, 1)
        self.assertEqual(tuple(mover.position), before)
        self.assertEqual(mover.values.run_forward, mover.data.run_forward * 1.5)
        self.assertEqual(mover.velocity, (0, 0, 0))
        mover.track_mods({scalar_key: scalar}, 1)
        self.assertEqual(mover.mods[vector_key].end, 2)
        mover.step(Command(2, forward=127), .016, 2)
        self.assertGreater(mover.velocity[2], 0)
        self.assertGreater(mover.position[2], before[2])


if __name__ == "__main__":
    unittest.main()
