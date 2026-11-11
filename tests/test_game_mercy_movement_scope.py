"""Actual Res input across the authored cast movement scope and completion.

These establish the selected host ordering,not full native mover phase parity.
"""

import math
import unittest

import test_game_mercy_resurrect as resurrect

from ow174.game import combat
from ow174.game.commands import CROUCH, JUMP, Command


class MercyCastMovementTests(unittest.TestCase):
    def setUp(self):
        self.f = resurrect.ResurrectTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.step(1, resurrect.RESURRECT)

    def command(self, *, right=0, forward=0, buttons=0):
        f = self.f
        f.run.frame += 1
        f.match.update(f.run.frame * combat.TICK, f.run.frame)
        for player in f.run.players:
            player.take_commands([Command(f.run.frame, right=right, forward=forward, buttons=buttons)
                                  if player is f.mercy else Command(f.run.frame)])

    def check_direction(self, right, forward):
        f = self.f
        mover = f.mercy.mover
        speed = mover.data.run_strafe if right else (mover.data.run_forward if forward > 0
                                                   else mover.data.run_backward)
        for _ in range(20):
            self.command(right=right, forward=forward)
        self.assertTrue(f.fight.is_dead(f.ally))
        self.assertAlmostEqual(math.hypot(mover.velocity[0], mover.velocity[2]), speed * 0.25, delta=0.01)
        self.assertEqual(mover.throttles, (right, forward))
        self.assertEqual(mover.input_throttles, (right, forward))
        # Authored00CA/107 stacks +0.75 into23184;001C7/152 negates the sum.
        self.assertAlmostEqual(f.mercy.body_script.component.entity_var(23184).value(), 0.75)
        self.assertTrue(any(m.index == 0 and m.value == -0.75 for m in mover.mods.values()))
        for _ in range(120):
            self.command(right=right, forward=forward)
            if not f.fight.is_dead(f.ally):
                break
        self.assertFalse(f.fight.is_dead(f.ally))
        self.assertIsNone(f.mercy.body_script.component.entity_var(23184).value())
        self.assertFalse(any(m.index == 0 and m.value == -0.75 for m in mover.mods.values()))
        for _ in range(12):
            self.command(right=right, forward=forward)
        self.assertAlmostEqual(math.hypot(mover.velocity[0], mover.velocity[2]), speed, delta=0.01)
        self.assertGreater(f.cooldown(), 29)

    def test_forward_input_slows_during_cast_and_recovers_after_completion(self):
        self.check_direction(0, 127)

    def test_backward_input_slows_during_cast_and_recovers_after_completion(self):
        self.check_direction(0, -127)

    def test_left_input_slows_during_cast_and_recovers_after_completion(self):
        self.check_direction(-127, 0)

    def test_right_input_slows_during_cast_and_recovers_after_completion(self):
        self.check_direction(127, 0)

    def test_jump_and_crouch_locks_clear_by_first_command_after_actual_revive(self):
        mover = self.f.mercy.mover
        for _ in range(20):
            self.command(buttons=JUMP | CROUCH)
        self.assertEqual(mover.position[1], 1)
        self.assertFalse(mover.crouched)
        self.assertTrue(any(m.index == 20 and m.value == 0 for m in mover.mods.values()))
        self.assertTrue(any(m.index == 21 and m.value == 0 for m in mover.mods.values()))
        for _ in range(120):
            self.command()
            if not self.f.fight.is_dead(self.f.ally):
                break
        self.assertFalse(self.f.fight.is_dead(self.f.ally))
        # This host retains the jump/crouch scope on the revive command and
        # retires it on the next. Native phase parity remains open.
        self.assertTrue(any(m.index in (20, 21) for m in mover.mods.values()))
        self.command()
        self.assertFalse(any(m.index in (20, 21) for m in mover.mods.values()))
        self.command(buttons=JUMP)
        self.assertGreater(mover.velocity[1], 5)
        self.assertGreater(mover.position[1], 1)
