"""Audited 104319 FirstPerson heights through live combat and target history.

Static triangle cover uses reconstructed server rays, not native camera/bones.
"""
import unittest

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, mover
from ow174.game.collision import World
from ow174.game.content import heroes


class EyeHeightTests(unittest.TestCase):
    def test_player_origins_targets_and_history_share_authored_stand_and_crouch(self):
        run = Run(KANEZAKA_DM, ("Reinhardt", "McCree"))
        run.match.bots.clear()
        for player, hero, expected in zip(
            run.players, (combat.REINHARDT, 0x02E0000000000042),
            ((1.8, 1.26), (1.425, 0.9975)), strict=True,
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position = [0.0, 3.0, 0.0]
            for crouched, height in zip((False, True), expected, strict=True):
                with self.subTest(hero=hero, crouched=crouched):
                    player.mover.flags_word = (player.mover.flags_word & ~mover.CROUCHED) | (
                        mover.CROUCHED if crouched else 0
                    )
                    target = next(t for t in run.match.combat._targets() if t.owner is player)
                    self.assertAlmostEqual(run.match.combat._eye(player)[1], 3.0 + height)
                    self.assertAlmostEqual(target.eye, height)
                    run.match.combat.update(run.frame * 0.016, run.frame, frames=False)
                    self.assertAlmostEqual(run.match.combat.history[player.body][-1][3], height)
                    run.frame += 1

    def test_low_cover_rays_distinguish_each_authored_eye_from_old_fallback(self):
        for body, crouched, height, cover, visible in (
            (0x0400000000000024, False, 1.8, 1.7, True),
            (0x0400000000000024, True, 1.26, 1.2, True),
            (0x04000000000002A3, False, 1.425, 1.5, False),
            (0x04000000000002A3, True, 0.9975, 1.1, False),
        ):
            with self.subTest(body=body, crouched=crouched):
                wall = World([(-5, 0, 2, -5, cover, 2, 5, cover, 2),
                              (-5, 0, 2, 5, cover, 2, 5, 0, 2)])
                origin = (0, combat.eye_height(body, crouched), 0)
                self.assertAlmostEqual(origin[1], height)
                self.assertEqual(combat.ray_world(wall, origin, (0, 0, 1), 5) is None, visible)
                old = combat.EYE_CROUCHED_DEFAULT if crouched else combat.EYE
                self.assertEqual(combat.ray_world(wall, (0, old, 0), (0, 0, 1), 5) is None,
                                 not visible)


if __name__ == "__main__":
    unittest.main()
