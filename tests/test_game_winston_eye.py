"""Actual FirstPerson fields, shared eye queries and low-cover Tesla casts."""

import unittest

import test_game_winston as fixtures
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class EyeGeometryTests(unittest.TestCase):
    def scene(self):
        case = fixtures.TeslaTests()
        case.setUp()
        return case

    def test_recorded_winston_and_enemy_mccree_source_target_eye_agree(self):
        for hero, standing, crouched in ((0x02E0000000000009, 1.5, 1.05),
                                         (0x02E0000000000042, 1.425, .9975)):
            with self.subTest(hero=hero):
                case = self.scene()
                player = case.enemy
                case.match.switch_hero(player, heroes()[hero])
                for button, height in ((0, standing), (CROUCH, crouched)):
                    case.run.step({player: (0, 0, button)})
                    target = next(t for t in case.fight._targets() if t.owner is player)
                    self.assertAlmostEqual(case.fight._eye(player)[1] - player.mover.position[1], height)
                    self.assertAlmostEqual(target.eye, height)

    def test_actual_standing_and_crouched_tesla_use_original_low_cover_heights(self):
        for crouched, height in ((False, 1.5), (True, 1.05)):
            for above in (False, True):
                with self.subTest(crouched=crouched, cover_above_eye=above):
                    case = self.scene()
                    # Raise the standing target so its capsule axis includes1.5.
                    case.enemy.mover.position[:] = (0, 0 if crouched else .7, 4)
                    case.enemy.mover.body = None
                    top = height + (.025 if above else -.025)
                    case.match.collision_world = World(
                        box(-10, 10, -1, 0, -10, 10) + wall(-5, 2, 5, 2, 0, top))
                    for player in case.run.players:
                        player.mover.use(world=case.match.collision_world)
                    case.run.step({case.winston: (0, 0, 1 | (CROUCH if crouched else 0))})
                    self.assertEqual(case.winston.mover.crouched, crouched)
                    self.assertAlmostEqual(case.health(case.enemy), 200 if above else 199.04)

    def test_unspecified_body_retains_existing_fallback(self):
        self.assertEqual(combat.eye_height(0), combat.EYE)
        self.assertEqual(combat.eye_height(0, True), combat.EYE_CROUCHED_DEFAULT)


if __name__ == '__main__':
    unittest.main()
