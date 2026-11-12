"""Actual Zarya bubbles interacting with Ana's shield lane and shared input buffer."""
import unittest

import test_game_ana_barrier as personal
import test_game_ana_projected_barrier as projected

from ow174.game.script import nodes


class AnaBarrierBufferTests(unittest.TestCase):
    def fight(self, other):
        if other:
            helper = projected.AnaProjectedBarrierTests()
            run, _ana, caster, target = helper.fight()
            helper.cast(run, caster, target)
            direction = helper.direction(run, caster, target)
        else:
            run, _ana, caster, _entry = personal.AnaPersonalBarrierTests().fight()
            direction = (0, 0)
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == (0x25C if other else 0x244))
        ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
        pet = instance.states[10 if other else 2]
        self.assertEqual(pet.counter, 1)
        window = ability.node.field('m_246438AD')
        if other:
            self.assertIsNone(window)
        else:
            self.assertEqual(window['m_value'], .15)
        button = 16 if other else 8
        for _ in range(150):
            run.step({caster: (*direction, 0)})
        self.assertGreater(ability.remaining(), 1)
        self.assertFalse(pet.active)
        return run, caster, direction, button, ability, pet

    @staticmethod
    def step(run, caster, direction, button=0):
        run.step({caster: (*direction, button)})

    def test_released_early_bubble_press_expires_then_fresh_press_starts(self):
        for other in (False, True):
            with self.subTest(projected=other):
                run, caster, direction, button, ability, pet = self.fight(other)
                self.step(run, caster, direction, button)
                self.step(run, caster, direction)
                for _ in range(900):
                    if ability.remaining() <= 0:
                        break
                    self.step(run, caster, direction)
                for _ in range(10):
                    self.step(run, caster, direction)
                self.assertEqual(pet.counter, 1)
                self.assertFalse(pet.active)
                self.assertEqual(ability.remaining(), 0)
                self.step(run, caster, direction, button)
                self.assertEqual(pet.counter, 2)
                self.assertTrue(pet.active)

    def test_bubble_press_timestamp_does_not_refresh_while_unready(self):
        for other in (False, True):
            with self.subTest(projected=other):
                run, caster, direction, button, ability, _pet = self.fight(other)
                self.step(run, caster, direction, button)
                original = tuple(ability.presses)
                self.assertTrue(original)
                for _ in range(4):
                    self.step(run, caster, direction)
                self.assertEqual(tuple(ability.presses), original)

    def test_near_expiry_short_press_obeys_each_bubbles_authored_window(self):
        for other in (False, True):
            with self.subTest(projected=other):
                run, caster, direction, button, ability, pet = self.fight(other)
                while ability.remaining() > .08:
                    self.step(run, caster, direction)
                self.assertGreater(ability.remaining(), 0)
                self.step(run, caster, direction, button)
                self.assertEqual(pet.counter, 1)
                for _ in range(20):
                    self.step(run, caster, direction)
                self.assertEqual(pet.counter, 1 if other else 2)
                self.assertEqual(pet.active, not other)
                if other:
                    self.step(run, caster, direction, button)
                    self.assertEqual(pet.counter, 2)
                    self.assertTrue(pet.active)

    def test_held_bubble_press_remains_usable_when_cooldown_finishes(self):
        for other in (False, True):
            with self.subTest(projected=other):
                run, caster, direction, button, _ability, pet = self.fight(other)
                for _ in range(900):
                    self.step(run, caster, direction, button)
                    if pet.counter == 2:
                        break
                self.assertEqual(pet.counter, 2)
                self.assertTrue(pet.active)


if __name__ == '__main__':
    unittest.main()
