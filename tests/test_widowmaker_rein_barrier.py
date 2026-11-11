"""Actual Widow shots against authored Reinhardt0095 and the curved shield mesh."""

import os
import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, rein_barrier
from ow174.game.content import heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_BARRIER_GEOMETRY'), 'requires authored shield mesh')
class WidowmakerReinhardtBarrierTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[rein_barrier.REINHARDT]))
        self.b.mover.position[:] = (0, 0, 5)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        for _ in range(30):
            self.step()
        self.shield = rein_barrier.active(self.b)
        self.assertIsNotNone(self.shield)
        self.assertEqual(self.shield.vars[586].value(), 1600)

    def step(self, widow=0, rein=2, yaw=-32768):
        direction = aim(self.fight._eye(self.a), (0, 1, 5))
        self.run.step({self.a: (*direction, widow), self.b: (yaw, 0, rein)})

    def test_actual_scoped_and_primary_hits_change_shield_only(self):
        before = self.health(self.b)
        self.step(FIRE)
        self.assertEqual(self.shield.vars[586].value(), 1587)
        for _ in range(100):
            self.step(SCOPE)
        self.step(SCOPE | FIRE)
        self.assertEqual(self.shield.vars[586].value(), 1467)
        self.assertEqual(self.health(self.b), before)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_breaking_event_does_not_spill_and_later_hit_reaches_body(self):
        before = self.health(self.b)
        self.shield.write((expr.INSTANCE, 586), 5)
        self.shield.component.run_now()
        self.step(FIRE)
        self.assertEqual(self.shield.vars[586].value(), 0)
        self.assertEqual(self.health(self.b), before)
        self.assertIsNone(rein_barrier.active(self.b))
        for _ in range(15):
            self.step(rein=0)
        self.step(FIRE, rein=0)
        self.assertLess(self.health(self.b), before)

    def test_aim_away_exposes_body_and_held_shield_retains_capacity(self):
        before = self.health(self.b)
        self.step(yaw=0)
        self.step(FIRE, yaw=0)
        self.assertEqual(self.shield.vars[586].value(), 1600)
        self.assertLess(self.health(self.b), before)
