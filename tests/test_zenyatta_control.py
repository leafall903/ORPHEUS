"""Real Flashbang inputs against Zen's charging weapon and active Transcendence."""

import unittest

import test_mccree_flashbang as flashbang_tests

from ow174.game import combat
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.zenyatta import HERO


class ZenyattaControlTests(unittest.TestCase):
    def arena(self):
        helper = flashbang_tests.FlashbangTests()
        run, caster, zen = helper.arena(distance=3)
        run.match.switch_hero(zen, heroes()[HERO])
        x, y, z = caster.mover.position
        zen.mover.position = [x, y, z+3]
        zen.mover.body = None
        helper.step(run, caster, zen)
        return helper, run, caster, zen

    def test_actual_flashbang_cannot_interrupt_damage_invulnerable_transcendence(self):
        helper, run, caster, zen = self.arena()
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        helper.step(run, caster, zen, target_buttons=0x20)
        fight = run.match.combat
        self.assertTrue(fight.zenyatta.ultimate(zen))
        helper.step(run, caster, zen, cast=0x10)
        for _ in range(60):
            helper.step(run, caster, zen)
            self.assertFalse(fight.is_stunned(zen))
            self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(helper.health(run, zen), 200)
        self.assertFalse(caster.client.messages(combat.HIT))

    def test_actual_flashbang_cancels_charged_volley_and_weapon_recovers_after_expiry(self):
        helper, run, caster, zen = self.arena()
        for _ in range(90):
            helper.step(run, caster, zen, target_buttons=SECONDARY)
        helper.cast(run, caster, zen, target_buttons=SECONDARY)
        fight = run.match.combat
        self.assertEqual(helper.health(run, zen), 175)
        before = helper.health(run, caster)
        end = fight.stuns[zen.body]
        while run.frame < end-1:
            helper.step(run, caster, zen, target_buttons=FIRE | SECONDARY)
            self.assertEqual(helper.health(run, caster), before)
            self.assertFalse(any(r.shooter is zen for r in fight.rockets))
        helper.step(run, caster, zen)
        helper.step(run, caster, zen)
        helper.step(run, caster, zen, target_buttons=FIRE)
        for _ in range(20):
            helper.step(run, caster, zen)
        self.assertFalse(fight.is_stunned(zen))
        self.assertLess(helper.health(run, caster), before)


if __name__ == '__main__':
    unittest.main()
