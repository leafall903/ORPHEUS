"""Actual0263 lateral input row with declared state entry, not Wall Ride acceptance."""

import unittest

import test_lucio_combat as fixtures

from ow174.game.commands import Command
from ow174.game.mover import statescript_mods


class LucioThrottleScopeTests(unittest.TestCase):
    def scenario(self):
        _, player, _, _ = fixtures.LucioCombatTests().scenario()
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x0263)
        state = instance.state(1)
        self.assertFalse(state.active)
        state.begin()  # declared controller-owned scope; native producer remains open
        self.assertTrue(state.active)
        rows = statescript_mods(script.component)
        self.assertEqual(rows[(instance.id, 1, 0)][:3], (19, 2, 0.))
        frame = script.frame + 2
        player.mover.track_mods(rows, frame)
        return player, state, frame

    def pose(self, player, frame, *, right, forward=0):
        player.mover.velocity = (0., 0., 0.)  # isolate acceleration, not pre-existing momentum
        player.mover.step_pose(Command(frame, right=right, forward=forward), .016, frame)
        return player.mover.velocity

    def test_actual_lateral_zero_row_blocks_both_signs_but_preserves_forward_and_backward(self):
        for right in (-127, 127):
            for forward in (-127, 127):
                with self.subTest(right=right, forward=forward):
                    player, _, frame = self.scenario()
                    velocity = self.pose(player, frame+1, right=right, forward=forward)
                    self.assertEqual(velocity[0], 0.)
                    self.assertGreater(velocity[2]*forward, 0.)
                    self.assertEqual(player.mover.throttles, (right, forward))
                    self.assertEqual(player.mover.input_throttles, (right, forward))

    def test_scope_begins_on_next_pose_and_end_restores_lateral_on_next_pose(self):
        player, state, frame = self.scenario()
        before = self.pose(player, frame, right=127)
        self.assertLess(before[0], 0.)
        active = self.pose(player, frame+1, right=127)
        self.assertEqual(active[0], 0.)
        state.end(False)
        player.mover.track_mods(statescript_mods(state.instance.component), frame+1)
        restored = self.pose(player, frame+2, right=127)
        self.assertLess(restored[0], 0.)

    def test_lateral_restriction_does_not_erase_existing_horizontal_momentum(self):
        player, _, frame = self.scenario()
        player.mover.velocity = (2., 0., 0.)
        player.mover.step_pose(Command(frame+1, right=-127), .016, frame+1)
        self.assertGreater(player.mover.velocity[0], 0.)
        self.assertLessEqual(player.mover.velocity[0], 2.)


if __name__ == '__main__':
    unittest.main()
