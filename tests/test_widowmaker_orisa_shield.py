"""Actual Orisa deploy and Widow shot against the authored barrier mesh."""

import os
import unittest

from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, orisa_shield
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_ORISA_GEOMETRY'), 'requires authored shield mesh')
class WidowmakerOrisaShieldTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[orisa_shield.HERO]))
        self.b.mover.position[:] = (0, 0, 8)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        self.shields = self.fight.orisa_shields

    def cast(self):
        for n in range(90):
            self.run.step({self.b: (-32768, 3800, 16 if n == 0 else 0)})
            if self.b.body in self.shields.active:
                return self.shields.active[self.b.body]
        self.fail('Actual 0BD7 cast did not deploy its mesh')

    def test_actual_scoped_shot_spends_shield_capacity_only(self):
        shield = self.cast()
        self.assertEqual(shield.health, 600)
        self.assertEqual(shield.ends - shield.starts, 1250)
        self.assertAlmostEqual(shield.position[1], 0.0)
        before = self.health(self.b)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(shield.health, 480)
        self.assertEqual(self.health(self.b), before)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_world_expiry_and_source_body_replacement(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                self.setUp()
                shield = self.cast()
                body = self.b.body
                owner_frame = self.b.body_script.frame
                if replace:
                    self.assertTrue(self.run.match.switch_hero(self.b, heroes()[orisa_shield.HERO]))
                else:
                    self.run.frame = shield.ends
                    self.run.match.update(shield.ends * combat.TICK, shield.ends)
                    self.assertEqual(self.b.body_script.frame, owner_frame)
                self.assertNotIn(body, self.shields.active)
