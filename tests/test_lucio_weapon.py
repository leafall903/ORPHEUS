"""Authored Sonic Amplifier consumption/reload with real projectile damage."""

import unittest
from unittest.mock import patch

import test_lucio_combat as fixtures

from ow174.game import combat
from ow174.game.commands import FIRE, Command

RELOAD = 0x400  # logical14, from the actual0776 graph and driver input map


class SonicAmplifierTests(unittest.TestCase):
    def scenario(self):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        ally.mover.position[:] = (10, 1, 4)
        # A declared high-health player target keeps the full magazine/reload
        # observation alive; this is not a claim about Soldier's native health.
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 5000)
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x776)
        return helper, run, source, enemy, weapon

    def test_minimum_four_shot_burst_survives_early_release(self):
        helper, run, source, enemy, weapon = self.scenario()
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
            run.step({source: (0, 0, FIRE)})
            for _ in range(35):
                run.step({})
        shots = [call.args for call in fired.call_args_list]
        self.assertEqual([row[3] for row in shots], [0, 1, 2, 3])
        self.assertEqual([shots[i + 1][4] - shots[i][4] for i in range(3)], [125, 125, 125])
        self.assertEqual(weapon.vars[53].value(), 16)
        self.assertEqual(helper.health(run, enemy), 4920)

    def test_empty_magazine_triggers_authored_reload_and_resumes_damage(self):
        helper, run, source, enemy, weapon = self.scenario()
        states = {}
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as fired:
            for offset in range(430):
                run.step({source: (0, 0, FIRE)})
                if offset in (259, 300, 362, 363, 387, 388):
                    states[offset] = weapon.vars[53].value()
        shots = [call.args for call in fired.call_args_list]
        self.assertEqual(len(shots), 24)
        self.assertEqual([row[3] for row in shots[:20]], [0, 1, 2, 3] * 5)
        self.assertEqual(states, {259: 0, 300: 0, 362: 0, 363: 20, 387: 20, 388: 19})
        self.assertEqual(helper.health(run, enemy), 5000 - 24 * 20)

    def test_manual_reload_has_fill_and_end_phases_that_cannot_be_bypassed(self):
        _, run, source, _, weapon = self.scenario()
        for _ in range(25):
            run.step({source: (0, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 16)
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as fired:
            run.step({source: (0, 0, RELOAD)})
            for offset in range(1, 127):
                run.step({source: (0, 0, FIRE | (RELOAD if offset % 2 == 0 else 0))})
                if offset == 102:
                    self.assertEqual(weapon.vars[53].value(), 20)
                self.assertEqual(len(fired.call_args_list), 0)
            run.step({source: (0, 0, FIRE)})
            self.assertEqual(len(fired.call_args_list), 1)

    def test_partial_magazine_never_reports_more_shots_than_available(self):
        helper, run, source, enemy, weapon = self.scenario()
        weapon.write((0, 53), 2)  # explicit resource precondition
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as fired:
            for _ in range(35):
                run.step({source: (0, 0, FIRE)})
        self.assertEqual(len(fired.call_args_list), 2)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(helper.health(run, enemy), 4960)

    def test_disabled_reload_leaves_empty_weapon_without_fabricated_shots(self):
        _, run, source, _, weapon = self.scenario()
        # The instance slot is overridden by a Stack reading the entity input.
        weapon.write((1, 31196), False)
        run.step({})  # process the authored Stack's dependency event
        self.assertIs(weapon.vars[31196].value(), False)
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as fired:
            for _ in range(430):
                run.step({source: (0, 0, FIRE | RELOAD)})
        self.assertEqual(len(fired.call_args_list), 20)
        self.assertEqual(weapon.vars[53].value(), 0)

    def test_replayed_command_cannot_duplicate_ammo_or_damage(self):
        helper, run, source, enemy, weapon = self.scenario()
        run.step({source: (0, 0, FIRE)})
        for _ in range(5):
            source.take_commands([Command(run.frame, buttons=FIRE)])
        self.assertEqual(weapon.vars[53].value(), 19)
        for _ in range(35):
            run.step({})
        self.assertEqual(helper.health(run, enemy), 4920)

    def test_death_stops_remaining_burst_but_preserves_in_flight_impact(self):
        helper, run, source, enemy, _ = self.scenario()
        run.step({source: (0, 0, FIRE)})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is source)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        for _ in range(35):
            run.step({source: (0, 0, FIRE | RELOAD)})
        self.assertTrue(fight.is_dead(source))
        self.assertEqual(helper.health(run, enemy), 4980)
