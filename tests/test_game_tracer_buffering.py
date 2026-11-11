"""Actual Tracer resource/cooldown gates retain the original early-input time."""

import unittest

import test_game_tracer as tracer_tests

from ow174.game import tracer


class TracerBufferTests(tracer_tests.TracerCommandFixture, unittest.TestCase):
    def recall_cooldown(self):
        self.wait(2)
        self.step(16)
        self.wait(81)
        self.assertFalse(tracer.recalling(self.player))
        instance = next(i for i in self.player.body_script.component.instances.values()
                        if i.graph.index == 0x1A)
        self.assertGreater(instance.vars[61].value(), 11)
        return instance, instance.state(instance.graph.node(4).state)

    def test_released_early_recall_press_expires_and_fresh_ready_press_still_works(self):
        instance, warmup = self.recall_cooldown()
        count = warmup.counter
        self.step(16)
        self.step()
        self.wait(790)
        self.assertEqual(warmup.counter, count)
        self.assertFalse(tracer.recalling(self.player))
        self.assertEqual(instance.vars[61].value(), 0)
        self.step(16)
        self.assertTrue(tracer.recalling(self.player))
        self.assertEqual(warmup.counter, count+1)

    def test_short_recall_press_near_cooldown_end_is_consumed_when_ready(self):
        instance, warmup = self.recall_cooldown()
        count = warmup.counter
        while instance.vars[61].value() > 0.1:
            self.step()
        self.step(16)
        self.step()
        self.wait(10)
        self.assertTrue(tracer.recalling(self.player))
        self.assertEqual(warmup.counter, count+1)

    def test_held_recall_input_waits_for_cooldown_and_starts_once(self):
        _, warmup = self.recall_cooldown()
        count = warmup.counter
        for _ in range(780):
            self.step(16)
        self.assertTrue(tracer.recalling(self.player))
        self.assertEqual(warmup.counter, count+1)

    def test_released_unfunded_pulse_press_does_not_launch_after_later_charge_fill(self):
        script = self.player.body_script
        script.ult_seconds = 0
        self.step(32)
        self.step()
        self.wait(30)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.wait(20)
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertFalse(self.run.match.combat.pulse_bombs.bombs)
        self.step(32)
        self.wait(12)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(self.run.match.combat.pulse_bombs.bombs)

    def test_recent_released_pulse_press_can_buffer_a_charge_gate(self):
        script = self.player.body_script
        script.ult_seconds = 0
        self.step(32)
        self.step()
        self.wait(4)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.wait(12)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(self.run.match.combat.pulse_bombs.bombs)
