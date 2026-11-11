"""104319 explicit FirstPerson heights in actual player poses/projectile cover.

Camera-based server ray origins remain a reconstruction,not native muzzle bones.
"""

import unittest

import test_game_mercy_staff as staff
from test_game_mover import FLOOR, box

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, Command
from ow174.game.content import heroes

MCCREE = 0x02E0000000000042


class MercyEyeHeightTests(unittest.TestCase):
    def fixture(self, cover=None):
        f = staff.MercyStaffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.ally.mover.position[:] = (10, 1, 5)
        f.enemy.mover.position[:] = (0, 1, 10)
        if cover is not None:
            f.match.collision_world = World(FLOOR + box(-5, 5, 0, cover, 4, 4.25))
            for player in f.run.players:
                player.mover.use(world=f.match.collision_world)
        return f

    def step(self, f, count, *, buttons=0, action=0, enemy_buttons=0):
        for index in range(count):
            f.run.frame += 1
            f.match.update(f.run.frame * combat.TICK, f.run.frame)
            for player in f.run.players:
                player.take_commands([Command(f.run.frame,
                    buttons=buttons if player is f.mercy else enemy_buttons if player is f.enemy else 0,
                    action=action if player is f.mercy and index == 0 else 0)])

    def test_mercy_actual_standing_and_crouched_eyes_match_authored_values(self):
        f = self.fixture()
        for buttons, expected in ((0, 1.45), (CROUCH, 1.015), (0, 1.45)):
            self.step(f, 20, buttons=buttons)
            target = next(t for t in f.fight._targets() if t.owner is f.mercy)
            self.assertAlmostEqual(target.eye, expected)
            self.assertAlmostEqual(f.fight._eye(f.mercy)[1] - f.mercy.mover.position[1], expected)

    def test_enemy_mccree_actual_standing_and_crouched_target_eyes(self):
        f = self.fixture()
        f.match.switch_hero(f.enemy, heroes()[MCCREE])
        for buttons, expected in ((0, 1.425), (CROUCH, 0.9975), (0, 1.425)):
            self.step(f, 20, enemy_buttons=buttons)
            target = next(t for t in f.fight._targets() if t.owner is f.enemy)
            self.assertAlmostEqual(target.eye, expected)
            self.assertAlmostEqual(f.fight._eye(f.enemy)[1] - f.enemy.mover.position[1], expected)

    def cover_shot(self, height, crouched, expected):
        f = self.fixture(height)
        self.step(f, 35)
        self.step(f, 1, action=2)
        self.step(f, 35)
        held = CROUCH if crouched else 0
        self.step(f, 20, buttons=held)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        vitals = f.fight.vitals_of(target)
        before = vitals.total
        self.step(f, 1, buttons=held | FIRE)
        self.step(f, 25, buttons=held)
        self.assertAlmostEqual(before - vitals.total, expected)

    def test_standing_horizontal_blaster_clears_lower_cover_and_hits_taller_cover(self):
        # The clear standing ray also reaches the target's server critical zone.
        for height, damage in ((2.40, 40), (2.50, 0)):
            with self.subTest(cover=height):
                self.cover_shot(height, False, damage)

    def test_crouched_horizontal_blaster_clears_lower_cover_and_hits_taller_cover(self):
        for height, damage in ((1.98, 20), (2.05, 0)):
            with self.subTest(cover=height):
                self.cover_shot(height, True, damage)
