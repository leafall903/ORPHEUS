"""Actual Archives bodies versus an actual Mei wall in Widowmaker's match."""

import unittest

from test_game_combat import aim
from test_widowmaker_npcs import DATA, _ActualNpcMatch

from ow174.game import mei_frost, npc_control
from ow174.game.content import heroes
from ow174.game.mover import mover_data


@unittest.skipUnless(DATA, 'requires actual Archives NPC data')
class WidowmakerNpcIceWallTests(_ActualNpcMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[mei_frost.HERO]))
        self.b.mover.position[:] = (0, 0, 6)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        self.walls = self.fight.walls

    def wall(self, z):
        aim_down = aim(self.fight._eye(self.b), (0, 0, z))

        def tap(button):
            self.run.step({self.b: (*aim_down, button)})
            self.run.step({self.b: (*aim_down, 0)})

        tap(16)
        tap(1)
        self.assertEqual(len(self.walls.pieces), 5)
        return min(self.walls.pieces.values(), key=lambda piece: abs(piece.feet[0])), tap

    def test_five_actual_npc_routes_stop_and_resume_after_wall_removal(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition, (0, 0, 0))
                piece, tap = self.wall(3)
                bot.route = [(0, 0, 7)]
                bot.wait = 0
                for _ in range(125):
                    self.run.step({})
                edge = piece.feet[2] - piece.depth - mover_data(bot.body).stand_radius
                self.assertAlmostEqual(bot.position[2], edge, delta=0.005)
                self.assertEqual(bot.velocity[2], 0)
                self.assertIsNotNone(bot.target)
                tap(16)
                for _ in range(130):
                    self.run.step({})
                self.assertGreater(bot.position[2], piece.feet[2] + piece.depth)

    def test_five_actual_attacks_are_occluded_until_wall_is_removed(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition, (0, 0, 2.5))
                bot.yaw = -32768
                piece, tap = self.wall(1.25)
                self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a), (0, 1, 2.5))
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                for _ in range(45):
                    self.run.step({})
                self.assertIn(piece.entity, self.walls.pieces)
                self.assertEqual(self.health(self.a), 175)
                self.assertNotIn(bot.entity, self.fight.pins)
                tap(16)
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertLess(self.health(self.a), 175)
