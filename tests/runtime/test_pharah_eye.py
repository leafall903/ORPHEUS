"""Authored FirstPerson scalars in real command rays and static low cover.

These test the server's eye-origin model, not animated camera/muzzle parity.
"""
import unittest
from unittest.mock import patch

import test_pharah_combat as primary
import test_pharah_control as control

from ow174.game import combat
from ow174.game.collision import World


class PharahEyeTests(unittest.TestCase):
    health = primary.PharahCombatTests.health

    def stance(self, run, owner, crouched, yaw=0):
        for _ in range(20):
            run.step({owner: (yaw, 0, 0x80 if crouched else 0)})
        self.assertEqual(owner.mover.crouched, crouched)

    def cover(self, floor, top, z):
        return World([(-10, floor-5, z, 10, floor-5, z, 10, floor+top, z),
                      (-10, floor-5, z, 10, floor+top, z, -10, floor+top, z)])

    def test_player_ray_and_target_descriptor_share_each_authored_stance(self):
        run, pharah, mccree = control.PharahControlTests().arena()
        for crouched in (False, True):
            for owner, standing, lowered in ((pharah, 1.35, .945), (mccree, 1.425, .9975)):
                self.stance(run, owner, crouched)
                expected = lowered if crouched else standing
                fight = run.match.combat
                self.assertAlmostEqual(fight._eye(owner)[1]-owner.mover.position[1], expected)
                target = next(item for item in fight._targets() if item.owner is owner)
                self.assertEqual(target.eye, expected)
        self.assertEqual(combat.eye_height(0x04000000000003CF, True), 1.085)

    def test_pharah_primary_low_cover_uses_authored_standing_and_crouched_origin(self):
        for crouched in (False, True):
            for blocked in (False, True):
                with self.subTest(crouched=crouched, blocked=blocked):
                    run, pharah, victim = primary.PharahCombatTests().encounter()
                    self.stance(run, pharah, crouched)
                    authored, old = (.945, 1.15) if crouched else (1.35, 1.55)
                    top = (authored+old)/2 if blocked else authored-.1
                    wall = self.cover(pharah.mover.position[1], top, 4)
                    with patch.object(run.match.combat, '_collision', return_value=wall):
                        run.step({pharah: (0, 0, 1 | (0x80 if crouched else 0))})
                        for _ in range(60):
                            run.step({pharah: (0, 0, 0x80 if crouched else 0)})
                    self.assertEqual(self.health(run, victim).total, 200 if blocked else 80)

    def test_pharah_launches_use_nonzero_spring_while_changing_stance(self):
        for button, crouched in ((1, True), (1, False), (16, True), (16, False), (32, False)):
            with self.subTest(button=button, crouched=crouched):
                run, pharah, victim = primary.PharahCombatTests().encounter()
                victim.mover.position[0] = 30
                victim.mover.body = None
                self.stance(run, pharah, not crouched)
                if button == 32:
                    run.step({pharah: (0, 0, 0)})
                    script = pharah.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                if button == 16:
                    old_stance = 0 if crouched else 0x80
                    run.step({pharah: (0, 0, 16 | old_stance)})
                    for _ in range(9):
                        run.step({pharah: (0, 0, old_stance)})
                    self.assertFalse(run.match.combat.rockets)
                    button = 0  # change stance on the authored launch frame
                run.step({pharah: (0, 0, button | (0x80 if crouched else 0))})
                rocket, = run.match.combat.rockets
                self.assertEqual(pharah.mover.crouched, crouched)
                self.assertGreater(abs(pharah.mover.spring), .03)
                self.assertAlmostEqual(
                    rocket.position[1] - pharah.mover.position[1],
                    (.945 if crouched else 1.35) + pharah.mover.spring,
                    places=6,
                )

    def test_crouch_transition_launch_clears_cover_above_settled_eye(self):
        run, pharah, victim = primary.PharahCombatTests().encounter()
        self.stance(run, pharah, False)
        wall = self.cover(pharah.mover.position[1], 1.04, 4)
        with patch.object(run.match.combat, '_collision', return_value=wall):
            run.step({pharah: (0, 0, 1 | 0x80)})
            self.assertTrue(pharah.mover.crouched)
            for _ in range(45):
                run.step({pharah: (0, 0, 0x80)})
        self.assertEqual(self.health(run, victim).total, 80)

    def test_enemy_mccree_low_cover_uses_authored_standing_and_crouched_origin(self):
        for crouched in (False, True):
            for blocked in (False, True):
                with self.subTest(crouched=crouched, blocked=blocked):
                    run, pharah, mccree = control.PharahControlTests().arena()
                    self.stance(run, mccree, crouched, -32768)
                    authored, old = (.9975, 1.15) if crouched else (1.425, 1.55)
                    top = (authored+old)/2 if blocked else authored-.1
                    wall = self.cover(mccree.mover.position[1], top, 1.5)
                    with patch.object(run.match.combat, '_collision', return_value=wall):
                        run.step({mccree: (-32768, 0, 1 | (0x80 if crouched else 0))})
                        for _ in range(10):
                            run.step({mccree: (-32768, 0, 0x80 if crouched else 0)})
                    self.assertEqual(self.health(run, pharah).total, 200 if blocked else 130)

    def test_history_retains_the_previous_authored_stance_offset(self):
        run, pharah, _ = primary.PharahCombatTests().encounter()
        self.stance(run, pharah, True)
        run.step({pharah: (0, 0, 0x80)})
        sampled = run.frame
        self.stance(run, pharah, False)
        fight = run.match.combat
        current = next(item for item in fight._targets() if item.owner is pharah)
        historical = fight.seen(current, sampled)
        self.assertIsNotNone(historical)
        self.assertEqual(historical.eye, .945)
        self.assertEqual(current.eye, 1.35)


if __name__ == '__main__':
    unittest.main()
