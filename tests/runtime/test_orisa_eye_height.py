"""104319 FirstPerson heights in actual player input and low-cover shots."""

import unittest

import test_orisa_combat as fixture
from test_game_combat import aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE
from ow174.game.content import heroes


class OrisaEyeHeightTests(unittest.TestCase):
    def test_original_body_heights_reach_origins_and_target_history(self):
        for name, standing, crouched in (("Orisa", 1.78, 1.246), ("McCree", 1.425, 0.9975)):
            with self.subTest(hero=name):
                run, shooter, _ = fixture.OrisaCombatTests().arena()
                run.match.switch_hero(shooter, heroes()[0x02E000000000013E if name == "Orisa"
                                                       else 0x02E0000000000042])
                fight = run.match.combat
                for buttons, height in ((0, standing), (CROUCH, crouched), (0, standing)):
                    run.step({shooter: (0, 0, buttons)})
                    # Match.update records poses before the next command is taken.
                    run.step({shooter: (0, 0, buttons)})
                    target = next(t for t in fight._targets() if t.owner is shooter)
                    self.assertEqual(shooter.mover.crouched, bool(buttons))
                    self.assertAlmostEqual(fight._eye(shooter)[1] - target.feet[1], height)
                    self.assertAlmostEqual(target.eye, height)
                    self.assertAlmostEqual(fight.seen(target, run.match.tick).eye, height)

    def test_original_eye_heights_change_actual_low_cover_shots(self):
        for name, cover in (("Orisa", 1.60), ("McCree", 1.35)):
            for crouched in (False, True):
                with self.subTest(hero=name, crouched=crouched):
                    run, shooter, victim = fixture.OrisaCombatTests().arena()
                    run.match.switch_hero(shooter, heroes()[0x02E000000000013E if name == "Orisa"
                                                           else 0x02E0000000000042])
                    x, y, z = shooter.mover.position
                    faces = [
                        (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
                        (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
                    ]
                    a, b = (x-2, y, z+2.5), (x+2, y, z+2.5)
                    c, d = (x+2, y+cover, z+2.5), (x-2, y+cover, z+2.5)
                    faces += [tuple(axis for point in tri for axis in point)
                              for tri in ((a, b, c), (a, c, d), (a, c, b), (a, d, c))]
                    run.match.collision_world = World(faces)
                    held = CROUCH if crouched else 0
                    run.step({shooter: (0, 0, held)})
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is victim)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    yaw, pitch = aim(fight._eye(shooter), (x, y+combat.EYE, z+5))
                    run.step({shooter: (yaw, pitch, held | FIRE)})
                    for _ in range(10):
                        run.step({shooter: (yaw, pitch, held)})
                    if crouched:
                        self.assertEqual(vitals.total, before)
                        self.assertFalse(shooter.client.messages(combat.HIT))
                    else:
                        self.assertLess(vitals.total, before)
                        self.assertTrue(shooter.client.messages(combat.HIT))


if __name__ == "__main__":
    unittest.main()
