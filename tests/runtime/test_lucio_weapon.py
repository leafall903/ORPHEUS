"""Authored Sonic Amplifier consumption/reload with real projectile damage."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_lucio_combat as fixtures
from test_game_combat import aim

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

    def test_death_during_reload_never_refills_the_retired_magazine(self):
        _, run, source, enemy, weapon = self.scenario()
        for _ in range(25):
            run.step({source: (0, 0, FIRE)})
        run.step({source: (0, 0, RELOAD)})
        for _ in range(50):
            run.step({source: (0, 0, FIRE | RELOAD)})
        self.assertEqual(weapon.vars[53].value(), 16)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is source)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
            for _ in range(100):
                run.step({source: (0, 0, FIRE | RELOAD)})
        self.assertTrue(fight.is_dead(source))
        self.assertEqual(len(fired.call_args_list), 0)
        self.assertEqual(weapon.vars[53].value(), 16)

    def test_body_replacement_during_reload_drops_old_timer_and_starts_full(self):
        _, run, source, _, weapon = self.scenario()
        for _ in range(25):
            run.step({source: (0, 0, FIRE)})
        run.step({source: (0, 0, RELOAD)})
        old_body = source.body
        run.match.switch_hero(source, source.hero)
        self.assertNotEqual(source.body, old_body)
        current = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x776)
        self.assertEqual(current.vars[53].value(), 20)
        for _ in range(130):
            run.step({})
        self.assertEqual(current.vars[53].value(), 20)
        self.assertEqual(weapon.vars[53].value(), 16)

    def test_primary_body_damage_has_no_falloff_and_stops_at_authored_reach(self):
        for distance in (4, 50, 99, 101):
            with self.subTest(distance=distance):
                helper, run, source, enemy, weapon = self.scenario()
                enemy.mover.position[:] = (0, 1, distance)
                yaw, pitch = aim(run.match.combat._eye(source), (0, 1.8, distance))
                run.step({source: (yaw, pitch, FIRE)})
                for _ in range(160):
                    run.step({})
                self.assertEqual(helper.health(run, enemy), 5000 - (80 if distance < 100 else 0))
                self.assertEqual(weapon.vars[53].value(), 16)
                self.assertFalse(run.match.combat.rockets)

    def test_primary_terminal_sweep_includes_exact_capsule_contact(self):
        # The integrated sweep includes 0776's authored 0.15 projectile extent
        # in addition to this target's 0.5 capsule radius and 100-unit reach.
        # Select adjacent representable poses across the contact boundary.
        contact_distance = int((100 + 0.5 + 0.15) * 1024) / 1024
        for distance, expected_damage in ((contact_distance, 80), (contact_distance + 1 / 1024, 0)):
            with self.subTest(distance=distance):
                helper, run, source, enemy, weapon = self.scenario()
                enemy.mover.position[:] = (0, 1, distance)
                run.step({source: (0, 0, FIRE)})
                for _ in range(160):
                    run.step({})
                self.assertEqual(helper.health(run, enemy), 5000 - expected_damage)
                self.assertEqual(weapon.vars[53].value(), 16)
                self.assertFalse(run.match.combat.rockets)

    def test_primary_same_team_player_is_excluded_except_in_free_for_all(self):
        for free_for_all in (False, True):
            with self.subTest(free_for_all=free_for_all):
                helper, run, source, enemy, weapon = self.scenario()
                enemy.team = source.team
                run.match.game_map = replace(run.match.game_map, free_for_all=free_for_all)
                yaw, pitch = aim(run.match.combat._eye(source), (0, 1.8, 4))
                run.step({source: (yaw, pitch, FIRE)})
                for _ in range(35):
                    run.step({})
                self.assertEqual(helper.health(run, enemy), 4920 if free_for_all else 5000)
                self.assertEqual(weapon.vars[53].value(), 16)
