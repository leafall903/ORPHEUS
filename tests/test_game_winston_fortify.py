"""Actual Orisa Fortify against Winston's authored damage and impulses."""

import unittest

import test_game_winston_wraith as fixtures

from ow174.game import combat, protections
from ow174.game.content import heroes


class WinstonFortifyTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonWraithTests.move)
    health = fixtures.WinstonWraithTests.health
    step = fixtures.WinstonWraithTests.step
    rage = fixtures.WinstonWraithTests.rage

    def setUp(self):
        fixtures.WinstonWraithTests.setUp(self)
        self.orisa = self.reaper
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[protections.ORISA]))
        self.move(self.orisa, (0, 1, 1.5))

    def fortify(self):
        self.step(defense=8)
        self.assertTrue(protections.force_immune(self.orisa))
        self.assertAlmostEqual(protections.incoming_scale(self.orisa), .6)

    def test_tesla_damage_and_charge_use_fortify_before_armour(self):
        self.fortify()
        before = self.health(self.orisa)
        self.step(1)
        self.assertAlmostEqual(before - self.health(self.orisa), .96 * .6 * .8)
        charge = self.owner.body_script.component.entity_var(342).value()
        self.assertAlmostEqual(charge, before - self.health(self.orisa))

    def test_melee_and_primal_damage_reduced_and_primal_impulse_rejected(self):
        self.fortify()
        before = self.health(self.orisa)
        self.step(0x800)
        for _ in range(22):
            self.step()
        self.assertAlmostEqual(before - self.health(self.orisa), 13, places=5)
        self.rage()
        before = self.health(self.orisa)
        for _ in range(70):
            self.step(1)
            if self.health(self.orisa) < before:
                break
        self.assertAlmostEqual(before - self.health(self.orisa), 19, places=5)
        self.assertEqual(tuple(self.orisa.mover.position), (0, 1, 1.5))
        self.assertEqual(self.orisa.mover.velocity, (0, 0, 0))

    def test_jump_takeoff_and_landing_reduce_damage_without_displacement(self):
        self.fortify()
        before = self.health(self.orisa)
        self.step(8, pitch=-16384)
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertAlmostEqual(before - self.health(self.orisa), .3)
        self.assertEqual(self.orisa.mover.velocity, (0, 0, 0))
        self.move(self.orisa, (0, 1, 0))
        before = self.health(self.orisa)
        for _ in range(250):
            self.step()
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertTrue(protections.force_immune(self.orisa))
        self.assertAlmostEqual(before - self.health(self.orisa), 25, places=5)
        self.assertEqual(tuple(self.orisa.mover.position), (0, 1, 0))

    def test_idle_expiry_restores_damage_and_early_press_cannot_recast(self):
        self.fortify()
        self.run.frame += 253
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(protections.force_immune(self.orisa))
        self.assertEqual(protections.incoming_scale(self.orisa), 1)
        before = self.health(self.orisa)
        self.step(1, defense=8)
        self.assertFalse(protections.force_immune(self.orisa))
        self.assertAlmostEqual(before - self.health(self.orisa), .96 * .8)

    def test_actual_flashbang_deals_reduced_damage_without_stun(self):
        self.assertTrue(self.match.switch_hero(self.owner, heroes()[0x02E0000000000042]))
        self.move(self.owner, (0, 1, 0))
        self.fortify()
        before = self.health(self.orisa)
        self.step(16)
        for _ in range(30):
            self.step()
            if self.health(self.orisa) < before:
                break
        self.assertLess(self.health(self.orisa), before)
        self.assertFalse(self.fight.is_stunned(self.orisa))
        self.assertTrue(protections.force_immune(self.orisa))
