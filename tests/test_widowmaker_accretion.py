"""Actual Sigma rock command versus Widowmaker and the selected defense gates."""

import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import combat, defenses, sigma_accretion
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.physics_snapshot import snapshot_mover


class WidowmakerAccretionTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[sigma_accretion.HERO]))
        self.b.mover.position[:] = (0, 0, 6)
        self.b.mover.body = None
        self.fight = self.run.match.combat

    def rock(self, *, must_hit=True):
        source = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        before = self.health(self.a)
        seen = None
        for n in range(100):
            self.run.step({self.b: (*source, 16 if n == 0 else 0)})
            if self.fight.rockets and seen is None:
                seen = self.fight.rockets[0]
            if self.health(self.a) < before and must_hit:
                break
        self.assertIsNotNone(seen)
        self.assertEqual(seen.gravity, 18)
        self.assertFalse(seen.detonate_on_expiry)
        self.assertEqual(seen.direct.knockback, (6, 3, ((3, 1),)))
        return seen

    def test_actual_rock_deals_damage_controls_and_moves_widow_then_expires(self):
        self.rock()
        self.assertEqual(self.health(self.a), 105)
        self.assertTrue(self.fight.is_stunned(self.a))
        deadline = self.fight.stuns[self.a.body]
        self.assertEqual(deadline - self.fight.tick, 50)
        self.assertTrue(self.a.mover.external_impulse)
        self.assertLess(self.a.mover.velocity[2], -4.5)
        self.assertGreater(self.a.mover.position[1], 0.0)
        recorded = snapshot_mover(self.a.mover)
        self.a.mover.external_impulse = False
        self.assertNotEqual(snapshot_mover(self.a.mover), recorded)
        self.a.mover.external_impulse = True
        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.a.take_commands([Command(self.run.frame, buttons=FIRE | 8 | 16 | 32, forward=127)])
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.assertIsNone(self.a.body_script.infra_window())
        self.assertFalse(self.fight.venom.flights)
        self.run.frame = deadline
        self.run.match.update(deadline * combat.TICK, deadline)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.press(0, 35)
        self.press(FIRE)
        self.assertTrue(self.a.client.messages(combat.HIT))

    def test_wall_blocks_rock_damage_impulse_and_control(self):
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-5, 0, 3, 5, 0, 3, 5, 3, 3),
            (-5, 0, 3, 5, 3, 3, -5, 3, 3),
        ])
        self.rock(must_hit=False)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertFalse(self.a.mover.external_impulse)

    def test_fortify_allows_reduced_rock_damage_but_blocks_control_and_impulse(self):
        self.assertTrue(self.run.match.switch_hero(self.a, heroes()[defenses.ORISA]))
        self.a.mover.position[:] = (0, 0, 0)
        self.a.mover.body = None
        self.run.step({self.a: (0, 0, 8)})
        self.assertTrue(self.fight.control_immune(self.a))
        before = self.health(self.a)
        rocket = self.rock()
        self.assertEqual(rocket.direct.amount + rocket.blast.amount, 70)
        # Direct and splash are two admitted pool events, each with armour.
        self.assertAlmostEqual(before - self.health(self.a), 70 * 0.6 - 2 * combat.ARMOUR_BLOCK, places=4)
        self.assertEqual(len(self.b.client.messages(combat.HIT)), 2)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertFalse(self.a.mover.external_impulse)
