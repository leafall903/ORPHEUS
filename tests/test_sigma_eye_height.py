"""104319 FirstPerson heights through actual pose, target services and low-cover shots."""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, sigma
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE
from ow174.game.content import heroes


class SigmaEyeHeightTests(unittest.TestCase):
    def arena(self, shooter_hero=sigma.HERO):
        run = Run(KANEZAKA_DM, ("Shooter", "Sigma"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, target = run.players
        run.match.switch_hero(shooter, heroes()[shooter_hero])
        run.match.switch_hero(target, heroes()[sigma.HERO])
        x, y, z = shooter.mover.position
        target.mover.position = [x, y, z + 6]
        health = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is target))
        return run, shooter, target, health

    def low_wall(self, run, height):
        x, y, z = run.players[0].mover.position
        a, b, c, d = ((x - 4, y, z + 2), (x + 4, y, z + 2),
                      (x + 4, y + height, z + 2), (x - 4, y + height, z + 2))
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        for player in run.players:
            player.mover.use(world=run.match.collision_world)

    def test_sigma_xml_heights_drive_shooter_and_player_target_pose(self):
        run, shooter, _target, _health = self.arena()
        fight = run.match.combat
        self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], 2.1)
        self.assertEqual(next(t for t in fight._targets() if t.owner is shooter).eye, 2.1)
        for _ in range(20):
            run.step({shooter: (0, 0, CROUCH)})
        self.assertTrue(shooter.mover.crouched)
        self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], 1.75)
        self.assertEqual(next(t for t in fight._targets() if t.owner is shooter).eye, 1.75)

    def test_actual_sigma_pair_standing_clears_low_wall_but_crouching_is_blocked(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, shooter, _target, health = self.arena()
                self.low_wall(run, 1.78)
                buttons = CROUCH if crouched else 0
                for _ in range(20):
                    run.step({shooter: (0, 0, buttons)})
                before = health.total
                run.step({shooter: (0, 0, buttons | FIRE)})
                for _ in range(60):
                    run.step({shooter: (0, 0, buttons)})
                if crouched:
                    self.assertEqual(health.total, before)
                else:
                    self.assertLess(health.total, before)

    def test_actual_enemy_mccree_shot_low_cover_rejects_old_generic_eye(self):
        for old_eye in (False, True):
            with self.subTest(old_eye=old_eye):
                run, shooter, _target, health = self.arena(0x02E0000000000042)
                self.low_wall(run, 1.5)
                before = health.total
                with patch.dict(combat.EYE_STANDING,
                                {shooter.hero.body: 1.55} if old_eye else {}):
                    run.step({shooter: (0, 0, FIRE)})
                    for _ in range(25):
                        run.step({})
                if old_eye:
                    self.assertLess(health.total, before)
                else:
                    self.assertEqual(health.total, before)


if __name__ == "__main__":
    unittest.main()
