"""Real Bastion weapon input against the authored Reinhardt barrier."""
import unittest

import test_bastion_combat as kit

from ow174.game import combat, reinhardt_barrier


class BastionBarrierTests(unittest.TestCase):
    def test_reinhardt_barrier_intercepts_each_bastion_weapon_before_body(self):
        for form, damage in (("recon", 40), ("sentry", 75), ("tank", 205)):
            for scenario in ("raised", "lowered", "wall"):
                with self.subTest(form=form, scenario=scenario):
                    case = kit.BastionCombatTests()
                    case.fixture(enemy_position=(0, 1, 6), bystander_position=(0, 1, 8),
                                 enemy_hero=reinhardt_barrier.REINHARDT)
                    if form == "sentry":
                        case.configure()
                    if form == "tank":
                        case.tank()
                    enemy = case.enemy
                    for _ in range(12):
                        case.run.step({enemy: (-32768, 0, 2)})
                    shield = reinhardt_barrier.active(enemy)
                    self.assertIsNotNone(shield)
                    shield_before = shield.vars[586].value()
                    if scenario == "lowered":
                        case.run.step({enemy: (-32768, 0, 0)})
                        self.assertIsNone(reinhardt_barrier.active(enemy))
                    if scenario == "wall":
                        case.wall(3)
                    health = case.health(enemy)
                    before = health.total
                    behind = case.health(case.bystander)
                    behind_before = behind.total
                    yaw, pitch = case.command_aim
                    for frame in range(24):
                        buttons = int(frame < (1 if form == "tank" else 10))
                        held = 0 if scenario == "lowered" else 2
                        case.run.step({case.player: (yaw, pitch, buttons), enemy: (-32768, 0, held)})
                    expected = damage if scenario == "raised" else 0
                    self.assertEqual(shield_before - shield.vars[586].value(), expected)
                    if scenario == "lowered":
                        self.assertLess(health.total, before)
                    else:
                        self.assertEqual(health.total, before)
                        self.assertEqual(behind.total, behind_before)
                        self.assertFalse(case.player.client.messages(combat.HIT))
                    self.assertFalse(case.fight.rockets)


    def test_tank_splash_stays_on_the_approach_side_when_the_barrier_breaks(self):
        for position, takes_damage in (((1, 1, 4.3), True), ((1, 1, 6), False)):
            with self.subTest(position=position):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 6), bystander_position=position,
                             enemy_hero=reinhardt_barrier.REINHARDT)
                case.tank()
                enemy = case.enemy
                for _ in range(12):
                    case.run.step({enemy: (-32768, 0, 2)})
                shield = reinhardt_barrier.active(enemy)
                self.assertIsNotNone(shield)
                reinhardt_barrier.damage(shield, 1500)
                self.assertEqual(shield.vars[586].value(), 100)
                defender = case.health(enemy)
                ally = case.health(case.bystander)
                before = defender.total, ally.total
                yaw, pitch = case.command_aim
                for frame in range(24):
                    case.run.step({case.player: (yaw, pitch, int(frame == 0)), enemy: (-32768, 0, 2)})
                self.assertIsNone(reinhardt_barrier.active(enemy))
                self.assertEqual(shield.vars[586].value(), 0)
                self.assertEqual(defender.total, before[0])
                self.assertEqual(ally.total < before[1], takes_damage)
                self.assertFalse(case.fight.rockets)

    def test_sentry_breaks_the_actual_barrier_and_reinhardt_raises_it_again(self):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 6), enemy_hero=reinhardt_barrier.REINHARDT)
        case.configure()
        enemy = case.enemy
        for _ in range(12):
            case.run.step({enemy: (-32768, 0, 2)})
        shield = reinhardt_barrier.active(enemy)
        self.assertIsNotNone(shield)
        yaw, pitch = case.command_aim
        for _ in range(280):
            case.run.step({case.player: (yaw, pitch, 1), enemy: (-32768, 0, 2)})
            if reinhardt_barrier.active(enemy) is None:
                break
        self.assertEqual(shield.vars[586].value(), 0)
        self.assertIsNone(reinhardt_barrier.active(enemy))
        health = case.health(enemy)
        before = health.total
        for _ in range(10):
            case.run.step({case.player: (yaw, pitch, 1), enemy: (-32768, 0, 2)})
        self.assertLess(health.total, before)
        self.assertIsNone(reinhardt_barrier.active(enemy))
        for _ in range(390):
            case.run.step({enemy: (-32768, 0, 0)})
        self.assertGreater(shield.vars[586].value(), 0)
        for _ in range(45):
            case.run.step({enemy: (-32768, 0, 2)})
            if reinhardt_barrier.active(enemy) is not None:
                break
        self.assertIs(reinhardt_barrier.active(enemy), shield)
        before = health.total
        stored = shield.vars[586].value()
        for _ in range(10):
            case.run.step({case.player: (yaw, pitch, 1), enemy: (-32768, 0, 2)})
        self.assertEqual(health.total, before)
        self.assertLess(shield.vars[586].value(), stored)


if __name__ == "__main__":
    unittest.main()
