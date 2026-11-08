"""Authored Pharah fuel/cooldowns with reconstructed force adapters in real Match."""

import unittest

import test_pharah_combat as primary


class PharahMovementTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter

    def step(self, run, player, count, buttons=0, yaw=0):
        for _ in range(count):
            run.step({player: (yaw, 0, buttons)})

    def instance(self, player, index):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == index)

    def test_jump_jet_applies_authored_impulse_and_moves_in_aim_direction(self):
        for yaw, expected in ((0, (0, 20, 10)), (16384, (10, 20, 0))):
            with self.subTest(yaw=yaw):
                run, player, _ = self.encounter()
                start = list(player.mover.position)
                self.step(run, player, 1, 8, yaw)
                for got, want in zip(player.mover.velocity, expected, strict=True):
                    self.assertAlmostEqual(got, want, places=5)
                self.step(run, player, 12, 0, yaw)
                self.assertGreater(player.mover.position[1], start[1] + 2)
                axis = 2 if yaw == 0 else 0
                self.assertGreater(player.mover.position[axis], start[axis] + 1)

    def test_jump_jet_cooldown_rejects_repeated_presses_then_allows_after_expiry(self):
        run, player, _ = self.encounter()
        jet = self.instance(player, 0x27)
        self.step(run, player, 1, 8)
        self.assertGreater(jet.vars[61].value(), 9.9)
        for _ in range(5):
            self.step(run, player, 1)
            self.step(run, player, 1, 8)
            self.assertLess(player.mover.velocity[1], 20)
        self.step(run, player, 625)
        self.assertEqual(jet.vars[61].value(), 0)
        self.step(run, player, 1, 8)
        self.assertEqual(player.mover.velocity[1], 20)
        self.assertGreater(jet.vars[61].value(), 9.9)

    def test_hover_secondary_consumes_fuel_lifts_and_release_recharges(self):
        run, player, _ = self.encounter()
        hover = self.instance(player, 0x25)
        y = player.mover.position[1]
        self.step(run, player, 60, 2)
        self.assertGreater(player.mover.position[1], y + 1)
        self.assertGreater(player.mover.velocity[1], 0)
        self.assertLessEqual(player.mover.velocity[1], 2.75)
        self.assertLess(hover.vars[87].value(), 60)
        self.step(run, player, 140)
        self.assertEqual(hover.vars[87].value(), hover.vars[971].value())
        self.assertAlmostEqual(player.mover.position[1], y, places=2)

    def test_jump_jet_accepts_a_fresh_press_in_authored_150ms_buffer(self):
        run, player, _ = self.encounter()
        jet = self.instance(player, 0x27)
        self.step(run, player, 1, 8)
        self.step(run, player, 619)
        self.assertLess(jet.vars[61].value(), 0.15)
        self.step(run, player, 1, 8)
        self.step(run, player, 10)
        self.assertGreater(jet.vars[61].value(), 9.8)
        self.assertGreater(player.mover.velocity[1], 18)

    def test_hover_jump_button_also_lifts_and_empty_fuel_cannot_keep_flying(self):
        run, player, _ = self.encounter()
        hover = self.instance(player, 0x25)
        y = player.mover.position[1]
        self.step(run, player, 90, 0x40)
        self.assertGreater(player.mover.position[1], y + 1)
        self.assertLess(hover.vars[87].value(), 50)
        self.step(run, player, 250, 0x40)
        # Fuel may briefly regenerate while the button remains held; continuous
        # thrust cannot continue indefinitely after the authored resource empties.
        self.assertLess(hover.vars[87].value(), 15)
        self.assertLess(player.mover.velocity[1], 0)

    def test_hover_does_not_replace_faster_jump_jet_impulse(self):
        run, player, _ = self.encounter()
        self.step(run, player, 1, 8)
        self.step(run, player, 3, 2)
        self.assertGreater(player.mover.velocity[1], 15)


if __name__ == "__main__":
    unittest.main()
