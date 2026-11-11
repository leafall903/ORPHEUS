"""Actual Zarya projectiles/melee against reconstructed authored Genji defenses."""

import os
import unittest
from unittest.mock import patch

import test_game_zarya_absorption as absorption
from test_game_combat import Run, aim

from ow174.game import combat, interception, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires external projectile tags")
class ZaryaDeflectionTests(unittest.TestCase):
    advance = staticmethod(absorption.ZaryaAbsorptionTests.advance)
    health = staticmethod(absorption.ZaryaAbsorptionTests.health)
    shoot = absorption.ZaryaAbsorptionTests.shoot

    def scene(self, *, facing=-32768, close=False, ally=False, nearer=False, offset=0, recipient=False):
        run = Run(names=("Zarya", "Genji", "Victim"))
        a, b, c = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(a, heroes()[0x02E0000000000068])
        run.match.switch_hero(b, heroes()[0x02E0000000000029])
        a.team, b.team, c.team = 0, 0 if ally else 1, 1
        a.mover.position[:] = [0, 1, 3.5 if close else 0]
        b.mover.position[:] = [offset, 1, 5]
        c.mover.position[:] = [0 if nearer else 10, 1, 2 if nearer else 5]
        if recipient:
            a.mover.position[:] = [0, 1, -3]
            c.mover.position[:] = [0, 1, 0]
            c.team = a.team
        for frame in range(5):
            run.step({b: (facing, 0, 16 if frame == 0 else 0)})
        self.assertTrue(self.instance(b).state(10).active)
        return run, a, b, c

    @staticmethod
    def instance(player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x03A3)

    def until_reflected(self, run, b):
        for _ in range(40):
            self.advance(run, 1)
            found = next((r for r in run.match.combat.rockets if r.shooter is b), None)
            if found is not None:
                return found
        self.fail("actual physical projectile did not transfer authority")

    def test_secondary_returns_47_with_deflector_credit_and_authored_event(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 200)
        self.assertEqual(self.health(run, a), 353)
        self.assertEqual(b.stats.values[stats.HERO_DAMAGE], 47)
        self.assertEqual(a.stats.values.get(stats.HERO_DAMAGE, 0), 0)
        self.assertEqual(self.instance(b).vars[6584].value(), 47)
        self.assertFalse(a.client.messages(combat.HIT))
        self.assertTrue(b.client.messages(combat.HIT))

    def test_sparse_contacts_respect_deflect_already_ended_on_owner_vm(self):
        for attack, damage in ((0x800, 30), (2, 47)):
            for cancelled, late in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(attack=attack, cancelled=cancelled, late=late):
                    run, zarya, genji, _ = self.scene(close=True)
                    match = run.match
                    fight = match.combat
                    start = run.frame
                    expiry = next(interception.timed_expiries(fight))[3]
                    deadline = start + 20 if cancelled else expiry + 1
                    close = deadline if cancelled else deadline + 20
                    contact = deadline + int(cancelled) if late else deadline - (4 if attack == 2 else 1)
                    inputs = ((close, genji, Command(close, yaw=-32768, buttons=16 if cancelled else 0)),
                              (contact, zarya, Command(start + 1, buttons=attack)))
                    for tick, owner, command in sorted(inputs, key=lambda event: event[0]):
                        match.tick = tick
                        owner.take_commands([command])
                    launched = tuple(fight.rockets)
                    self.assertFalse(self.instance(genji).states[10].active)
                    self.assertEqual(fight.tick, start)
                    end = max(close, contact) + 40
                    match.update(end * combat.TICK, end, frames=False)
                    if attack == 2:
                        rocket, = launched
                        self.assertIs(rocket.shooter, zarya if late else genji)
                        if not late:
                            self.assertEqual(self.instance(genji).vars[6584].value(), 47)
                    self.assertEqual(self.health(run, genji), 200 - damage if late else 200)
                    self.assertNotIn(genji.body, fight.projectile_defense_windows)

    def test_sparse_rear_flash_closes_deflect_before_later_contacts(self):
        for attack, damage in ((2, 47), (0x800, 30)):
            for late in (False, True):
                with self.subTest(attack=attack, late=late):
                    run, caster, genji, flash = self.scene(close=True)
                    match, fight = run.match, run.match.combat
                    self.assertTrue(match.switch_hero(flash, heroes()[0x02E0000000000042]))
                    flash.team = caster.team
                    flash.mover.position[:] = (0, 1, 8)
                    flash.mover.body = None
                    start = run.frame
                    inputs = (
                        (start + (55 if late else 2), caster, Command(start + 1, buttons=attack)),
                        (start + 18, flash, Command(start + 1, yaw=-32768, buttons=16)),
                    )
                    for at, owner, command in sorted(inputs, key=lambda event: event[0]):
                        match.tick = at
                        owner.take_commands([command])
                    end = start + 70
                    match.update(end * combat.TICK, end, frames=False)
                    self.assertTrue(fight.is_stunned(genji))
                    self.assertFalse(self.instance(genji).state(10).active)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(self.health(run, genji), 175 - damage if late else 175)
                    if attack == 2 and late:
                        retry = fight.control_deadline(genji.body) + 1
                        match.update(retry * combat.TICK, retry, frames=False)
                        genji.take_commands([Command(retry, yaw=-32768, buttons=16)])
                        self.assertFalse(self.instance(genji).state(10).active)
                        self.assertGreater(self.instance(genji).vars[61].value(), 0)

    def test_sparse_flash_admits_deflect_only_after_recovery(self):
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                run, caster, genji, flash = self.scene(close=True)
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(genji, genji.hero))
                genji.mover.position[:] = (0, 1, 5)
                genji.mover.body = None
                self.advance(run, 5, b=genji)
                start = run.frame
                self.assertFalse(self.instance(genji).state(10).active)
                self.assertTrue(match.switch_hero(flash, heroes()[0x02E0000000000042]))
                flash.team = caster.team
                flash.mover.position[:] = (0, 1, 8)
                flash.mover.body = None
                match.tick = start + 1
                flash.take_commands([Command(match.tick, yaw=-32768, buttons=16)])
                match.tick = start + (90 if recovered else 35)
                genji.take_commands([Command(match.tick, yaw=-32768, buttons=16)])
                self.assertTrue(self.instance(genji).state(10).active)
                match.tick = start + 95
                caster.take_commands([Command(match.tick, buttons=2)])
                end = start + 113
                match.update(end * combat.TICK, end, frames=False)
                self.assertEqual(self.instance(genji).state(10).active, recovered)
                self.assertEqual(self.health(run, genji), 175 if recovered else 128)
                if not recovered:
                    for tick, buttons in ((end + 1, 0), (end + 2, 16)):
                        match.update(tick * combat.TICK, tick, frames=False)
                        genji.take_commands([Command(tick, yaw=-32768, buttons=buttons)])
                    self.assertTrue(self.instance(genji).state(10).active)

    def test_sparse_sleep_admits_deflect_only_after_damage_wakes_genji(self):
        for woken in (False, True):
            with self.subTest(woken=woken):
                run, caster, genji, ana = self.scene(close=True)
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(genji, genji.hero))
                self.assertFalse(self.instance(genji).state(10).active)
                end = absorption.queue_sleep_wake_and_defense(
                    self, run, caster, genji, ana, wake_offset=12 if woken else 35, defense_button=16,
                )
                self.assertTrue(self.instance(genji).state(10).active)
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.is_stunned(genji))
                self.assertEqual(self.health(run, genji), 165 if woken else 118)
                self.assertEqual(self.instance(genji).state(10).active, woken)
                if not woken:
                    match.update((end + 1) * combat.TICK, end + 1, frames=False)
                    genji.take_commands([Command(end + 1, yaw=-32768, buttons=16)])
                    self.assertTrue(self.instance(genji).state(10).active)

    def test_deflected_halt_pulls_zarya_on_the_reflectors_enemy_team(self):
        definition = 0x040000000000172C
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                self.assertTrue(interception.eligible(definition))
                run, orisa, genji, victim = self.scene(recipient=True)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                self.assertTrue(run.match.switch_hero(victim, heroes()[0x02E0000000000068]))
                orisa.mover.position[:] = [0, 1, -3]
                victim.mover.position[:] = [0, 1, 1]
                orisa.mover.body = victim.mover.body = None
                fight = run.match.combat
                angles = aim(fight._eye(genji), (0, 1, -1))
                halt = next(i for i in orisa.body_script.component.instances.values()
                            if i.graph.index == 0x0CCB)
                returned = None
                pulled = False
                for frame in range(95):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0), genji: (*angles, 0),
                              victim: (0, 0, 8 if shielded and frame == 20 else 0)})
                    for orb in fight.rockets:
                        if orb.definition == definition and orb.shooter is genji:
                            returned = orb
                            self.assertEqual((orb.team, orb.source_entity), (genji.team, genji.body))
                            self.assertIsNone(orb.halt_instance)
                            self.assertIsNone(orb.halt_trigger_tick)
                    pulled |= victim.body in fight.halt_pulls
                    self.assertNotIn(genji.body, fight.halt_pulls)
                    if frame == 25:
                        self.assertGreater(halt.state(2).remaining(), 4)
                self.assertIsNotNone(returned)
                self.assertTrue(returned.direction[2] < 0)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, victim), 400)
                self.assertEqual(pulled, not shielded)
                if shielded:
                    self.assertEqual(victim.mover.position[2], 1)
                    self.assertEqual(fight.zarya_barriers.active[victim.body].remaining, 200)
                    self.assertEqual(expr.to_float(victim.body_script.component.entity_var(87).value()), 0)
                else:
                    self.assertLess(victim.mover.position[2], 0.5)

    def test_reflected_hyperspheres_credit_genji_and_charge_zarya_barrier(self):
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                run, sigma, genji, zarya = self.scene(recipient=True)
                self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
                self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                sigma.mover.position[:] = [2.5, 1, -3]
                zarya.mover.position[:] = [0, 1, 0]
                run.step({zarya: (0, 0, 8 if shielded else 0), genji: (-32768, 0, 0)})
                fight = run.match.combat
                angles = aim(fight._eye(sigma), fight._eye(genji))
                returned = set()
                for frame in range(55):
                    run.step({sigma: (*angles, 1 if frame == 0 else 0), genji: (-32768, 0, 0)})
                    returned.update(r.definition for r in fight.rockets if r.shooter is genji)
                self.assertEqual(returned, {0x0400000000002D35, 0x04000000000032B4})
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, zarya), 400 if shielded else 290)
                self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), 0 if shielded else 110)
                self.assertFalse(sigma.client.messages(combat.HIT))
                if shielded:
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 22)
                    energy = next(i for i in zarya.body_script.component.instances.values()
                                  if i.graph.index == 0x0242)
                    self.assertEqual(energy.vars[392].value(), expr.Entity(genji.body))
                self.assertEqual(self.instance(genji).vars[6584].value(), 55)

    def test_accretion_bypasses_and_interrupts_deflect(self):
        run, sigma, genji, _ = self.scene()
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        sigma.mover.position[:] = [0, 1, 0]
        fight = run.match.combat
        angles = aim(fight._eye(sigma), fight._eye(genji))
        for frame in range(65):
            run.step({sigma: (*angles, 16 if frame == 0 else 0), genji: (-32768, 0, 0)})
            self.assertFalse(any(rocket.shooter is genji for rocket in fight.rockets))
            if fight.is_stunned(genji):
                break
        self.assertEqual(self.health(run, genji), 130)
        self.assertTrue(fight.is_stunned(genji))
        self.assertFalse(self.instance(genji).state(10).active)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_reflected_ana_grenade_applies_damage_and_antiheal_or_is_absorbed_by_zarya(self):
        self.assertTrue(interception.eligible(combat.ANA_GRENADE))
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                run, ana, genji, zarya = self.scene(recipient=True)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                ana.mover.position[:] = [0, 1, -3]
                zarya.mover.position[:] = [2, 1, -1]
                fight = run.match.combat
                angles = aim(fight._eye(ana), fight._eye(genji))
                run.step({ana: (*angles, 16), genji: (-32768, 0, 0),
                          zarya: (0, 0, 8 if shielded else 0)})
                self.until_reflected(run, genji)
                self.advance(run, 25)
                self.assertEqual(self.health(run, ana), 150)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, zarya), 400 if shielded else 350)
                self.assertGreater(fight.vitals[ana.body].healing_block_until, fight.tick)
                self.assertEqual(fight.vitals[zarya.body].healing_block_until > fight.tick, not shielded)
                self.assertEqual(self.instance(genji).vars[6584].value(), 50)
                self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 50 if shielded else 100)
                if shielded:
                    energy = next(i for i in zarya.body_script.component.instances.values()
                                  if i.graph.index == 0x0242)
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 10)
                    self.assertEqual(energy.vars[392].value(), expr.Entity(genji.body))

    def test_reflected_ana_sleep_dart_keeps_its_control_effect(self):
        self.assertTrue(interception.eligible(0x04000000000015E8))
        run, ana, genji, _ = self.scene()
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = [0, 1, 0]
        fight = run.match.combat
        angles = aim(fight._eye(ana), fight._eye(genji))
        run.step({ana: (*angles, 8), genji: (-32768, 0, 0)})
        self.until_reflected(run, genji)
        self.advance(run, 25)
        self.assertEqual(self.health(run, ana), 195)
        self.assertGreater(fight.sleeps[ana.body], fight.tick)
        self.assertFalse(fight.is_stunned(genji))
        self.assertEqual(self.instance(genji).vars[6584].value(), 5)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 5)

    def test_reflected_ana_hip_rifle_binds_damage_to_the_return_target(self):
        run, ana, genji, _ = self.scene()
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = [0, 1, 0]
        fight = run.match.combat
        angles = aim(fight._eye(ana), fight._eye(genji))
        run.step({ana: (*angles, 1), genji: (-32768, 0, 0)})
        returned = self.until_reflected(run, genji)
        self.assertTrue(interception.eligible(returned.definition))
        for _ in range(25):
            self.advance(run, 1)
            if fight.rifle_doses:
                break
        dose, = fight.rifle_doses
        self.assertEqual((dose.source, dose.target, dose.healing), (genji.body, ana.body, False))
        self.advance(run, 40)
        self.assertEqual(self.health(run, ana), 130)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 70)

    def test_graviton_transfers_field_owner_source_team_and_charge_stays_spent(self):
        run, a, b, _ = self.scene()
        a.body_script.ult_seconds = 0
        self.shoot(run, a, ultimate=True)
        for _ in range(80):
            self.advance(run, 1)
            if run.match.combat.zarya_gravitons.fields:
                break
        field, = run.match.combat.zarya_gravitons.fields
        self.assertIs(field.shooter, b)
        self.assertEqual(field.source_entity, b.body)
        self.assertEqual(field.team, b.team)
        self.assertEqual(a.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(self.instance(b).vars[6584].value(), 5)
        self.advance(run, 280)
        self.assertAlmostEqual(self.health(run, a), 375, places=4)
        self.assertEqual(self.health(run, b), 200)

    def test_two_actual_deflects_transfer_damage_and_field_to_second_genji(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run, a, first, second = self.scene()
                self.assertTrue(run.match.switch_hero(second, first.hero))
                second.team = a.team
                second.mover.position[:] = [0, 1, 1.5]
                run.step({first: (-32768, 0, 0), second: (0, 0, 16)})
                self.assertTrue(self.instance(second).state(10).active)
                a.body_script.ult_seconds = 0
                self.shoot(run, a, ultimate=ultimate)
                self.until_reflected(run, first)
                self.advance(run, 1, b=first, facing=0)
                returned = self.until_reflected(run, second)
                self.assertEqual(returned.source_entity, second.body)
                self.assertEqual(returned.team, a.team)
                amount = 5 if ultimate else 47
                self.assertEqual(self.instance(first).vars[6584].value(), amount)
                self.assertEqual(self.instance(second).vars[6584].value(), amount)

                self.advance(run, 50)
                fight = run.match.combat
                self.assertFalse(fight.rockets)
                if ultimate:
                    field, = fight.zarya_gravitons.fields
                    self.assertIs(field.shooter, second)
                    self.assertEqual(field.source_entity, second.body)
                    self.assertEqual(field.team, a.team)
                    self.assertEqual(a.body_script.component.entity_var(342).value(), 0)
                    self.advance(run, 250)
                self.assertAlmostEqual(self.health(run, first), 175 if ultimate else 153)
                self.assertEqual(self.health(run, a), 400)
                self.assertEqual(self.health(run, second), 200)
                self.assertAlmostEqual(second.stats.values[stats.HERO_DAMAGE], 25 if ultimate else 47)
                self.assertEqual(first.stats.values.get(stats.HERO_DAMAGE, 0), 0)

    def test_actual_reflection_is_absorbed_by_original_team_sigma(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run, a, genji, sigma = self.scene()
                self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
                sigma.team = a.team
                sigma.mover.position[:] = [1, 1, 2]
                run.step({genji: (-32768, 0, 0), sigma: (0, 0, 8)})
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == 0x1D02)
                self.assertTrue(grasp.state(1).active)
                a.body_script.ult_seconds = 0
                a.body_script.set_ult_charge(0)
                self.shoot(run, a, ultimate=ultimate)
                self.advance(run, 75)
                amount = 5 if ultimate else 47
                self.assertEqual(self.instance(genji).vars[6584].value(), amount)
                self.assertEqual(grasp.vars[21312].value(), amount)
                self.assertEqual(self.health(run, a), 400)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, sigma), 400)
                self.assertFalse(run.match.combat.rockets)
                self.assertFalse(run.match.combat.zarya_gravitons.fields)
                self.assertEqual(a.body_script.component.entity_var(342).value(), 0)

    def test_frozen_damage_definition_gravity_and_lifetime_survive_transfer(self):
        run, a, b, _ = self.scene()
        a.body_script.component.entity_var(87).base = 100
        self.shoot(run, a)
        original, = run.match.combat.rockets
        lifetime = original.left + original.flown
        blast, definition, gravity = original.blast, original.definition, original.gravity
        rocket = self.until_reflected(run, b)
        self.assertIs(rocket, original)
        self.assertIs(rocket.blast, blast)
        self.assertEqual(rocket.definition, definition)
        self.assertEqual(rocket.gravity, gravity)
        self.assertEqual(rocket.source_entity, b.body)
        self.assertEqual(rocket.team, b.team)
        self.assertIsNone(rocket.shot)
        self.assertAlmostEqual(rocket.left + rocket.flown, lifetime)
        a.body_script.component.entity_var(87).base = 0
        self.advance(run, 50)
        self.assertAlmostEqual(self.health(run, a), 305, places=4)

    def test_primary_beam_is_not_reflected(self):
        run, a, b, _ = self.scene()
        self.advance(run, 51, a, 1)
        self.assertAlmostEqual(self.health(run, b), 140, places=4)
        self.assertEqual(self.health(run, a), 400)

    def test_frontal_melee_is_blocked_without_return_damage(self):
        run, a, b, _ = self.scene(close=True)
        run.step({a: (0, 0, 0x800), b: (-32768, 0, 0)})
        self.advance(run, 40)
        self.assertEqual(self.health(run, b), 200)
        self.assertEqual(self.health(run, a), 400)

    def test_rear_secondary_and_melee_are_not_blocked(self):
        for melee in (False, True):
            with self.subTest(melee=melee):
                run, a, b, _ = self.scene(facing=0, close=melee)
                if melee:
                    run.step({a: (0, 0, 0x800)})
                else:
                    self.shoot(run, a)
                self.advance(run, 60)
                self.assertEqual(self.health(run, b), 170 if melee else 153)
                self.assertEqual(self.health(run, a), 400)

    def test_expired_deflect_does_not_reflect(self):
        run, a, b, _ = self.scene()
        self.advance(run, 180)
        self.assertFalse(self.instance(b).state(10).active)
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 153)

    def test_unknown_or_excluded_projectile_metadata_passes_through(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                run, a, b, _ = self.scene()
                self.shoot(run, a)
                rocket, = run.match.combat.rockets
                tags = {} if unknown else {rocket.definition: frozenset({interception.EXCLUDED_TAG})}
                with patch.object(interception, "_TAGS", tags):
                    self.advance(run, 60)
                self.assertEqual(self.health(run, b), 153)
                self.assertEqual(self.health(run, a), 400)

    def test_no_prediction_lease_still_reflects_known_definition(self):
        run, a, b, _ = self.scene()
        with patch.object(run.match.combat.projectiles, "fired", return_value=None):
            self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, a), 353)
        self.assertEqual(self.health(run, b), 200)

    def test_nearer_body_is_hit_before_the_deflect_volume(self):
        run, a, b, c = self.scene(nearer=True)
        a.mover.position[2] = -2  # Keep the launch body outside the splash.
        a.mover.body = None
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, c), 153)
        self.assertEqual(self.health(run, b), 200)
        self.assertEqual(self.health(run, a), 400)

    def test_deflector_team_change_does_not_rewrite_transferred_team(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a)
        rocket = self.until_reflected(run, b)
        transferred_team = rocket.team
        b.team = a.team
        self.advance(run, 60)
        self.assertEqual(rocket.team, transferred_team)
        self.assertEqual(self.health(run, a), 353)

    def test_returned_secondary_is_intercepted_by_original_casters_personal_barrier(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a)
        self.advance(run, 1, a, 8)
        self.advance(run, 60)
        self.assertEqual(self.health(run, a), 400)
        self.assertEqual(self.health(run, b), 200)
        self.assertAlmostEqual(a.body_script.component.entity_var(87).value(), 9.4, places=4)
        self.assertAlmostEqual(run.match.combat.zarya_barriers.active[a.body].remaining, 153)

    def test_returned_ultimate_initial_impact_and_field_are_intercepted(self):
        run, a, b, _ = self.scene()
        self.advance(run, 1, a, 8)
        self.advance(run, 2)
        self.shoot(run, a, ultimate=True)
        for _ in range(80):
            self.advance(run, 1)
            if run.match.combat.zarya_gravitons.fields:
                break
        self.assertTrue(run.match.combat.zarya_gravitons.fields)
        self.assertEqual(self.health(run, a), 400)
        self.assertAlmostEqual(run.match.combat.zarya_barriers.active[a.body].remaining, 195)
        self.assertAlmostEqual(a.body_script.component.entity_var(87).value(), 1)
        self.advance(run, 10)
        self.assertEqual(self.health(run, a), 400)
        self.assertGreater(a.body_script.component.entity_var(87).value(), 1)
        self.assertEqual(self.health(run, b), 200)

    def test_emitted_return_persists_after_deflector_death_with_original_source_id(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a)
        rocket = self.until_reflected(run, b)
        old = b.body
        target = next(t for t in run.match.combat._targets() if t.owner is b)
        run.match.combat._damage(a, target, 1000, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(b))
        self.advance(run, 60)
        self.assertEqual(rocket.source_entity, old)
        self.assertEqual(self.health(run, a), 353)

    def test_emitted_return_persists_across_deflector_body_replacement(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a, ultimate=True)
        rocket = self.until_reflected(run, b)
        old = b.body
        run.match.switch_hero(b, heroes()[0x02E000000000006E])
        self.assertNotEqual(b.body, old)
        self.assertIn(rocket, run.match.combat.rockets)
        for _ in range(80):
            self.advance(run, 1)
            if run.match.combat.zarya_gravitons.fields:
                break
        field, = run.match.combat.zarya_gravitons.fields
        self.assertEqual(field.source_entity, old)
        self.assertIs(field.shooter, b)
        self.advance(run, 280)
        self.assertAlmostEqual(self.health(run, a), 375, places=4)

    def test_departed_deflector_cancels_return_without_field(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a, ultimate=True)
        self.until_reflected(run, b)
        b.gone = True
        self.advance(run, 60)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)
        self.assertEqual(self.health(run, a), 400)

    def test_nearer_static_wall_prevents_reflection(self):
        run, a, b, _ = self.scene()
        a.mover.position[2] = -1
        a.mover.body = None
        wall = World([(-10, 0, 3, 10, 0, 3, 10, 10, 3),
                      (-10, 0, 3, 10, 10, 3, -10, 10, 3)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            self.shoot(run, a)
            self.advance(run, 60)
        self.assertEqual(self.health(run, b), 200)
        self.assertEqual(self.health(run, a), 400)
        self.assertEqual(b.stats.values.get(stats.HERO_DAMAGE, 0), 0)

    def test_rear_approach_through_side_box_does_not_transfer_authority(self):
        run, a, _, _ = self.scene(facing=0, offset=0.6)
        self.shoot(run, a)
        self.advance(run, 25)
        rocket, = run.match.combat.rockets
        self.assertIs(rocket.shooter, a)

    def test_allied_deflector_does_not_intercept(self):
        run, a, b, _ = self.scene(ally=True)
        self.shoot(run, a)
        self.advance(run, 25)
        rocket, = run.match.combat.rockets
        self.assertIs(rocket.shooter, a)
        self.assertEqual(self.health(run, b), 200)

    def test_barrier_press_during_ultimate_cast_does_not_retroactively_protect_return(self):
        run, a, _, _ = self.scene()
        self.shoot(run, a, ultimate=True)
        self.advance(run, 1, a, 8)
        for _ in range(80):
            self.advance(run, 1)
            if run.match.combat.zarya_gravitons.fields:
                break
        self.assertTrue(run.match.combat.zarya_gravitons.fields)
        self.assertEqual(self.health(run, a), 395)
        self.assertNotIn(a.body, run.match.combat.zarya_barriers.active)

    def test_returned_secondary_is_intercepted_by_projected_allied_recipient(self):
        run, a, b, recipient = self.scene(recipient=True)
        self.advance(run, 1, a, 16)
        self.advance(run, 5)
        service = run.match.combat.zarya_barriers
        instance = next(i for i in a.body_script.component.instances.values() if i.graph.index == 0x025C)
        self.assertTrue(instance.state(10).active)
        self.assertEqual(instance.vars[536].value(), expr.Entity(recipient.body))
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, recipient), 200)
        self.assertEqual(self.health(run, b), 200)
        self.assertAlmostEqual(service.projected[a.body].remaining, 153)
        self.assertAlmostEqual(a.body_script.component.entity_var(87).value(), 9.4, places=4)
        energy = next(i for i in a.body_script.component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value(), expr.Entity(b.body))


if __name__ == "__main__":
    unittest.main()
