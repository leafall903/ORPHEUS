"""Actual Lucio ability buffers keep event age across cooldown/resource rejection.

Commands run the real body VM; this isolates the authored 150ms input window
from combat target movement, world-time aura delivery and client prediction.
"""
import math
import unittest

import test_lucio_combat as fixtures

from ow174.game.script import nodes


class LucioInputBufferTests(unittest.TestCase):
    CASES = ((0x0776, 20, 11, 0x02), (0x0277, 0, 2, 0x10))

    def scenario(self, graph_index, ability_index, effect_index):
        _, player, _, _ = fixtures.LucioCombatTests().scenario()
        script = player.body_script
        script.ult_seconds = 0
        instance = next(i for i in script.component.instances.values() if i.graph.index == graph_index)
        ability = instance.state(ability_index)
        self.assertIsInstance(ability, nodes.Ability)
        self.assertEqual(ability.evaluate(ability.node.field("m_246438AD")), .15000000596046448)
        return script, instance, ability, instance.state(effect_index)

    @staticmethod
    def step(script, buttons=0):
        script.command(script.frame + 1, buttons)

    def advance(self, script, count, buttons=0):
        for _ in range(count):
            self.step(script, buttons)

    def cast_then_cool(self, script, ability, effect, button):
        self.step(script, button)
        self.assertEqual(effect.counter, 1)
        for _ in range(400):
            self.step(script)
            if not ability.flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), .2)

    def test_early_released_soundwave_and_amp_presses_expire_before_cooldown_end(self):
        for graph, ability_id, effect_id, button in self.CASES:
            with self.subTest(graph=graph):
                script, _, ability, effect = self.scenario(graph, ability_id, effect_id)
                self.cast_then_cool(script, ability, effect, button)
                self.step(script, button)
                self.step(script)
                self.advance(script, math.ceil(ability.remaining() / .016) + 3)
                self.assertEqual(ability.remaining(), 0.)
                self.assertEqual(effect.counter, 1)
                self.step(script, button)
                self.assertEqual(effect.counter, 2)

    def test_rejected_press_retains_original_timestamp_instead_of_refreshing_it(self):
        for graph, ability_id, effect_id, button in self.CASES:
            with self.subTest(graph=graph):
                script, _, ability, effect = self.scenario(graph, ability_id, effect_id)
                self.cast_then_cool(script, ability, effect, button)
                self.step(script, button)
                original = next(time for time, which, _ in ability.presses if which == ability.buttons()[0])
                self.advance(script, 4)
                self.assertTrue(any(time == original and which == ability.buttons()[0]
                                    for time, which, _ in ability.presses))
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_short_released_press_near_expiry_still_buffers_and_is_consumed_once(self):
        for graph, ability_id, effect_id, button in self.CASES:
            with self.subTest(graph=graph):
                script, _, ability, effect = self.scenario(graph, ability_id, effect_id)
                self.cast_then_cool(script, ability, effect, button)
                while ability.remaining() > .08:
                    self.step(script)
                self.step(script, button)
                self.step(script)
                self.advance(script, math.ceil(ability.remaining() / .016) + 3)
                self.assertEqual(effect.counter, 2)
                self.assertFalse(ability.presses)

    def test_held_input_can_start_at_cooldown_end_without_rewriting_press_age(self):
        for graph, ability_id, effect_id, button in self.CASES:
            with self.subTest(graph=graph):
                script, _, ability, effect = self.scenario(graph, ability_id, effect_id)
                self.cast_then_cool(script, ability, effect, button)
                self.step(script, button)
                self.advance(script, math.ceil(ability.remaining() / .016) + 3, button)
                self.assertEqual(effect.counter, 2)

    def test_stale_barrier_press_does_not_autocast_when_charge_arrives_later(self):
        script, instance, ability, _ = self.scenario(0x073A, 10, 0)
        self.step(script, 0x20)
        self.advance(script, 32)
        script.set_ult_charge(2940.)  # declared external resource update
        self.step(script)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 2940.)
        self.assertEqual(instance.state(0).counter, 0)
        self.step(script, 0x20)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0.)

    def test_barrier_press_inside_authored_window_can_buffer_a_resource_update(self):
        script, _, ability, _ = self.scenario(0x073A, 10, 0)
        self.step(script, 0x20)
        self.advance(script, 4)
        script.set_ult_charge(2940.)
        self.step(script)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0.)


if __name__ == '__main__':
    unittest.main()
