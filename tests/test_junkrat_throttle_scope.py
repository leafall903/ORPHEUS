"""Authored numeric-state consumers,not complete tire VM/possession acceptance.

Only each selected original MovementMod state is begun/ended explicitly. This
isolates modifier-to-mover timing from the still-open tire-controller lifecycle.
"""

import unittest
from unittest.mock import patch

from ow174.game import mover
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import graph, runtime


class JunkratThrottleScopeTests(unittest.TestCase):
    def scope(self, index, position):
        component = runtime.Component(0xE1000100)
        instance = component.create(graph.graph(index), init=False)
        state = instance.node_state(instance.graph.node(position))
        state.begin()
        self.assertTrue(state.active)
        return component, state

    def character(self):
        return mover.Mover((0, 0, 0), 0, flat_world(0), mover.mover_data(find_hero('Junkrat').body))

    def step_inputs(self, body, frame, *, right=127, forward=127):
        command = Command(frame, right=right, forward=forward)
        with patch.object(body, '_accelerate', wraps=body._accelerate) as accelerate:
            body.step(command, .016, frame)
        self.assertEqual(body.throttles, (right, forward))  # keep raw command animation state
        return accelerate.call_args.args[1:3]

    def test_original_0232_prep_rows_suppress_all_directions_then_restore(self):
        for right, forward in ((127, 127), (-127, -127), (127, -127)):
            with self.subTest(right=right, forward=forward):
                component, state = self.scope(0x232, 73)
                body = self.character()
                body.track_mods(mover.statescript_mods(component), 10)
                self.assertEqual(self.step_inputs(body, 10, right=right, forward=forward),
                                 tuple(mover.throttle_byte(v)[0] for v in (right, forward)))
                self.assertEqual(self.step_inputs(body, 11, right=right, forward=forward), (0, 0))
                state.end(False)
                body.track_mods(mover.statescript_mods(component), 11)
                self.assertEqual(self.step_inputs(body, 12, right=right, forward=forward),
                                 tuple(mover.throttle_byte(v)[0] for v in (right, forward)))

    def test_original_0233_drive_rows_force_forward_with_idle_or_reverse_input(self):
        for forward in (0, -127, 127):
            with self.subTest(forward=forward):
                component, state = self.scope(0x233, 14)
                body = self.character()
                body.track_mods(mover.statescript_mods(component), 0)
                self.assertEqual(self.step_inputs(body, 1, right=0, forward=forward), (0, 1))
                state.end(False)
                body.track_mods(mover.statescript_mods(component), 1)
                expected = mover.throttle_byte(forward)[0]
                self.assertEqual(self.step_inputs(body, 2, right=0, forward=forward), (0, expected))

    def test_original_0233_stopped_rows_clear_lateral_and_both_longitudinal_sides(self):
        component, state = self.scope(0x233, 23)
        body = self.character()
        body.track_mods(mover.statescript_mods(component), 0)
        self.assertEqual(self.step_inputs(body, 1, right=-127, forward=-127), (0, 0))
        state.end(False)
        body.track_mods(mover.statescript_mods(component), 1)
        self.assertEqual(self.step_inputs(body, 2, right=-127, forward=-127), (-1, -1))

    def test_unsigned_lateral_mod_restores_raw_lateral_sign_before_clamp(self):
        mods = [mover.Mod(17, mover.ADD, 2, 0), mover.Mod(18, mover.MULTIPLY, .5, 0),
                mover.Mod(19, mover.SET, .75, 0)]
        self.assertEqual(mover.modified_throttles(mods, -.25, -1), (-.75, 1))
        self.assertEqual(mover.modified_throttles(mods, .25, -1), (.75, 1))
