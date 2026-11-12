"""Actual Tracer magazine/Recall guards around shared notification cleanup.

These combat transitions also passed the old-source diagnostic; the separate
minimal notification tests prove the repaired shared defect.
"""

import unittest

import test_game_tracer as cases
from test_game_combat import aim

from ow174.game import combat, tracer


class TracerNotificationLifetimeTests(unittest.TestCase):
    def empty_magazine(self):
        run, player, enemy, _ = cases.TracerPistolTests().fight()
        health = combat.Vitals(enemy.body, 100000)
        run.match.combat.vitals[enemy.body] = health  # explicit nonlethal target fixture
        facing = aim(run.match.combat._eye(player),
                     (enemy.mover.position[0], enemy.mover.position[1]+1, enemy.mover.position[2]))
        gun = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x16)
        for _ in range(100):
            run.step({player: (*facing, 1)})
            if gun.vars[53].value() == 0:
                return run, player, health, gun, facing
        self.fail("actual primary did not empty the magazine")

    def test_empty_magazine_recall_restores_firing_across_reload_entry_offsets(self):
        for delay in (0, 1, 5, 10, 20):
            with self.subTest(delay_after_empty=delay):
                run, player, health, gun, facing = self.empty_magazine()
                for _ in range(delay):
                    run.step({})
                self.assertEqual(gun.vars[53].value(), 0)
                run.step({player: (*facing, 16)})
                self.assertTrue(tracer.recalling(player))
                for _ in range(100):
                    run.step({})
                    if not tracer.recalling(player):
                        break
                self.assertFalse(tracer.recalling(player))
                self.assertEqual(gun.vars[53].value(), 40)
                before = health.total
                run.step({player: (*facing, 1)})
                self.assertEqual(before-health.total, 12)
                for _ in range(10):
                    run.step({player: (*facing, 1)})
                self.assertEqual(before-health.total, 48)
                self.assertEqual(gun.vars[53].value(), 32)
                self.assertIsNone(player.body_script.component.fault)

    def test_held_primary_through_recall_cannot_fire_inside_and_resumes_after_exit(self):
        run, player, health, gun, facing = self.empty_magazine()
        run.step({player: (*facing, 16)})
        self.assertTrue(tracer.recalling(player))
        before = health.total
        for _ in range(100):
            if not tracer.recalling(player):
                break
            run.step({player: (*facing, 1)})
            if tracer.recalling(player):
                self.assertEqual(health.total, before)
        self.assertFalse(tracer.recalling(player))
        for _ in range(10):
            run.step({player: (*facing, 1)})
        self.assertLess(health.total, before)
        self.assertLess(gun.vars[53].value(), 40)
        self.assertIsNone(player.body_script.component.fault)


if __name__ == "__main__":
    unittest.main()
