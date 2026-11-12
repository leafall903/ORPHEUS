"""Actual Baptiste graphs: cooldown press age, authored buffer and held recovery."""

import math
import unittest

import test_baptiste_combat as fixture

from ow174.game.script import nodes


class BaptisteCooldownBufferTests(unittest.TestCase):
    def begin(self, index, button):
        self.driver = fixture.BaptisteStateTests()
        body = self.driver.make_body()
        instance = self.driver.instance(body, index)
        ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
        self.driver.advance(body, 1, button)
        self.driver.advance(body, 20 if index == 0x43 else 60)
        tracker = instance.states[{0x19A5: 4, 0x1761: 12, 0x43: 4}[index]]
        self.assertGreater(ability.remaining(), 0)
        return body, ability, tracker

    def test_early_released_press_expires_and_fresh_ready_press_still_works(self):
        for index, button in ((0x19A5, 8), (0x1761, 16), (0x43, 0x800)):
            with self.subTest(graph=hex(index)):
                body, ability, tracker = self.begin(index, button)
                first = tracker.counter
                self.driver.advance(body, 1, button)
                self.driver.advance(body, math.ceil(ability.remaining() / .016) + 30)
                self.assertEqual(tracker.counter, first)
                self.assertEqual(ability.remaining(), 0)
                self.driver.advance(body, 20, button)
                self.assertGreater(tracker.counter, first)

    def test_unset_buffer_does_not_retain_a_near_expiry_short_press(self):
        for index, button in ((0x19A5, 8), (0x1761, 16)):
            with self.subTest(graph=hex(index)):
                body, ability, tracker = self.begin(index, button)
                self.assertIsNone(ability.node.field('m_246438AD'))
                while ability.remaining() > .08:
                    self.driver.advance(body, 1)
                first = tracker.counter
                self.driver.advance(body, 1, button)
                self.driver.advance(body, 30)
                self.assertEqual(tracker.counter, first)
                self.driver.advance(body, 20, button)
                self.assertGreater(tracker.counter, first)

    def test_actual_melee_near_expiry_buffer_and_held_inputs_start_when_ready(self):
        for index, button, held in ((0x43, 0x800, False), (0x43, 0x800, True),
                                    (0x19A5, 8, True), (0x1761, 16, True)):
            with self.subTest(graph=hex(index), held=held):
                body, ability, tracker = self.begin(index, button)
                if not held:
                    self.assertAlmostEqual(ability.evaluate(ability.node.field('m_246438AD')), .15)
                    while ability.remaining() > .08:
                        self.driver.advance(body, 1)
                first = tracker.counter
                self.driver.advance(body, 1, button)
                self.driver.advance(body, math.ceil(ability.remaining() / .016) + 20, button if held else 0)
                self.assertGreater(tracker.counter, first)


if __name__ == '__main__':
    unittest.main()
