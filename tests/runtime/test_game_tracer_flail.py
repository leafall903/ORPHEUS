"""Actual Brigitte 1248 input against Tracer in the reconstructed swing volume."""
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
from test_game_mover import box

from ow174.game import combat, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import heroes

BRIGITTE = 0x02E0000000000195


class TracerFlailTests(unittest.TestCase):
    def setUp(self):
        self.run, self.target, self.brigitte, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.match.switch_hero(self.brigitte, heroes()[BRIGITTE])
        self.place(5)
        self.fight = self.match.combat
        target = next(target for target in self.fight._targets() if target.owner is self.target)
        self.vitals = self.fight.vitals_of(target)

    def place(self, distance):
        x, y, z = self.target.mover.position
        self.brigitte.mover.position = [x, y, z + distance]
        self.brigitte.mover.body = None

    def swing(self, yaw=-32768, frames=40):
        for _ in range(frames):
            self.run.step({self.brigitte: (yaw, 0, FIRE)})

    def test_actual_flail_applies_once_then_repress_hits_again(self):
        self.swing()
        self.assertEqual(self.vitals.total, 115)
        for _ in range(8):
            self.run.step({})
        self.swing()
        self.assertEqual(self.vitals.total, 80)
        hits = self.brigitte.client.messages(combat.HIT)
        self.assertEqual([(hit["+0x78"]["+0x4"], hit["+0x78"]["+0x8"]) for hit in hits], [(35, 0), (35, 0)])

    def test_actual_flail_rejects_rear_range_and_friendly(self):
        for condition in ("rear", "range", "friendly"):
            with self.subTest(condition=condition):
                self.setUp()
                yaw = -32768
                if condition == "rear":
                    yaw = 0
                elif condition == "range":
                    self.place(8)
                else:
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.brigitte.team = self.target.team
                self.swing(yaw=yaw)
                self.assertEqual(self.vitals.total, 150)
                self.assertFalse(self.brigitte.client.messages(combat.HIT))

    def test_actual_flail_is_blocked_by_map_geometry(self):
        x, y, z = self.target.mover.position
        collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                          + box(x - 5, x + 5, y, y + 5, z + 2, z + 2.1))
        self.match.collision_provider = StaticCollisionProvider(collision)
        self.match.collision_world = collision
        self.swing()
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.brigitte.client.messages(combat.HIT))

    def test_actual_recall_protects_tracer_from_flail(self):
        self.run.step({self.target: (0, 0, 16)})
        self.assertTrue(tracer.recalling(self.target))
        self.swing(frames=36)
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.brigitte.client.messages(combat.HIT))
        for _ in range(80):
            if not tracer.recalling(self.target):
                break
            self.run.step({})
        self.assertFalse(tracer.recalling(self.target))
        self.swing()
        self.assertEqual(self.vitals.total, 115)
