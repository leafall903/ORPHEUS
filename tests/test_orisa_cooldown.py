"""Actual Fortify commands exercise press age and held-input cooldown gates."""

import unittest

import test_orisa_combat as fixture


class OrisaCooldownTests(unittest.TestCase):
    @staticmethod
    def step(run, player, buttons=0):
        run.step({player: (0, 0, buttons)})

    def cast(self, *, release=True):
        run, player, _ = fixture.OrisaCombatTests().arena()
        self.step(run, player, 8)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xCF4)
        ability = instance.node_state(instance.graph.node(3))
        effect = instance.node_state(instance.graph.node(14))
        self.assertEqual(effect.counter, 1)
        if release:
            for _ in range(270):
                self.step(run, player)
            self.assertFalse(self.immune(player))
            self.assertGreater(ability.remaining(), 1)
        return run, player, ability, effect

    @staticmethod
    def immune(player):
        return bool(player.body_script.component.entity_var(9161).value())

    def ready(self, run, player, ability):
        for _ in range(2000):
            if ability.remaining() == 0:
                return
            self.step(run, player)
        self.fail("actual Fortify cooldown did not finish")

    def test_too_early_released_press_expires_and_fresh_ready_press_works(self):
        run, player, ability, effect = self.cast()
        self.step(run, player, 8)
        press = ability.presses[-1]
        for _ in range(20):
            self.step(run, player)
        self.assertIn(press, ability.presses)
        self.ready(run, player, ability)
        for _ in range(30):
            self.step(run, player)
        self.assertEqual(effect.counter, 1)
        self.assertFalse(self.immune(player))
        self.assertGreater(ability.now - press[0], 150)
        self.step(run, player, 8)
        self.assertEqual(effect.counter, 2)
        self.assertTrue(self.immune(player))

    def test_released_short_press_in_original_early_window_starts_when_ready(self):
        run, player, ability, effect = self.cast()
        window = ability.evaluate(ability.node.field("m_246438AD"), watch=False)
        self.assertAlmostEqual(window, 0.15)
        for _ in range(2000):
            if 0 < ability.remaining() < window / 2:
                break
            self.step(run, player)
        self.assertGreater(ability.remaining(), 0)
        self.step(run, player, 8)
        self.assertEqual(effect.counter, 1)
        for _ in range(10):
            self.step(run, player)
        self.assertEqual(effect.counter, 2)
        self.assertTrue(self.immune(player))

    def test_new_press_held_through_cooldown_starts_after_its_short_buffer_expires(self):
        run, player, ability, effect = self.cast()
        self.step(run, player, 8)
        stamp = ability.presses[-1][0]
        for _ in range(2000):
            self.step(run, player, 8)
            if effect.counter == 2:
                break
        self.assertEqual(effect.counter, 2)
        self.assertGreater(ability.now - stamp, 150)
        self.assertTrue(self.immune(player))

    def test_holding_original_cast_does_not_recast_until_released_and_pressed_again(self):
        run, player, ability, effect = self.cast(release=False)
        for _ in range(1400):
            self.step(run, player, 8)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(effect.counter, 1)
        self.assertFalse(self.immune(player))
        self.step(run, player)
        self.step(run, player, 8)
        self.assertEqual(effect.counter, 2)
        self.assertTrue(self.immune(player))


if __name__ == "__main__":
    unittest.main()
