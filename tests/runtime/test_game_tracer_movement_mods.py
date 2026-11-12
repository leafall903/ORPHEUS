"""Tracer input under actual shared MovementMod resources.

Fixture activation is explicit, not proof of a native combat status trigger.
The graph nodes/parameters and player command/mover path are real.
"""

import math
import unittest

import test_game_tracer as tracer_tests

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.mover import statescript_mods
from ow174.game.script import expr, graph


class TracerMovementModTests(unittest.TestCase):
    def arena(self):
        run, player, _, _ = tracer_tests.TracerPistolTests().fight()
        run.step({})
        return run, player

    def scope(self, player, index=0x23A, position=2, factor=0.25):
        component = player.body_script.component
        resource = graph.graph(index)
        self.assertIsNotNone(resource)
        instance = component.create(resource, values={(expr.INSTANCE, 2101): factor})
        state = instance.state(resource.node(position).state)
        state.begin()
        self.assertTrue(state.active)
        return instance, state

    def command(self, run, player, *, right=0, forward=0, frame=None):
        run.frame = run.frame + 1 if frame is None else frame
        run.match.update(run.frame * combat.TICK, run.frame)
        player.take_commands([Command(run.frame, yaw=0, pitch=0, right=right, forward=forward)])

    def test_shared_023a_factor_restricts_all_four_tracer_input_axes_and_preserves_sign(self):
        for right, forward in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(right=right, forward=forward):
                run, player = self.arena()
                self.scope(player)
                for _ in range(20):
                    self.command(run, player, right=right, forward=forward)
                vx, _, vz = player.mover.velocity
                speed = math.hypot(vx, vz)
                expected = (player.mover.data.run_strafe if right else
                            player.mover.data.run_forward if forward > 0 else player.mover.data.run_backward)
                self.assertAlmostEqual(speed, expected * 0.25, delta=0.002)
                self.assertGreater((-vx if right > 0 else vx) if right else
                                   (vz if forward > 0 else -vz), 0)
                self.assertEqual(player.mover.throttles, (right, forward))
                self.assertFalse(run.match.combat.is_stunned(player))

    def test_actual_zero_restriction_does_not_accelerate_and_scope_end_restores_input(self):
        for index, position in ((0xCB, 2), (0x1CB, 11)):
            with self.subTest(graph=hex(index)):
                run, player = self.arena()
                _, state = self.scope(player, index, position)
                for _ in range(6):
                    self.command(run, player, right=-127, forward=127)
                self.assertEqual(player.mover.velocity, (0, 0, 0))
                self.assertEqual(player.mover.throttles, (-127, 127))
                state.end(False)
                # Posing reads last frame's mods before collecting graph changes.
                self.command(run, player, right=-127, forward=127)
                self.assertEqual(player.mover.velocity, (0, 0, 0))
                self.command(run, player, right=-127, forward=127)
                self.assertGreater(player.mover.velocity[0], 0)
                self.assertGreater(player.mover.velocity[2], 0)

    def test_restriction_begins_after_collection_and_duplicate_command_does_not_advance(self):
        run, player = self.arena()
        self.scope(player, factor=0)
        self.command(run, player, forward=127)
        self.assertGreater(player.mover.velocity[2], 0)
        # The first pose collects the resource; remove momentum observationally
        # to isolate whether the next command can add new acceleration.
        player.mover.velocity = (0, 0, 0)
        self.command(run, player, forward=127)
        self.assertEqual(player.mover.velocity, (0, 0, 0))
        before = player.mover.snapshot(run.frame)
        player.take_commands([Command(run.frame, right=127)])
        self.assertEqual(player.mover.snapshot(run.frame), before)

    def test_dynamic_factor_change_is_consumed_without_restarting_shared_state(self):
        run, player = self.arena()
        instance, state = self.scope(player, factor=0.25)
        for _ in range(20):
            self.command(run, player, forward=127)
        self.assertAlmostEqual(player.mover.velocity[2], 1.5, delta=0.002)
        counter = state.counter
        instance.write((expr.INSTANCE, 2101), 0.5)
        for _ in range(20):
            self.command(run, player, forward=127)
        self.assertAlmostEqual(player.mover.velocity[2], 3, delta=0.002)
        self.assertEqual(state.counter, counter)

    def test_shared_zero_scope_dominates_lower_priority_factor_then_releases(self):
        run, player = self.arena()
        self.scope(player, factor=0.25)
        _, lock = self.scope(player, 0xCB, 2)
        for _ in range(6):
            self.command(run, player, forward=127)
        self.assertEqual(player.mover.velocity, (0, 0, 0))
        active = statescript_mods(player.body_script.component)
        self.assertTrue(any(row[3] == 99 for row in active.values()))
        lock.end(False)
        for _ in range(20):
            self.command(run, player, forward=127)
        self.assertAlmostEqual(player.mover.velocity[2], 1.5, delta=0.002)

    def test_actual_00cb_wait_expires_without_manual_stop_and_restores_next_pose(self):
        run, player = self.arena()
        instance, lock = self.scope(player, 0xCB, 2)
        self.assertEqual(instance.start_entries((), player.body), 1)
        self.command(run, player, forward=127)
        self.assertTrue(instance.states[instance.graph.node(3).state].active)
        player.mover.velocity = (0, 0, 0)
        for _ in range(120):
            self.command(run, player, forward=127)
            self.assertEqual(player.mover.velocity, (0, 0, 0))
            if not lock.active:
                break
        else:
            self.fail("actual1.5s wait did not retire its movement subgraph")
        # The resource deadline can fall after the pose within this16ms frame.
        elapsed = player.body_script.component.now - lock.start
        self.assertGreaterEqual(elapsed, 1500)
        self.assertLess(elapsed, 1516)
        self.command(run, player, forward=127)
        self.assertGreater(player.mover.velocity[2], 0)
        self.assertIsNone(player.body_script.component.fault)

    def test_body_replacement_drops_old_resource_modifier_scope(self):
        run, player = self.arena()
        self.scope(player, factor=0)
        self.command(run, player, forward=127)
        self.command(run, player, forward=127)
        old_body, old_mover = player.body, player.mover
        from ow174.game.content import heroes

        self.assertTrue(run.match.switch_hero(player, heroes()[tracer_tests.TRACER]))
        self.assertNotEqual(player.body, old_body)
        self.assertIsNot(player.mover, old_mover)
        self.command(run, player, forward=127)
        self.assertGreater(player.mover.velocity[2], 0)
        self.assertFalse(any(mod.index in (17, 18, 19) for mod in player.mover.mods.values()))


if __name__ == "__main__":
    unittest.main()
