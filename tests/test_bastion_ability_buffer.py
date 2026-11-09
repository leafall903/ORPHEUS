"""Actual melee cooldown inputs exercise the shared Ability press lifetime."""

import math
import unittest

import test_bastion_combat as kit

from ow174.game import combat
from ow174.game.script import expr


class BastionAbilityBufferTests(unittest.TestCase):
    def fixture(self):
        case = kit.BastionCombatTests()
        script = case.fixture(enemy_position=(0, 1, 1.4))
        case.step(0x800)
        self.assertEqual(case.health(case.enemy).total, 170)
        ability = next(i for i in script.component.instances.values() if i.graph.index == 0x43).states[6]
        for _ in range(50):
            case.step()
            if not ability.flags & ability.RUNNING:
                break
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertGreater(ability.remaining(), 0.15)
        self.assertEqual(ability.evaluate(ability.node.field('m_246438AD'), watch=False),
                         expr.f32(0.15))
        return case, ability

    def test_expired_early_melee_tap_does_not_replay_and_fresh_press_damages(self):
        case, ability = self.fixture()
        case.step(0x800)
        case.step(0, math.ceil(ability.remaining() / combat.TICK) + 5)
        self.assertEqual(case.health(case.enemy).total, 170)
        self.assertEqual(ability.remaining(), 0)
        case.step(0x800, 10)
        self.assertEqual(case.health(case.enemy).total, 140)

    def test_near_expiry_short_buffer_and_held_input_start_when_ready(self):
        for held in (False, True):
            with self.subTest(held=held):
                case, ability = self.fixture()
                if not held:
                    while ability.remaining() > 0.08:
                        case.step()
                case.step(0x800)
                self.assertEqual(case.health(case.enemy).total, 170)
                case.step(0x800 if held else 0, math.ceil(ability.remaining() / combat.TICK) + 5)
                self.assertEqual(case.health(case.enemy).total, 140)
                case.step(0, 100)
                self.assertEqual(case.health(case.enemy).total, 140)


if __name__ == '__main__':
    unittest.main()
