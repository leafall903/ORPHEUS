"""Actual opposing body02A3 stance and low-cover fire with prepared104319 eye inputs."""

import os
import unittest

from test_doomfist_combat import DoomfistFixture
from test_game_combat import aim
from test_game_mover import box

from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_REQUIRE_FIRST_PERSON') == '1',
                     'requires prepared direct FirstPerson offsets')
class OpposingMcCreeEyeTests(DoomfistFixture, unittest.TestCase):
    def enemy(self):
        self.prepare(3)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.run.step({})

    def test_actual_opposing_stance_updates_eye_target_and_retained_history(self):
        self.enemy()
        for stance, height in ((0, 1.425), (CROUCH, .9975), (0, 1.425)):
            for _ in range(12):
                self.run.step({self.b: (-32768, 0, stance)})
            self.assertEqual(self.b.mover.crouched, bool(stance))
            self.assertAlmostEqual(self.fight._eye(self.b)[1] - self.b.mover.position[1], height)
            self.assertEqual(next(t.eye for t in self.fight._targets() if t.owner is self.b), height)
            self.assertEqual(self.fight.history[self.b.body][-1][3], height)

    def test_actual_enemy_standing_primary_clears_cover_while_crouched_primary_is_blocked(self):
        for stance, damage in ((0, 70), (CROUCH, 0)):
            with self.subTest(stance=stance):
                self.enemy()
                x, y, z = self.a.mover.position
                world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                              + box(x - 4, x + 4, y, y + 1.03, z + 1.3, z + 1.5))
                self.match.collision_world = world
                for player in self.run.players:
                    player.mover.use(world=world)
                for _ in range(4):
                    self.run.step({self.b: (-32768, 0, stance)})
                own = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))
                before = own.total
                yaw, pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
                self.run.step({self.b: (yaw, pitch, stance | 1)})
                for _ in range(10):
                    self.run.step({self.b: (yaw, pitch, stance)})
                self.assertEqual(before - own.total, damage)
