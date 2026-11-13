"""Guardian Angel jump cancellation preserves the authored source velocity."""

import unittest
from unittest.mock import patch

import test_game_mercy_guardian as guardian


class MercyGuardianVelocityTests(unittest.TestCase):
    def test_jump_cancel_adds_vertical_velocity_without_erasing_forward_velocity(self):
        fixture = guardian.GuardianAngelTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        owner = fixture.mercy
        world = owner.body_script.world
        original = world.queue_velocity_changes
        observed = []

        def capture(frame, source, changes):
            if any(change[1] == -1 for change in changes):
                observed.append((tuple(owner.mover.velocity), changes))
            return original(frame, source, changes)

        with patch.object(world, "queue_velocity_changes", side_effect=capture):
            fixture.step(1, guardian.SHIFT)
            fixture.step(10, guardian.SHIFT)
            fixture.step(1, guardian.SHIFT | 0x40)
            fixture.step(1)
        self.assertEqual(len(observed), 1)
        before, changes = observed[0]
        requested = changes[0][3]
        self.assertGreater(before[2], 10)
        self.assertAlmostEqual(requested[0], before[0])
        self.assertAlmostEqual(requested[1], before[1] + 5)
        self.assertAlmostEqual(requested[2], before[2])
        self.assertGreater(owner.mover.velocity[2], 0)
        self.assertGreater(owner.mover.velocity[1], 0)
