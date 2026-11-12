"""Actual047E command timing and authored150ms input window; full ability scope open."""

import math
import unittest

import test_game_dva_mech as mech_tests
import test_game_dva_placement as placement_tests
from test_game_combat import dva_pilot_input

from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.nodes import Ability


class AbilityBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()

    def scenario(self):
        run, mech, _, _ = mech_tests.AuthoredMechTests().scenario()
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x47E)
        ability = instance.state(0)
        run.step({mech: (0, 0, 8)})
        self.assertTrue(ability.flags & Ability.RUNNING)
        for _ in range(300):
            run.step({})
            if not ability.flags & Ability.RUNNING:
                break
        self.assertFalse(ability.flags & Ability.RUNNING)
        self.assertGreater(ability.remaining(), 1.0)
        return run, mech, instance, ability

    def until_ready(self, run, mech, ability, buttons=0):
        for _ in range(math.ceil(ability.remaining()/0.016)+3):
            run.step({mech: (0, 0, buttons)})

    def test_released_short_press_far_before_cooldown_end_expires_without_auto_recast(self):
        run, mech, instance, ability = self.scenario()
        activation = instance.state(2).counter
        run.step({mech: (0, 0, 8)})
        run.step({})
        original = ability.presses[0][0]
        for _ in range(20):
            run.step({})
        self.assertTrue(all(item[0] <= original for item in ability.presses))
        self.until_ready(run, mech, ability)
        self.assertFalse(ability.flags & Ability.RUNNING)
        self.assertEqual(instance.state(2).counter, activation)

    def test_released_short_press_inside_authored_window_starts_once_at_ready(self):
        run, mech, instance, ability = self.scenario()
        activation = instance.state(2).counter
        while ability.remaining() > 0.08:
            run.step({})
        run.step({mech: (0, 0, 8)})
        self.assertFalse(ability.flags & Ability.RUNNING)
        self.until_ready(run, mech, ability)
        self.assertTrue(ability.flags & Ability.RUNNING)
        self.assertEqual(instance.state(2).counter, activation+1)

    def test_held_button_through_cooldown_starts_once_when_ready(self):
        run, mech, instance, ability = self.scenario()
        activation = instance.state(2).counter
        self.until_ready(run, mech, ability, 8)
        self.assertTrue(ability.flags & Ability.RUNNING)
        self.assertEqual(instance.state(2).counter, activation+1)

    def test_new_press_after_cooldown_completes_starts_once(self):
        run, mech, instance, ability = self.scenario()
        activation = instance.state(2).counter
        self.until_ready(run, mech, ability)
        self.assertFalse(ability.flags & Ability.RUNNING)
        run.step({mech: (0, 0, 8)})
        self.assertTrue(ability.flags & Ability.RUNNING)
        self.assertEqual(instance.state(2).counter, activation+1)

    def test_micro_missiles_without_authored_window_do_not_buffer_a_near_expiry_short_press(self):
        run, mech, _, _ = mech_tests.AuthoredMechTests().scenario()
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x1128)
        ability = instance.state(0)
        self.assertIsNone(ability.node.field("m_246438AD"))
        run.step({mech: (0, 0, 16)})
        self.assertTrue(ability.flags & Ability.RUNNING)
        for _ in range(300):
            run.step({})
            if not ability.flags & Ability.RUNNING:
                break
        self.assertGreater(ability.remaining(), 1)
        while ability.remaining() > 0.08:
            run.step({})
        run.step({mech: (0, 0, 16)})
        self.assertFalse(ability.flags & Ability.RUNNING)
        self.until_ready(run, mech, ability)
        self.assertFalse(ability.flags & Ability.RUNNING)
        run.step({mech: (0, 0, 16)})
        self.assertTrue(ability.flags & Ability.RUNNING)

    def test_pilot_call_mech_charge_gate_uses_original_press_time_for_all_four_input_cases(self):
        for case in ("expired", "near", "held", "fresh"):
            with self.subTest(case=case):
                run, pilot, _, _, placement = placement_tests.PlacementTests().scenario()
                dva_pilot_input(pilot)
                for _ in range(100):
                    run.step({})
                script = pilot.body_script
                cost = script.component.entity_var(ULT_COST).value()
                delay = 0.08 if case == "near" else 0.6
                script.set_ult_charge(cost-cost*delay/script.ult_seconds)
                before = placement.counter
                if case != "fresh":
                    run.step({pilot: (0, 0, 32)})
                for _ in range(100):
                    run.step({pilot: (0, 0, 32 if case == "held" else 0)})
                    if script.component.entity_var(ULT_CHARGE).value() == cost:
                        break
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
                for _ in range(10):
                    run.step({pilot: (0, 0, 32 if case == "held" else 0)})
                if case in ("expired", "fresh"):
                    self.assertEqual(placement.counter, before)
                if case == "fresh":
                    for _ in range(10):
                        run.step({pilot: (0, 0, 32)})
                if case != "expired":
                    self.assertGreater(placement.counter, before)


if __name__ == "__main__":
    unittest.main()
