"""Effective native healing is charged once before1544's positive-delta bonus."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_echo_duplicate import EchoDuplicateTests

from ow174.game.script.driver import ULT_CHARGE, ULT_COST

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/echo-copied-native-heal-charge-v1.json').read_text())


class EchoCopiedNativeHealChargeTests(unittest.TestCase):
    def session(self):
        fixture = EchoDuplicateTests()
        run, player, enemy, _, angles = fixture.session()
        fixture.start(run, player, angles)
        player.body_script.ult_seconds = 0
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, CASE['input']['missing_health'], False, None, run.frame)
        player.body_script.set_ult_charge(0)
        return run, player, target

    @staticmethod
    def charge(player):
        return player.body_script.component.entity_var(ULT_CHARGE).value()

    def test_effective_native_heal_and_overheal_receive_one_original_bonus(self):
        run, player, target = self.session()
        script = player.body_script
        self.assertEqual(script.ult_gain_bonus, CASE['expected']['copy_bonus'])
        with patch.object(script, 'gain_ult', wraps=script.gain_ult) as legacy:
            given = run.match.combat._heal_target(
                player, target, CASE['input']['requested_heal'], source_entity=player.body)
            self.assertEqual(given, CASE['expected']['effective_heal'])
            self.assertAlmostEqual(self.charge(player), CASE['expected']['total_charge'], delta=.002)
            before = self.charge(player)
            given = run.match.combat._heal_target(
                player, target, CASE['input']['requested_heal'], source_entity=player.body)
            self.assertEqual(given, 0)
            self.assertEqual(self.charge(player)-before, CASE['expected']['overheal_charge'])
        self.assertEqual(legacy.call_count, 0)

    def test_near_cap_bonus_uses_native_awarded_delta(self):
        run, player, target = self.session()
        script = player.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost-CASE['input']['near_cap_missing_charge'])
        manager = run.match.combat.echo_duplicates
        original = manager.credit_native_healing
        deltas = []

        def observe(owner, before, **kwargs):
            deltas.append(self.charge(owner)-before)
            return original(owner, before, **kwargs)

        with patch.object(manager, 'credit_native_healing', side_effect=observe):
            run.match.combat._heal_target(player, target, 10, source_entity=player.body)
        self.assertEqual(deltas, [CASE['input']['near_cap_missing_charge']])
        self.assertEqual(self.charge(player), cost)

    def test_copied_body_source_cannot_credit_returned_echo(self):
        run, player, _ = self.session()
        fight = run.match.combat
        old = player.body
        self.assertTrue(fight.echo_duplicates.restore(player))
        self.assertNotEqual(old, player.body)
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        vitals.heal(vitals.maximum)
        enemy = next(p for p in run.players if p is not player)
        fight._damage(enemy, target, 10, False, None, run.frame)
        player.body_script.set_ult_charge(0)
        self.assertGreater(fight._heal_target(player, target, 10, source_entity=old), 0)
        self.assertEqual(self.charge(player), CASE['expected']['retired_source_charge'])


if __name__ == '__main__':
    unittest.main()
