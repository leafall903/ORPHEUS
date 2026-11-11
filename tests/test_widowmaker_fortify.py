"""Actual Widow and Ana inputs against the selected authored Orisa Fortify scope."""

import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, defenses
from ow174.game.commands import Command
from ow174.game.content import heroes


class WidowmakerFortifyTests(_WidowmakerMatch, unittest.TestCase):
    def test_actual_head_ray_is_reduced_until_world_expiry_with_defender_input_paused(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[defenses.ORISA]))
        self.b.mover.position[:] = (0, 0, 8)
        self.b.mover.body = None
        fight = self.run.match.combat
        head = fight._eye(self.b)[1]
        self.press(SCOPE, 100, height=head)
        direction = aim(fight._eye(self.a), (0, head, 8))
        self.run.step({self.a: (*direction, SCOPE), self.b: (-32768, 0, 8)})
        self.assertAlmostEqual(defenses.fortify(self.b, fight.now)[0], 0.6)
        owner_frame = self.b.body_script.frame
        before = self.health(self.b)
        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.a.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1],
                                     buttons=SCOPE | FIRE, extra=0)])
        hit = self.a.client.messages(combat.HIT)[-1]['+0x78']
        self.assertFalse(hit['+0x8'] & combat.CRITICAL)
        self.assertAlmostEqual(before - self.health(self.b), 67, places=4)
        self.run.frame += 260
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertIsNone(defenses.fortify(self.b, fight.now))
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.press(SCOPE, 100, height=head)
        self.press(SCOPE | FIRE, height=head)
        hit = self.a.client.messages(combat.HIT)[-1]['+0x78']
        self.assertTrue(hit['+0x8'] & combat.CRITICAL)
        self.assertGreaterEqual(hit['+0x4'], 295)

    def test_actual_sleep_dart_deals_reduced_damage_without_control(self):
        self.assertTrue(self.run.match.switch_hero(self.a, heroes()[defenses.ORISA]))
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E000000000013B]))
        self.a.mover.position[:] = (0, 0, 0)
        self.b.mover.position[:] = (0, 0, 2)
        self.a.mover.body = self.b.mover.body = None
        fight = self.run.match.combat
        for _ in range(10):
            self.run.step({})
        self.run.step({self.a: (0, 0, 8)})
        self.assertTrue(fight.control_immune(self.a))
        before = self.health(self.a)
        self.press(8, attacker=self.b, defender=self.a)
        self.press(0, 36, attacker=self.b, defender=self.a)
        self.assertAlmostEqual(before - self.health(self.a), 1.5)
        self.assertFalse(fight.is_stunned(self.a))
        self.assertNotIn(self.a.body, fight.sleeps)
