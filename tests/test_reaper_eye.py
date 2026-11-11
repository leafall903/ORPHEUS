"""Authored Reaper/McCree first-person heights through actual crouch and low cover."""

import unittest

import test_game_reaper as reaper_tests

from ow174.game import combat, reaper
from ow174.game.collision import World
from ow174.game.content import heroes

MCCREE = 0x02E0000000000042


class ReaperEyeTests(unittest.TestCase):
    def wall(self, run, owner, height):
        x, y, z = owner.mover.position
        z += 0.75
        triangles = [(-500, y, -500, -500, y, 500, 500, y, 500),
                     (-500, y, -500, 500, y, 500, 500, y, -500),
                     (x-5, y, z, x+5, y, z, x+5, y+height, z),
                     (x-5, y, z, x+5, y+height, z, x-5, y+height, z),
                     (x-5, y, z, x+5, y+height, z, x+5, y, z),
                     (x-5, y, z, x-5, y+height, z, x+5, y+height, z)]
        run.match.collision_world = World(triangles, name="authored crouch-eye low cover")
        for player in run.players:
            player.mover.use(world=run.match.collision_world)

    def test_actual_standing_crouch_pose_eye_and_low_cover_for_reaper_and_enemy_mccree(self):
        for hero, standing, crouched in [(reaper.HERO, 1.55, 1.085), (MCCREE, 1.425, 0.9975)]:
            with self.subTest(hero=hero):
                run, owner, _, _ = reaper_tests.ReaperTests().scenario()
                if hero != reaper.HERO:
                    position = tuple(owner.mover.position)
                    run.match.switch_hero(owner, heroes()[hero])
                    owner.mover.position[:] = position
                    owner.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = run.match.combat
                floor = owner.mover.position[1]
                self.assertAlmostEqual(fight._eye(owner)[1] - floor, standing)
                self.wall(run, owner, crouched + 0.025)
                self.assertIsNone(combat.ray_world(run.match.collision_world,
                                                   fight._eye(owner), (0, 0, 1), 2))
                for _ in range(40):
                    run.step({owner: (0, 0, 0x80)})
                self.assertTrue(owner.mover.crouched)
                self.assertAlmostEqual(fight._eye(owner)[1] - owner.mover.position[1], crouched)
                target = next(t for t in fight._targets() if t.owner is owner)
                self.assertAlmostEqual(target.eye, crouched)
                self.assertAlmostEqual(combat.ray_world(run.match.collision_world,
                                                        fight._eye(owner), (0, 0, 1), 2), 0.75)
                for _ in range(40):
                    run.step({})
                self.assertFalse(owner.mover.crouched)
                self.assertAlmostEqual(fight._eye(owner)[1] - owner.mover.position[1], standing)
                self.assertAlmostEqual(next(t for t in fight._targets() if t.owner is owner).eye, standing)

    def test_actual_reaper_pellets_meet_low_wall_from_authored_crouch_eye(self):
        damage = {}
        for crouched in (False, True):
            run, owner, victim, _ = reaper_tests.ReaperTests().scenario()
            fight = run.match.combat
            fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
            for _ in range(40):
                run.step({owner: (0, 0, 0x80 if crouched else 0)})
            self.wall(run, owner, 1.11)
            run.step({owner: (0, 0, 1 | (0x80 if crouched else 0))})
            damage[crouched] = 1000 - fight.vitals[victim.body].total
        self.assertGreater(damage[False], 0)
        self.assertGreater(damage[True], 0)  # some upper spread rays clear the low wall
        self.assertLess(damage[True], damage[False])
