"""Actual Sigma input through the scoped damage-based ultimate economy."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_sigma_combat as fixtures
import test_sigma_flux_placement as placement
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import absorption, combat, projectiles, sigma_charge, sigma_flux, stats
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class SigmaUltEconomyTests(unittest.TestCase):
    def arena(self):
        run, shooter, enemy, vitals = fixtures.SigmaCombatTests().arena(offset=0, distance=6)
        script = shooter.body_script
        script.ult_seconds = 0  # isolate actual hit awards from the passive host clock
        script.set_ult_charge(0)
        return run, shooter, enemy, vitals, script

    @staticmethod
    def charge(script):
        return script.component.entity_var(342).value()

    def test_match_passive_clock_combines_with_hits_and_opens_the_flux_gate(self):
        run, shooter, enemy, vitals = fixtures.SigmaCombatTests().arena(offset=0, distance=6)
        script = shooter.body_script
        script.set_ult_charge(0)
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1960)
        for _ in range(125):
            run.step({})
        self.assertAlmostEqual(self.charge(script), 10, places=4)

        fixtures.SigmaCombatTests().pair(run, shooter, enemy)
        self.assertEqual(vitals.total, 90)
        self.assertAlmostEqual(self.charge(script), 10 + 110 + 61 * 0.08, places=3)

        ability = fixtures.SigmaCombatTests().ability(shooter, sigma_flux.FLUX)
        script.set_ult_charge(cost - 0.2)
        run.step({shooter: (0, 0, 0x20)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertLess(self.charge(script), cost)
        run.step({})
        self.assertLess(self.charge(script), cost)
        run.step({})
        self.assertEqual(self.charge(script), cost)
        run.step({shooter: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(20):
            run.step({})
        self.assertEqual(self.charge(script), 0)

    def test_enemy_biotic_field_restores_damage_credit_without_healing_sigma(self):
        run = Run(KANEZAKA_DM, ("Sigma", "Target", "Healer"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter, enemy, healer = run.players
        shooter.team, enemy.team, healer.team = 0, 1, 1
        run.match.switch_hero(shooter, heroes()[sigma_charge.HERO])
        x, y, z = shooter.mover.position
        enemy.mover.position = [x, y, z + 6]
        healer.mover.position = [x + 3.5, y, z + 3]
        fight = run.match.combat
        vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        vitals[shooter].now[combat.HEALTH_POOL] = 250
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        fixture = fixtures.SigmaCombatTests()

        fixture.pair(run, shooter, enemy)
        self.assertEqual(vitals[enemy].total, 90)
        self.assertEqual(vitals[healer].total, 200)
        self.assertEqual(self.charge(script), 110)
        run.step({healer: (0, 0, 0x10)})  # Biotic Field
        for _ in range(80):
            run.step({})
            if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                break
        field, = (shot for shot in fight.projectiles.shots if shot.field_tick is not None)
        centre = projectiles.field_centre(field)
        self.assertLess(math.dist(centre, fight._eye(shooter)), projectiles.HEAL_RADIUS)
        self.assertLess(math.dist(centre, fight._eye(enemy)), projectiles.HEAL_RADIUS)
        for _ in range(420):
            run.step({})
            self.assertEqual(vitals[shooter].total, 350)
            self.assertEqual(self.charge(script), 110)
        self.assertEqual(vitals[enemy].total, 200)
        self.assertAlmostEqual(healer.stats.value(stats.HEALING), 110)
        self.assertFalse(fight.projectiles.shots)

        fixture.pair(run, shooter, enemy)
        self.assertEqual(vitals[enemy].total, 90)
        self.assertEqual(self.charge(script), 220)

    def test_effective_base_damage_excludes_temporary_pool_loss(self):
        run, shooter, enemy, vitals, script = self.arena()
        vitals.grant_temporary_shields(absorption.SHIELD_TAG, 60, 400)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
        run.step({shooter: (yaw, pitch, 1)})
        first_ball = False
        for _ in range(60):
            run.step({shooter: (yaw, pitch, 0)})
            if vitals.total == 205:
                first_ball = True
                self.assertEqual(self.charge(script), 0)
        self.assertTrue(first_ball)
        self.assertEqual(vitals.total, 150)
        self.assertEqual(self.charge(script), 50)

    def test_last_hypersphere_restarts_shield_recovery_and_recovered_pool_can_charge_flux(self):
        run, shooter, enemy, _, script = self.arena()
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[sigma_charge.HERO])
        enemy.mover.position = position
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is enemy))
        yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
        shield = combat.SHIELDS_POOL
        health = combat.HEALTH_POOL
        hit_frames = []
        for frame in range(60):
            before = vitals.total
            run.step({shooter: (yaw, pitch, 1 if frame == 0 else 0)})
            if vitals.total < before:
                self.assertEqual(before - vitals.total, 55)
                hit_frames.append(run.frame)
        first, last = hit_frames
        self.assertGreater(last, first)
        self.assertEqual(vitals.now[shield], 0)
        self.assertEqual(vitals.now[health], 290)
        self.assertEqual(self.charge(script), 110)
        while run.frame < last + 187:
            run.step({})
            self.assertEqual(vitals.now[shield], 0)
        run.step({})
        self.assertAlmostEqual(vitals.now[shield], 0.24)
        for _ in range(220):
            run.step({})
        self.assertEqual(vitals.now[shield], 100)
        self.assertEqual(vitals.now[health], 290)
        self.assertEqual(self.charge(script), 110)
        fixtures.SigmaCombatTests().pair(run, shooter, enemy)
        self.assertEqual(vitals.now[shield], 0)
        self.assertEqual(vitals.now[health], 280)
        self.assertEqual(self.charge(script), 220)

    def test_temporary_only_hit_restarts_ordinary_recovery_without_flux_credit(self):
        run, shooter, enemy, _, script = self.arena()
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[sigma_charge.HERO])
        enemy.mover.position = position
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is enemy))
        fixture = fixtures.SigmaCombatTests()
        fixture.pair(run, shooter, enemy)
        self.assertEqual(self.charge(script), 110)
        for _ in range(65):
            run.step({})
        toward_attacker = aim(fight._eye(enemy), fight._eye(shooter))
        run.step({enemy: (*toward_attacker, 0x08)})
        toward_defender = aim(fight._eye(shooter), fight._eye(enemy))
        for frame in range(61):
            run.step({shooter: (*toward_defender, 1 if frame == 0 else 0),
                      enemy: (*toward_attacker, 0)})
        for _ in range(75):
            run.step({})
        temporary = vitals.tagged_shields[absorption.SHIELD_TAG]
        self.assertEqual(temporary.now, 66)
        toward_defender = aim(fight._eye(shooter), fight._eye(enemy))
        shield = combat.SHIELDS_POOL
        run.step({shooter: (*toward_defender, 1)})
        for _ in range(30):
            if any(rocket.shooter is shooter for rocket in fight.rockets):
                break
            run.step({shooter: (*toward_defender, 0)})
        self.assertTrue(any(rocket.shooter is shooter for rocket in fight.rockets))
        # Aim the second sphere away from the defender once the first is in flight.
        for _ in range(30):
            run.step({shooter: (-32768, 0, 0)})
            if temporary.now < 66:
                break
        self.assertEqual(temporary.now, 11)
        at_contact = vitals.now[shield]
        self.assertGreater(at_contact, 0)
        self.assertLess(at_contact, 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 290)
        self.assertEqual(self.charge(script), 110)
        for _ in range(187):
            run.step({shooter: (-32768, 0, 0)})
            self.assertEqual(vitals.now[shield], at_contact)
        run.step({})
        self.assertAlmostEqual(vitals.now[shield] - at_contact, 0.24)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 290)
        self.assertEqual(self.charge(script), 110)

    def test_overkill_only_credits_available_base_pool(self):
        run, shooter, enemy, vitals, script = self.arena()
        vitals.now[combat.HEALTH_POOL] = 35
        fixtures.SigmaCombatTests().pair(run, shooter, enemy)
        self.assertTrue(vitals.dead)
        self.assertEqual(self.charge(script), 35)

    def test_actual_damage_reaches_cap_then_flux_spends_without_self_charge(self):
        run, shooter, enemy, vitals, script = self.arena()
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1960)
        script.set_ult_charge(cost - 60)
        fixtures.SigmaCombatTests().pair(run, shooter, enemy)
        self.assertEqual(vitals.total, 90)
        self.assertEqual(self.charge(script), cost)
        ability = fixtures.SigmaCombatTests().ability(shooter, sigma_flux.FLUX)
        placement.SigmaFluxPlacementTests().select(run, shooter, tuple(enemy.mover.position))
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(script), 0)
        for _ in range(380):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(script), 0)

    def test_in_flight_hyperspheres_do_not_charge_running_flux(self):
        run, shooter, enemy, vitals = fixtures.SigmaCombatTests().arena(offset=0, distance=20)
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
        run.step({shooter: (yaw, pitch, 1)})
        for _ in range(15):
            run.step({shooter: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 200)
        self.assertTrue(fight.rockets)
        run.step({shooter: (yaw, pitch, 0x20)})
        ability = fixtures.SigmaCombatTests().ability(shooter, sigma_flux.FLUX)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(45):
            run.step({})
        self.assertEqual(vitals.total, 90)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(script), 0)

    def test_hero_replacement_retires_the_earned_charge(self):
        run, shooter, enemy, _vitals, script = self.arena()
        fixtures.SigmaCombatTests().pair(run, shooter, enemy)
        self.assertEqual(self.charge(script), 110)
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        run.match.switch_hero(shooter, heroes()[sigma_charge.HERO])
        self.assertIsNot(shooter.body_script, script)
        self.assertEqual(self.charge(shooter.body_script), 0)

    def test_death_respawn_preserves_unspent_charge_without_refunding_flux(self):
        for cast_flux in (False, True):
            with self.subTest(cast_flux=cast_flux):
                run, shooter, enemy, _, script = self.arena()
                fixtures.SigmaCombatTests().pair(run, shooter, enemy)
                self.assertEqual(self.charge(script), 110)
                if cast_flux:
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({shooter: (0, 0, 0x20)})
                    ability = fixtures.SigmaCombatTests().ability(shooter, sigma_flux.FLUX)
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(self.charge(script), 0)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is shooter)
                vitals = fight.vitals_of(target)
                vitals.now[combat.HEALTH_POOL] = 15
                vitals.now[combat.SHIELDS_POOL] = 0
                x, y, z = shooter.mover.position
                yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
                run.step({enemy: (yaw, pitch, 1)})
                self.assertTrue(fight.is_dead(shooter))
                self.assertEqual(vitals.killer, enemy.body)
                expected = 0 if cast_flux else 110
                self.assertEqual(self.charge(script), expected)
                deadline, = (when for when, who in fight.respawns if who is shooter)
                old_body = shooter.body
                frame = math.ceil(deadline / combat.TICK)
                if not cast_flux:
                    with patch.object(run.match, "_next_body_id", return_value=None):
                        run.match.update(frame * combat.TICK, frame)
                    self.assertEqual(shooter.body, old_body)
                    self.assertEqual(self.charge(script), expected)
                    retry, = (when for when, who in fight.respawns if who is shooter)
                    frame = math.ceil(retry / combat.TICK)
                run.match.update(frame * combat.TICK, frame)
                self.assertNotEqual(shooter.body, old_body)
                self.assertFalse(fight.is_dead(shooter))
                self.assertIsNot(shooter.body_script, script)
                self.assertEqual(self.charge(shooter.body_script), expected)
