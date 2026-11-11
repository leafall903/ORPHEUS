"""Owned FirstPerson heights with actual posture,input,trajectory and low cover.

Flat geometry/capsule receivers and first-impact grenade handling remain
reconstructed. These do not prove animated muzzle or skeletal critical zones.
"""

import unittest

import test_junkrat_projectile_combat as helpers
from test_game_mover import box

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE
from ow174.game.content import heroes


class JunkratEyeGeometryTests(unittest.TestCase):
    def arena(self, cover=None, *, mccree=False):
        run, shooter, victim = helpers.JunkratProjectileTests().setup_fight()
        if mccree:
            run.match.switch_hero(shooter, heroes()[0x02E0000000000042])
            position = tuple(victim.mover.position)
            run.match.switch_hero(victim, heroes()[0x02E0000000000065])
            helpers.JunkratProjectileTests().place(run, victim, position)
            for _ in range(30):
                run.step({})
        x, y, z = shooter.mover.position
        if cover is not None:
            geometry = box(x-100, x+100, y-1, y, z-100, z+100)
            geometry += box(x-10, x+10, y, y+cover, z+1, z+1.1)
            run.match.collision_world = World(geometry)
            for player in run.players:
                player.mover.world = run.match.collision_world
        return run, shooter, victim

    def posture(self, run, shooter, crouched):
        buttons = CROUCH if crouched else 0
        for _ in range(10):
            run.step({shooter: (0, 0, buttons)})
        self.assertEqual(shooter.mover.crouched, crouched)
        return buttons

    def test_original_junkrat_posture_height_matches_origin_and_target_receiver(self):
        for crouched, expected in ((False, 1.25), (True, .875)):
            with self.subTest(crouched=crouched):
                run, shooter, _victim = self.arena()
                buttons = self.posture(run, shooter, crouched)
                target = next(t for t in run.match.combat._targets() if t.owner is shooter)
                self.assertEqual(target.eye, expected)
                self.assertAlmostEqual(run.match.combat._eye(shooter)[1]-shooter.mover.position[1], expected)
                run.step({shooter: (0, 0, buttons | FIRE)})
                rocket, = run.match.combat.rockets
                self.assertAlmostEqual(rocket.position[1]-shooter.mover.position[1], expected)

    def test_standing_junkrat_first_impact_hits_cover_below_old_default_eye(self):
        run, shooter, victim = self.arena(cover=1.3)
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(helpers.JunkratProjectileTests().health(run, victim).total, 200)

    def test_standing_clears_one_metre_cover_while_crouched_is_blocked(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, shooter, victim = self.arena(cover=1.0)
                buttons = self.posture(run, shooter, crouched)
                run.step({shooter: (0, 0, buttons | FIRE)})
                for _ in range(30):
                    run.step({shooter: (0, 0, buttons)})
                health = helpers.JunkratProjectileTests().health(run, victim).total
                self.assertEqual(health, 200 if crouched else 80)

    def test_enemy_mccree_posture_uses_original_heights_for_hit_and_target_eye(self):
        for crouched, expected, cover, blocked in ((False, 1.425, 1.1, False),
                                                  (True, .9975, 1.1, True),
                                                  (False, 1.425, 1.5, True)):
            with self.subTest(crouched=crouched, cover=cover):
                run, shooter, victim = self.arena(cover=cover, mccree=True)
                buttons = self.posture(run, shooter, crouched)
                target = next(t for t in run.match.combat._targets() if t.owner is shooter)
                self.assertEqual(target.eye, expected)
                self.assertAlmostEqual(run.match.combat._eye(shooter)[1]-shooter.mover.position[1], expected)
                run.step({shooter: (0, 0, buttons | FIRE)})
                health = helpers.JunkratProjectileTests().health(run, victim).total
                if blocked:
                    self.assertEqual(health, 200)
                else:
                    self.assertLess(health, 200)

    def test_existing_soldier_crouch_and_unrecorded_body_fallbacks_are_preserved(self):
        self.assertEqual(combat.eye_height(0x04000000000003CF, True), 1.085)
        self.assertEqual(combat.eye_height(0x04000000FFFFFFFF), 1.55)
        self.assertEqual(combat.eye_height(0x04000000FFFFFFFF, True), 1.15)
