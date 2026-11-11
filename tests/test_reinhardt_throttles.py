"""Authored Charge/Earthshatter throttle mods through real mover commands."""

import unittest

import test_game_reinhardt as fixtures

from ow174.game import combat
from ow174.game.commands import Command


class ReinhardtThrottleTests(unittest.TestCase):
    setUp = fixtures.HammerTests.setUp
    place = staticmethod(fixtures.HammerTests.place)

    def step(self, *, buttons=0, forward=0, right=0):
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        for player in self.run.players:
            player.take_commands([Command(self.run.frame, buttons=buttons if player is self.rein else 0,
                                           forward=forward if player is self.rein else 0,
                                           right=right if player is self.rein else 0)])

    def prepare(self):
        self.place(self.enemy, (100, 1, 0))
        self.place(self.other, (-100, 1, 0))

    def test_charge_windup_blocks_each_input_axis_but_keeps_raw_command_bytes(self):
        for forward, right in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(forward=forward, right=right):
                self.setUp()
                self.prepare()
                self.step(buttons=8)
                start = tuple(self.rein.mover.position)
                for _ in range(12):
                    self.step(forward=forward, right=right)
                self.assertEqual(tuple(self.rein.mover.position), start)
                self.assertEqual(self.rein.mover.throttles, (right, forward))
                self.assertEqual(self.rein.mover.input_throttles, (right, forward))

    def test_charge_drive_remains_forward_then_ordinary_input_recovers_after_end(self):
        self.prepare()
        self.step(buttons=8)
        for _ in range(48):
            self.step(forward=-127, right=127)
        self.assertAlmostEqual(self.rein.mover.velocity[0], 0, delta=.01)
        self.assertAlmostEqual(self.rein.mover.velocity[2], 16.5, delta=.01)
        for _ in range(240):
            self.step()
        self.place(self.rein, (0, 1, 0))
        for _ in range(12):
            self.step(forward=-127, right=127)
        self.assertLess(self.rein.mover.velocity[0], -1)
        self.assertLess(self.rein.mover.velocity[2], -1)

    def test_earthshatter_windup_blocks_forward_back_and_restores_after_channel(self):
        for forward in (127, -127):
            with self.subTest(forward=forward):
                self.setUp()
                self.prepare()
                self.rein.body_script.set_ult_charge(1540)
                self.step(buttons=32)
                start = tuple(self.rein.mover.position)
                for _ in range(12):
                    self.step(forward=forward)
                self.assertEqual(tuple(self.rein.mover.position), start)
                for _ in range(180):
                    self.step()
                for _ in range(12):
                    self.step(forward=forward)
                self.assertGreater(self.rein.mover.velocity[2] * forward, 127)

    def test_earthshatter_authored_lateral_multiply_zero_blocks_both_signs(self):
        for right in (127, -127):
            with self.subTest(right=right):
                self.setUp()
                self.prepare()
                self.rein.body_script.set_ult_charge(1540)
                self.step(buttons=32)
                start = tuple(self.rein.mover.position)
                for _ in range(12):
                    self.step(right=right)
                self.assertEqual(tuple(self.rein.mover.position), start)
                self.assertEqual(self.rein.mover.throttles, (right, 0))


if __name__ == '__main__':
    unittest.main()
