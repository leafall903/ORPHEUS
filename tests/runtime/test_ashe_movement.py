"""Actual ADS modifier, physical movement and release recovery on a flat world."""

import math
import unittest

import test_ashe_combat as fixtures

from ow174.game import combat, mover
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import HEROES_PATH
from ow174.game.script import graph


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheMovementTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight

    def step(self, run, player, button, right, forward):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        player.take_commands([Command(run.frame, buttons=button, right=right, forward=forward)])

    def test_actual_ads_slows_all_input_directions_and_release_restores_speed(self):
        for right, forward in ((0, 127), (0, -127), (127, 0), (-127, 0), (127, 127)):
            with self.subTest(right=right, forward=forward):
                run, player, _enemy = self.fight(distance=100)
                world = flat_world(0)
                run.match.collision_world = world
                player.mover.use(world=world)
                speeds = []
                for button, modifier in ((0, 0), (2, -0.25), (0, 0)):
                    for _ in range(60):
                        self.step(run, player, button, right, forward)
                    active = mover.statescript_mods(player.body_script.component)
                    common = next(instance for instance in player.body_script.component.instances.values()
                                  if instance.graph.index == 0x1C7)
                    state = common.node_state(common.graph.node(152))
                    self.assertTrue(state.active)
                    self.assertEqual(active[(common.id, state.index, 0)],
                                     (mover.SPEED, mover.ADD, modifier, 0))
                    self.assertEqual(player.mover.values.run_forward,
                                     mover.f32(player.mover.data.run_forward * (1 + modifier)))
                    self.assertEqual(player.mover.throttles, (right, forward))
                    self.assertEqual(player.mover.input_throttles, (right, forward))
                    self.assertEqual(player.mover.history[-1].throttles, (right, forward))
                    self.assertEqual(player.mover.history[-1].input_throttles, (right, forward))
                    speeds.append(math.hypot(player.mover.velocity[0], player.mover.velocity[2]))
                    self.assertEqual(player.mover.history[-1].position, tuple(player.mover.position))
                self.assertGreater(speeds[0], 0)
                # Contact integration introduces sub-mm/s pose-dependent error;
                # the authored scalar and modified profile are checked exactly.
                self.assertAlmostEqual(speeds[1], speeds[0] * 0.75, delta=0.002)
                self.assertAlmostEqual(speeds[2], speeds[0], delta=0.002)


if __name__ == "__main__":
    unittest.main()
