"""Actual numeric MovementMod scopes through the production VM and mover.

Explicit state entry is a fixture, not Assassin pin/AI or vector-motion acceptance.
"""

import unittest

import test_game_zarya_npcs as fixtures

from ow174.game.commands import Command
from ow174.game.mover import Mover, defined_mover_data, statescript_mods
from ow174.game.script import graph, runtime


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual NPC graphs and mover data")
class ResourceInputModTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def scope(self, index=0x1157, position=33):
        from ow174.game.collision import flat_world

        component = runtime.Component(0xC0001000, world=runtime.World(0x02E00000000001BB, 0))
        component.frame, component.now = 100, 1600
        instance = component.create(graph.graph(index), init=False)
        state = instance.state(instance.graph.node(position).state)
        state.begin()
        component.run_now()
        self.assertTrue(state.active)
        mover = Mover((0, 1, 0), 0, flat_world(1), defined_mover_data(0x0400000000001EBD))
        self.assertEqual(mover.data.run_forward, 14)
        return component, state, mover

    def pose(self, mover, frame, *, right=0, forward=0):
        mover.step(Command(frame, right=right, forward=forward), 0.016, frame)

    def test_assassin_scope_blocks_all_command_axes_but_keeps_raw_bytes(self):
        for right, forward in ((127, 0), (-127, 0), (0, 127), (0, -127), (127, 127)):
            with self.subTest(right=right, forward=forward):
                component, _, mover = self.scope()
                mover.track_mods(statescript_mods(component), 100)
                self.pose(mover, 101, right=right, forward=forward)
                self.assertEqual(mover.position, [0, 1, 0])
                self.assertEqual(mover.velocity, (0, 0, 0))
                self.assertEqual(mover.throttles, (right, forward))
                self.assertEqual(mover.input_throttles, (right, forward))

    def test_scope_publication_and_release_act_on_the_next_pose(self):
        component, state, mover = self.scope()
        mover.track_mods(statescript_mods(component), 100)
        # A publication at100 cannot change a pose already belonging to100.
        self.pose(mover, 100, forward=127)
        self.assertGreater(mover.velocity[2], 0)
        mover.position[:], mover.velocity = [0, 1, 0], (0, 0, 0)
        self.pose(mover, 101, forward=127)
        self.assertEqual(mover.velocity, (0, 0, 0))
        state.end(False)
        mover.track_mods(statescript_mods(component), 101)
        self.pose(mover, 102, forward=127)
        self.assertGreater(mover.velocity[2], 0)
        self.assertEqual(mover.throttles, (0, 127))

    def test_retired_scope_restores_each_axis(self):
        for right, forward in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(right=right, forward=forward):
                component, state, mover = self.scope()
                mover.track_mods(statescript_mods(component), 100)
                self.pose(mover, 101, right=right, forward=forward)
                state.end(False)
                mover.track_mods(statescript_mods(component), 101)
                self.pose(mover, 102, right=right, forward=forward)
                self.assertGreater(sum(abs(v) for v in (mover.velocity[0], mover.velocity[2])), 0)

    def test_common_npc_scope_restricts_back_and_side_without_inventing_forward_mod(self):
        for right, forward, blocked in ((127, 0, True), (-127, 0, True), (0, -127, True),
                                        (0, 127, False)):
            with self.subTest(right=right, forward=forward):
                component, _, mover = self.scope(0x0F68, 3)
                mover.track_mods(statescript_mods(component), 100)
                self.pose(mover, 101, right=right, forward=forward)
                moving = abs(mover.velocity[0]) + abs(mover.velocity[2])
                self.assertEqual(moving == 0, blocked)
