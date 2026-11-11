"""Actual Pharah cooldown input retains press age; no gates/resources are forced."""
import math
import unittest

import test_pharah_combat as primary

from ow174.game import combat


class PharahInputBufferTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    skills = (("Jump Jet", 0x27, 8), ("Concussive Blast", 0x7B, 16))

    def start(self, graph_id, button):
        run, player, enemy = self.encounter()
        enemy.mover.position[0] = 30
        enemy.mover.body = None
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == graph_id)
        ability = next(s for s in instance.states.values() if s.node.cls == 'STUStatescriptStateAbility')
        run.step({player: (0, 0, button)})
        for _ in range(30):
            run.step({})  # Concussion starts cooldown after its authored windup.
        duration = ability.remaining()
        self.assertGreater(duration, 8)
        return run, player, instance, ability, duration

    def wait_ready(self, run, ability):
        for _ in range(math.ceil(ability.remaining() / combat.TICK) + 3):
            run.step({})
        self.assertEqual(ability.remaining(), 0)

    def assert_cast(self, run, player, instance, ability, duration, button):
        for _ in range(3 if button == 8 else 25):
            run.step({})
        self.assertGreater(ability.remaining(), duration - 0.15)
        if button == 8:
            self.assertGreater(player.mover.velocity[1], 17)
        else:
            self.assertEqual(instance.states[0].counter, 2)

    def test_early_short_tap_keeps_original_timestamp_and_expires(self):
        for name, graph_id, button in self.skills:
            with self.subTest(skill=name):
                run, player, _, ability, _ = self.start(graph_id, button)
                for _ in range(40):
                    run.step({})
                run.step({player: (0, 0, button)})
                press = next(item for item in ability.presses if item[1] == ability.buttons()[0])
                for _ in range(6):
                    run.step({})
                    self.assertIn(press, ability.presses)
                    self.assertEqual(next(item[0] for item in ability.presses
                                          if item[1] == press[1]), press[0])
                self.wait_ready(run, ability)
                for _ in range(8):
                    run.step({})
                self.assertEqual(ability.remaining(), 0)
                if button == 8:
                    self.assertEqual(player.mover.velocity[1], 0)

    def test_short_tap_near_expiry_buffers_the_second_actual_cast(self):
        for name, graph_id, button in self.skills:
            with self.subTest(skill=name):
                run, player, instance, ability, duration = self.start(graph_id, button)
                while ability.remaining() > 0.08:
                    run.step({})
                run.step({player: (0, 0, button)})
                for _ in range(8):
                    run.step({})
                self.assert_cast(run, player, instance, ability, duration, button)

    def test_held_input_older_than_buffer_can_start_when_ready(self):
        for name, graph_id, button in self.skills:
            with self.subTest(skill=name):
                run, player, instance, ability, duration = self.start(graph_id, button)
                for _ in range(40):
                    run.step({})
                remaining = ability.remaining()
                for _ in range(math.ceil(remaining / combat.TICK) + 2):
                    run.step({player: (0, 0, button)})
                self.assert_cast(run, player, instance, ability, duration, button)

    def test_fresh_press_after_expiry_starts_after_old_tap_has_expired(self):
        for name, graph_id, button in self.skills:
            with self.subTest(skill=name):
                run, player, instance, ability, duration = self.start(graph_id, button)
                for _ in range(40):
                    run.step({})
                run.step({player: (0, 0, button)})
                run.step({})
                self.wait_ready(run, ability)
                run.step({player: (0, 0, button)})
                self.assert_cast(run, player, instance, ability, duration, button)
