"""Authored Sigma-referenced movement scopes on an explicit flat collision fixture.

Nodes are entered directly: this does not establish Flux target delivery or lift.
"""

import math
import unittest

from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data, statescript_mods
from ow174.game.script import graph, runtime


class SigmaMovementModTests(unittest.TestCase):
    def scope(self, resource, node):
        component = runtime.Component(0xA0000101)
        instance = component.create(graph.graph(resource), init=False)
        state = instance.node_state(instance.graph.node(node))
        self.assertIsNotNone(state)
        return component, instance, state

    @staticmethod
    def speed(mover):
        return math.hypot(mover.velocity[0], mover.velocity[2])

    def test_authored_flux_and_shared_lock_scopes_block_each_direction_then_restore(self):
        for resource, node in ((0x1DBC, 2), (0x224E, 32), (0x224E, 114), (0x224E, 168)):
            for right, forward in ((127, 0), (-127, 0), (0, 127), (0, -127)):
                with self.subTest(resource=resource, node=node, right=right, forward=forward):
                    component, _instance, state = self.scope(resource, node)
                    mover = Mover((0, 0, 0))
                    state.begin()
                    self.assertTrue(state.active)
                    mods = statescript_mods(component)
                    self.assertEqual({v[0] for v in mods.values()} & {17, 18, 19}, {17, 18, 19})
                    mover.track_mods(mods, 1000)
                    for frame in range(1001, 1011):
                        mover.step(Command(frame, right=right, forward=forward), 0.016, frame)
                        self.assertEqual(self.speed(mover), 0)
                    self.assertNotEqual(mover.throttles, (0, 0))  # raw command remains recorded
                    state.end(False)
                    self.assertFalse(state.active)
                    mover.track_mods(statescript_mods(component), 1010)
                    mover.step(Command(1011, right=right, forward=forward), 0.016, 1011)
                    self.assertGreater(self.speed(mover), 0)

    def test_lock_starts_and_ends_on_next_command_frame(self):
        component, _instance, state = self.scope(0x1DBC, 2)
        mover = Mover((0, 0, 0))
        state.begin()
        mover.track_mods(statescript_mods(component), 1000)
        mover.step(Command(1000, forward=127), 0.016, 1000)
        self.assertGreater(self.speed(mover), 0)
        # Remove inertia to isolate whether this frame still permits acceleration.
        mover.velocity = (0, 0, 0)
        mover.step(Command(1001, forward=127), 0.016, 1001)
        self.assertEqual(self.speed(mover), 0)
        state.end(False)
        mover.track_mods(statescript_mods(component), 1001)
        mover.step_pose(Command(1001, forward=127), 0.016, 1001)
        self.assertEqual(self.speed(mover), 0)
        mover.step(Command(1002, forward=127), 0.016, 1002)
        self.assertGreater(self.speed(mover), 0)

    def test_actual_dynamic_scope_scales_both_signs_and_reads_current_value(self):
        for right, forward in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(right=right, forward=forward):
                component, instance, state = self.scope(0x023A, 2)
                instance.write((0, 2101), 0.25)  # declared resource input, not inferred ownership
                state.begin()
                data = mover_data(None)
                mover, baseline = Mover((0, 0, 0), data=data), Mover((0, 0, 0), data=data)
                mover.track_mods(statescript_mods(component), 1000)
                for frame in range(1001, 1041):
                    command = Command(frame, right=right, forward=forward)
                    mover.step(command, 0.016, frame)
                    baseline.step(command, 0.016, frame)
                self.assertAlmostEqual(self.speed(mover) / self.speed(baseline), 0.25, delta=0.002)
                instance.write((0, 2101), 1.0)
                mover.track_mods(statescript_mods(component), 1040)
                for frame in range(1041, 1061):
                    mover.step(Command(frame, right=right, forward=forward), 0.016, frame)
                self.assertAlmostEqual(self.speed(mover), self.speed(baseline), delta=0.002)


if __name__ == "__main__":
    unittest.main()
