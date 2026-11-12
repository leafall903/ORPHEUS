"""Executed effective-health charge policy and native cost/cast/death lifecycle."""

import unittest

import test_game_ana as rifle_fixture
import test_game_ana_nano as nano_fixture
from test_game_combat import aim

from ow174.game.content import find_hero


class AnaChargeTests(unittest.TestCase):
    def fight(self, *, ally=False, distance=6):
        run, source, target, entry = rifle_fixture.AnaRifleTests().setup_fight(
            enemy=not ally, distance=distance)
        source.body_script.ult_seconds = 0  # isolate combat credit from passive filler
        source.body_script.set_ult_charge(0)
        return run, source, target, entry

    def test_actual_rifle_damage_gives_effective_charge_and_overkill_cannot_farm(self):
        for health, expected in ((200, 70), (20, 20)):
            with self.subTest(health=health):
                run, source, _target, entry = self.fight()
                vitals = run.match.combat.vitals_of(entry)
                vitals.damage(200 - health)
                rifle_fixture.AnaRifleTests().shoot(run, source, entry, False)
                self.assertEqual(source.body_script.component.vars[342].value(), expected)

    def test_actual_rifle_healing_clamps_charge_to_effective_healing(self):
        for missing, expected in ((100, 70), (10, 10), (0, 0)):
            with self.subTest(missing=missing):
                run, source, _target, entry = self.fight(ally=True)
                run.match.combat.vitals_of(entry).damage(missing)
                rifle_fixture.AnaRifleTests().shoot(run, source, entry, False)
                self.assertEqual(source.body_script.component.vars[342].value(), expected)

    def test_grenade_self_healing_and_enemy_damage_credit_effective_values(self):
        for self_heal in (False, True):
            with self.subTest(self_heal=self_heal):
                run, source, _target, entry = self.fight(ally=self_heal, distance=2.5 if self_heal else 6)
                fight = run.match.combat
                if self_heal:
                    own = next(t for t in fight._targets() if t.owner is source)
                    fight.vitals_of(own).damage(50)
                direction = aim(fight._eye(source), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                run.step({source: (*direction, 16)})
                for _ in range(75):
                    run.step({})
                self.assertEqual(source.body_script.component.vars[342].value(), 50)

    def test_effective_damage_can_fill_native_cost_and_enable_actual_nano_cast(self):
        fixture = nano_fixture.AnaNanoTests()
        run, source, ally, enemy = fixture.fight()
        cost = source.body_script.component.vars[338].value()
        source.body_script.set_ult_charge(cost - 70)
        fixture.single_shot(run, source, enemy)
        self.assertEqual(source.body_script.component.vars[342].value(), cost)
        fixture.cast(run, source, ally, charged=False)  # no direct charge write in this cast
        self.assertEqual(source.body_script.component.vars[342].value(), 0)
        vitals = run.match.combat.vitals_of(fixture.target(run.match.combat, ally))
        self.assertGreater(vitals.nano_until, run.match.combat.now)

    def test_nano_healing_during_activation_does_not_refund_charge(self):
        fixture = nano_fixture.AnaNanoTests()
        run, source, ally, enemy = fixture.fight(ally_hero="Roadhog")
        fight = run.match.combat
        fight.vitals_of(fixture.target(fight, ally)).damage(400)
        fixture.cast(run, source, ally)
        self.assertEqual(source.body_script.component.vars[342].value(), 0)
        for _ in range(90):
            run.step({})
        fixture.single_shot(run, source, enemy)
        self.assertEqual(source.body_script.component.vars[342].value(), 70)

    def test_no_overfill_invalid_credit_or_credit_after_death(self):
        run, source, target, entry = self.fight()
        script = source.body_script
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost - 10)
        rifle_fixture.AnaRifleTests().shoot(run, source, entry, False)
        self.assertEqual(script.component.vars[342].value(), cost)
        for amount in (1e20, -1, float("nan"), float("inf"), True):
            self.assertEqual(script.gain_ult_charge(amount), 0)
            self.assertEqual(script.component.vars[342].value(), cost)
        script.set_ult_charge(123)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        fight._damage(target, own, 10000, False, None, run.frame)
        self.assertEqual(script.gain_ult_charge(100), 0)
        self.assertEqual(script.component.vars[342].value(), 123)

    def test_ana_respawn_keeps_unspent_charge_but_hero_change_resets_it(self):
        run, source, target, _entry = self.fight()
        fight = run.match.combat
        source.body_script.set_ult_charge(123)
        old_body = source.body
        own = next(t for t in fight._targets() if t.owner is source)
        fight._damage(target, own, 10000, False, None, run.frame)
        run.frame += 250
        run.match.update(run.frame * 0.016, run.frame, frames=False)
        self.assertNotEqual(source.body, old_body)
        self.assertFalse(fight.is_dead(source))
        self.assertEqual(source.body_script.component.vars[342].value(), 123)
        self.assertTrue(run.match.switch_hero(source, find_hero("Reaper")))
        self.assertEqual(source.body_script.component.vars[342].value(), 0)


if __name__ == "__main__":
    unittest.main()
