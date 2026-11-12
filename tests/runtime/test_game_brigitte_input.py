"""Actual Brigitte input across authored cooldown/readiness and buffer windows."""

import unittest

import test_game_brigitte as flail_tests
import test_game_brigitte_rally as rally_tests
import test_game_brigitte_repair as repair_tests
import test_game_brigitte_shield as shield_tests


class BrigitteInputTests(unittest.TestCase):
    def fixture(self, kind):
        if kind == "whip":
            run, player = flail_tests.BrigitteTests().fixture()
            flail_tests.BrigitteTests().whip(run, player)
            index, state, phase, idle, press = 0x1259, 0, 6, 0, 8
        elif kind == "bash":
            run, player, *_ = shield_tests.BarrierTests().fixture()
            run.step({player: (0, 0, 3)})
            for _ in range(40):
                run.step({player: (0, 0, 2)})
            index, state, phase, idle, press = 0xEE3, 96, 14, 2, 3
        else:
            run, player, *_ = repair_tests.RepairTests().fixture()
            for _ in range(3):
                repair_tests.RepairTests().step(run, player, 45, cast=True)
            index, state, phase, idle, press = 0xEDD, 6, 19, 0, 16
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == index)
        ability = instance.states[state]
        if kind == "repair":
            self.assertEqual(instance.vars[11].value(), 0)
            def remaining():
                return instance.vars[61].value() if instance.vars[11].value() <= 0 else 0
        else:
            remaining = ability.remaining
        self.assertGreater(remaining(), 1)
        return run, player, instance, ability, phase, idle, press, remaining

    @staticmethod
    def advance(run, player, buttons, frames=1):
        for _ in range(frames):
            run.step({player: (0, 0, buttons)})

    def wait(self, run, player, buttons, condition):
        for _ in range(1000):
            if condition():
                return
            self.advance(run, player, buttons)
        self.fail("authored readiness or activation did not arrive within16s")

    def test_early_released_press_expires_and_new_press_after_readiness_starts(self):
        for kind in ("whip", "bash", "repair"):
            with self.subTest(kind=kind):
                run, player, instance, _ability, phase, idle, press, remaining = self.fixture(kind)
                before = instance.states[phase].counter
                self.advance(run, player, press)
                self.wait(run, player, idle, lambda remaining=remaining: remaining() <= 0)
                self.advance(run, player, idle, 30)
                self.assertEqual(instance.states[phase].counter, before)
                self.advance(run, player, press)
                self.advance(run, player, idle, 30)
                self.assertEqual(instance.states[phase].counter, before + 1)

    def test_near_ready_short_press_respects_each_resources_actual_window(self):
        for kind in ("whip", "bash", "repair"):
            with self.subTest(kind=kind):
                run, player, instance, ability, phase, idle, press, remaining = self.fixture(kind)
                before = instance.states[phase].counter
                self.wait(run, player, idle, lambda remaining=remaining: remaining() <= 0.08)
                self.assertGreater(remaining(), 0)
                self.advance(run, player, press)
                self.advance(run, player, idle, 35)
                #1259 has150ms;0EE3/0EDD have no authored early window.
                window = ability.node.field("m_246438AD")
                self.assertEqual(window is not None, kind == "whip")
                self.assertEqual(instance.states[phase].counter, before + (1 if kind == "whip" else 0))

    def test_held_press_follows_resource_route_instead_of_repeating_bash(self):
        for kind in ("whip", "bash", "repair"):
            with self.subTest(kind=kind):
                run, player, instance, _ability, phase, idle, press, remaining = self.fixture(kind)
                before = instance.states[phase].counter
                self.advance(run, player, press)
                self.assertEqual(instance.states[phase].counter, before)
                if kind == "bash":
                    #0EE3 LogicalButton22 -> gate98 requires a new down edge;
                    # Ability170 owns the cooldown but has no activation output.
                    self.wait(run, player, press, lambda remaining=remaining: remaining() <= 0)
                    self.advance(run, player, press, 30)
                    self.assertEqual(instance.states[phase].counter, before)
                    self.advance(run, player, idle)
                    self.advance(run, player, press)
                    self.advance(run, player, idle, 30)
                else:
                    self.wait(run, player, press,
                              lambda instance=instance, phase=phase, before=before:
                              instance.states[phase].counter > before)
                self.assertEqual(instance.states[phase].counter, before + 1)

    def test_rally_charge_gate_expires_early_press_buffers_near_press_and_accepts_held_or_new(self):
        for mode in ("early", "near", "held", "new"):
            with self.subTest(mode=mode):
                case = rally_tests.RallyTests()
                run, player, *_ = case.fixture()
                case.activate(run, player)
                case.advance(run, 700)  # actual first aura has ended, charge remains0
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x12BE)
                before = instance.states[0].counter
                self.assertEqual(case.charge(player), 0)
                if mode != "new":
                    self.advance(run, player, 32)
                    self.advance(run, player, 32 if mode == "held" else 0, 6 if mode == "near" else 20)
                self.assertEqual(instance.states[0].counter, before)
                # Explicit test/GM charge supply isolates the real readiness gate;
                # this scenario makes no claim about earning a full ultimate.
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                self.advance(run, player, 32 if mode in ("held", "new") else 0, 3)
                self.assertEqual(instance.states[0].counter, before + (0 if mode == "early" else 1))
                if mode == "early":
                    self.advance(run, player, 0, 30)
                    self.assertEqual(instance.states[0].counter, before)
                    self.advance(run, player, 32, 3)
                    self.assertEqual(instance.states[0].counter, before + 1)


if __name__ == "__main__":
    unittest.main()
