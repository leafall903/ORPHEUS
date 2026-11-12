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

    def test_jump_jet_reverses_a_fall_and_uses_yaw_independent_of_pitch(self):
        for pitch in (-10923, 10923):
            with self.subTest(pitch=pitch):
                run, player, _ = self.encounter()
                self.step(run, player, 65, 2)
                self.step(run, player, 20)
                self.assertTrue(player.mover.airborne)
                self.assertLess(player.mover.velocity[1], 0)
                before = tuple(player.mover.position)
                run.step({player: (16384, pitch, 8)})
                for got, want in zip(player.mover.velocity, (10, 20, 0), strict=True):
                    self.assertAlmostEqual(got, want, places=5)
                self.assertGreater(self.instance(player, 0x27).vars[61].value(), 9.9)
                self.step(run, player, 8, yaw=16384)
                self.assertGreater(player.mover.position[0], before[0] + 1)
                self.assertGreater(player.mover.position[1], before[1] + 2)

    def test_jump_jet_rejects_a_released_press_outside_authored_buffer(self):
        run, player, _ = self.encounter()
        jet = self.instance(player, 0x27)
        self.step(run, player, 1, 8)
        self.step(run, player, 612)
        self.assertGreater(jet.vars[61].value(), .17)
        self.step(run, player, 1, 8)
        self.assertGreater(jet.vars[61].value(), .15)
        self.step(run, player, 30)
        self.assertEqual(jet.vars[61].value(), 0)
        self.assertFalse(player.mover.airborne)
        self.step(run, player, 1, 8)
        self.assertEqual(player.mover.velocity[1], 20)
        self.assertGreater(jet.vars[61].value(), 9.9)

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

    def test_overlapping_hover_buttons_use_one_fuel_and_thrust_path(self):
        baseline, held, _ = self.encounter()
        run, player, _ = self.encounter()
        single = self.instance(held, 0x25)
        combined = self.instance(player, 0x25)
        for buttons in (2, 2 | 0x40, 2):
            self.step(baseline, held, 10, 2)
            self.step(run, player, 10, buttons)
            self.assertEqual(combined.vars[87].value(), single.vars[87].value())
            self.assertEqual(player.mover.velocity, held.mover.velocity)
            self.assertEqual(player.mover.position, held.mover.position)

        thrust = combined.state(combined.graph.node(12).state)
        fuel = combined.vars[87].value()
        y = player.mover.position[1]
        self.step(run, player, 40)
        self.assertGreater(combined.vars[87].value(), fuel)
        self.assertLess(player.mover.position[1], y)
        self.assertFalse(thrust.active)

    def test_jump_hover_handoff_obeys_authored_hold_timer(self):
        for overlap in (0, 5, 10, 12, 13, 20):
            with self.subTest(overlap=overlap):
                run, player, _ = self.encounter()
                hover = self.instance(player, 0x25)
                wait_node = hover.graph.node(2)
                wait = hover.state(wait_node.state)
                thrust = hover.state(hover.graph.node(12).state)
                self.assertAlmostEqual(wait.evaluate(wait_node.field("m_timeout"), watch=False), .2)
                self.step(run, player, 30, 2)
                self.step(run, player, overlap, 2 | 0x40)
                fuel = hover.vars[87].value()
                for _ in range(12 - overlap):
                    self.step(run, player, 1, 0x40)
                    self.assertTrue(wait.active)
                    self.assertFalse(thrust.active)
                    self.assertEqual(hover.vars[87].value(), fuel)
                self.step(run, player, 1, 0x40)
                self.assertFalse(wait.active)
                self.assertTrue(thrust.active)
                self.assertLess(hover.vars[87].value(), fuel)

    def test_releasing_jump_cancels_hover_wait_and_new_press_restarts_timer(self):
        run, player, _ = self.encounter()
        hover = self.instance(player, 0x25)
        wait = hover.state(hover.graph.node(2).state)
        thrust = hover.state(hover.graph.node(12).state)
        self.step(run, player, 30, 2)
        self.step(run, player, 10, 0x40)
        self.assertTrue(wait.active)
        self.assertFalse(thrust.active)
        fuel = hover.vars[87].value()
        for _ in range(4):
            self.step(run, player, 1)
            self.assertFalse(wait.active)
            self.assertFalse(thrust.active)
            self.assertGreaterEqual(hover.vars[87].value(), fuel)

        for _ in range(12):
            self.step(run, player, 1, 0x40)
            self.assertTrue(wait.active)
            self.assertFalse(thrust.active)
        self.step(run, player, 1, 0x40)
        self.assertFalse(wait.active)
        self.assertTrue(thrust.active)

    def test_hover_does_not_replace_faster_jump_jet_impulse(self):
        run, player, _ = self.encounter()
        self.step(run, player, 1, 8)
        self.step(run, player, 3, 2)
        self.assertGreater(player.mover.velocity[1], 15)

    def test_death_cancels_hover_fuel_use_and_blocks_flight_input(self):
        run, player, enemy = self.encounter()
        hover = self.instance(player, 0x25)
        self.step(run, player, 20, 2)
        fuel = hover.vars[87].value()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        position = list(player.mover.position)
        self.step(run, player, 30, 2 | 8)
        self.assertEqual(player.mover.position, position)
        self.assertGreaterEqual(hover.vars[87].value(), fuel)
        self.assertFalse(any(s.active and s.node.cls == "STU_A9C4FF83" for s in hover.states.values()))


if __name__ == "__main__":
    unittest.main()
