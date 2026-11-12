"""Actual Tank input across death/body replacement; declared transport profiles."""

import unittest
from unittest.mock import patch

import test_bastion_combat as bastion_tests

from ow174.game import combat
from ow174.game.content import heroes
from ow174.game.script.driver import SOLDIER


class BastionProjectileLifecycleTests(unittest.TestCase):
    def test_body_replacement_retires_flight_with_and_without_network_lease(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                case = bastion_tests.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 60))
                case.tank()
                if leased:
                    case.step(1)
                else:
                    # Only suppress network lease allocation; the real volley,
                    # projectile definition and damage payload still execute.
                    with patch.object(case.fight.projectiles, "lease_id", return_value=None):
                        case.step(1)
                self.assertEqual(len(case.fight.rockets), 1)
                rocket = case.fight.rockets[0]
                self.assertEqual(rocket.shot is not None, leased)
                old_body = case.player.body
                self.assertTrue(case.run.match.switch_hero(case.player, heroes()[SOLDIER]))
                self.assertNotEqual(case.player.body, old_body)
                case.player.mover.position[:] = (10, 1, 0)
                case.step(0, 90)
                self.assertEqual(case.health(case.enemy).total, 200)
                self.assertFalse(case.health(case.enemy).dead)
                self.assertNotIn(rocket, case.fight.rockets)
                self.assertFalse(case.player.client.messages(combat.KILL_NOTICE))

    def test_death_keeps_already_flying_tank_damage_and_original_attribution(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                case = bastion_tests.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 60))
                case.tank()
                if leased:
                    case.step(1)
                else:
                    with patch.object(case.fight.projectiles, "lease_id", return_value=None):
                        case.step(1)
                self.assertEqual(len(case.fight.rockets), 1)
                original_body = case.player.body
                case.fight._damage(case.enemy, case.target(case.player), 10000,
                                   False, None, case.run.frame)
                self.assertTrue(case.health(case.player).dead)
                self.assertEqual(len(case.fight.rockets), 1)
                case.step(1 | 8 | 32, 90)
                victim = case.health(case.enemy)
                self.assertTrue(victim.dead)
                self.assertEqual(victim.killer, original_body)
                self.assertEqual(case.enemy.client.messages(combat.DEATH_NOTICE)[-1],
                                 {"+0x78": combat.KILLED_BY,
                                  "+0x7C": {"+0x0": original_body}})
                self.assertEqual([hit["+0x78"]["+0x4"]
                                  for hit in case.player.client.messages(combat.HIT)], [65, 135])
                self.assertEqual(len(case.player.client.messages(combat.KILL_NOTICE)), 1)
                self.assertFalse(case.fight.rockets)


if __name__ == "__main__":
    unittest.main()
