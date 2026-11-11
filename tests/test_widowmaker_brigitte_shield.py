"""Actual Widow attacks against the selected Brigitte 0EE3 barrier."""

import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import brigitte_shield, combat
from ow174.game.content import heroes


class WidowmakerBrigitteShieldTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[brigitte_shield.HERO]))
        self.b.mover.position[:] = (0, 0, 3)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        for _ in range(30):
            self.step()
        self.shield = self.fight.brigitte_shields.by_body[self.b.body]
        self.assertTrue(self.fight.brigitte_shields.active(self.b, self.shield))

    def step(self, widow=0, brig=2):
        direction = aim(self.fight._eye(self.a), (0, 1, self.b.mover.position[2]))
        self.run.step({self.a: (*direction, widow), self.b: (-32768, 0, brig)})

    def test_real_scoped_ray_is_stopped_and_raising_holds_recovery(self):
        before = self.health(self.b)
        capacity = self.shield.health
        self.assertEqual(capacity, 250)
        for _ in range(100):
            self.step(SCOPE)
        self.step(SCOPE | FIRE)
        self.assertEqual(self.shield.health, capacity - 120)
        self.assertEqual(self.health(self.b), before)
        self.assertFalse(self.a.client.messages(combat.HIT))
        for _ in range(160):
            self.step()
        self.assertEqual(self.shield.health, capacity - 120)
        self.step(brig=0)
        for _ in range(145):
            self.step(brig=0)
        self.assertGreater(self.shield.health, capacity - 120)
        self.assertLess(self.shield.health, capacity)

    def test_break_has_no_spill_and_later_primary_hits_body(self):
        before = self.health(self.b)
        self.shield.health = 5
        self.step(FIRE)
        self.assertEqual(self.shield.health, 1)
        self.assertEqual(self.health(self.b), before)
        self.assertFalse(self.fight.brigitte_shields.active(self.b, self.shield))
        for _ in range(20):
            self.step()
        self.step(FIRE)
        self.assertLess(self.health(self.b), before)

    def test_melee_passes_held_shield_to_body(self):
        self.b.mover.position[2] = 1.5
        self.b.mover.body = None
        for _ in range(4):
            self.step()
        before = self.health(self.b)
        self.step(MELEE)
        for _ in range(20):
            self.step()
        self.assertEqual(self.shield.health, 250)
        self.assertLess(self.health(self.b), before)
