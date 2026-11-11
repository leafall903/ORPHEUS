"""Actual Archives attack graphs versus a raised allied physical barrier."""

import os
import unittest

from test_game_combat import aim
from test_widowmaker_npcs import DATA, _ActualNpcMatch

from ow174.game import npc_control, rein_barrier
from ow174.game.content import heroes


@unittest.skipUnless(DATA and os.environ.get('ORPHEUS_REINHARDT_BARRIER_GEOMETRY'),
                     'requires actual NPC data and authored Reinhardt shield mesh')
class WidowmakerNpcPhysicalBarrierTests(_ActualNpcMatch, unittest.TestCase):
    def test_four_actual_npc_weapons_damage_physical_barrier_then_hit_widow_after_drop(self):
        for key in ('1', '2', '3', '5'):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                self.setUp()
                bot = self.spawn(definition, (0, 0, 6))
                self.assertTrue(self.run.match.switch_hero(self.b, heroes()[rein_barrier.REINHARDT]))
                self.b.mover.position[:] = (-4, 0, 3)
                self.b.mover.body = None
                fight = self.run.match.combat
                for _ in range(32):
                    self.run.step({self.b: (0, 0, 2)})
                shield = rein_barrier.active(self.b)
                self.assertIsNotNone(shield)
                self.assertEqual(shield.vars[586].value(), 1600)
                self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 6))
                self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
                self.b.mover.position[:] = (0, 0, 3)
                self.b.mover.body = None
                for _ in range(180):
                    self.run.step({self.b: (0, 0, 2)})
                    if shield.vars[586].value() < 1600:
                        break
                self.assertLess(shield.vars[586].value(), 1600)
                self.assertEqual(self.health(self.a), 175)
                self.b.mover.position[:] = (-4, 0, 3)
                self.b.mover.body = None
                for _ in range(180):
                    self.run.step({})
                    if self.health(self.a) < 175:
                        break
                self.assertIsNone(rein_barrier.active(self.b))
                self.assertLess(self.health(self.a), 175)
