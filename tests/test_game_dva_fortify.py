"""Controlled D.Va attacks against Orisa's input-activated Fortify."""

import unittest

import test_game_dva_created_body as created_body
import test_game_dva_invulnerability as invulnerability
from test_game_combat import aim

from ow174.game import combat, defenses
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class FortifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        invulnerability.InvulnerabilityTests.setUpClass()

    def test_both_primary_weapons_lose_critical_bonus_until_fortify_expires(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, orisa, mech, health, _, _ = invulnerability.InvulnerabilityTests.scene(
                    self, "orisa", pilot_form=pilot_form, distance=2,
                )
                fight = run.match.combat
                target = fight.player_target(orisa)
                x, y, z = target.feet
                point = (x, y + target.eye - combat.HEAD_BELOW + .06, z - target.radius)
                angles = aim(fight._eye(pilot if pilot_form else mech), point)
                self.assertEqual(health.total, 450)

                run.step({orisa: (0, 0, 8)})
                state = defenses.active_fortify(orisa)
                self.assertIsNotNone(state)
                before = health.total
                run.step({pilot: (*angles, FIRE)})
                for _ in range(25):
                    run.step({})
                hits = pilot.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1 if pilot_form else 11)
                self.assertFalse(any(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))
                self.assertAlmostEqual(before - health.total, 4.2 if pilot_form else 6.6, places=5)

                for _ in range(400):
                    if defenses.active_fortify(orisa) is None:
                        break
                    run.step({})
                self.assertIsNone(defenses.active_fortify(orisa))
                before = health.total
                run.step({pilot: (*angles, FIRE)})
                for _ in range(25):
                    run.step({})
                later = pilot.client.messages(combat.HIT)[len(hits):]
                critical = sum(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL) for hit in later)
                self.assertEqual(len(later), len(hits))
                self.assertGreater(critical, 0)
                self.assertAlmostEqual(before - health.total, 23 if pilot_form else 11 + critical)

    def test_boosters_deal_reduced_contact_damage_without_displacing_fortified_orisa(self):
        for fortified in (False, True):
            with self.subTest(fortified=fortified):
                run, pilot, orisa, _, health, yaw, _ = invulnerability.InvulnerabilityTests.scene(
                    self, "orisa", distance=4.5,
                )
                if fortified:
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(defenses.active_fortify(orisa))
                start_z = orisa.mover.position[2]
                before = health.total
                run.step({pilot: (yaw, 0, 8)})
                for _ in range(35):
                    run.step({pilot: (yaw, 0, 0)})
                self.assertAlmostEqual(before - health.total, 3 if fortified else 5)
                if fortified:
                    self.assertAlmostEqual(orisa.mover.position[2], start_z)
                else:
                    self.assertGreater(orisa.mover.position[2], start_z + 1)

    def test_both_quick_melee_attacks_and_micro_missiles_deal_reduced_damage(self):
        attacks = ((False, 0x800, 40, 13), (True, 0x800, 40, 13), (False, 16, 125, 37.8))
        for pilot_form, button, frames, damage in attacks:
            with self.subTest(pilot_form=pilot_form, button=button):
                run, pilot, orisa, _, health, yaw, pitch = invulnerability.InvulnerabilityTests.scene(
                    self, "orisa", pilot_form=pilot_form, distance=1.5,
                )
                run.step({orisa: (0, 0, 8)})
                self.assertIsNotNone(defenses.active_fortify(orisa))
                before = health.total
                run.step({pilot: (yaw, pitch, button)})
                for _ in range(frames):
                    run.step({})
                self.assertIsNotNone(defenses.active_fortify(orisa))
                self.assertAlmostEqual(before - health.total, damage, places=5)

    def test_detached_self_destruct_reduces_damage_without_applying_impulse(self):
        run, pilot, orisa, mech, _, _, _ = invulnerability.InvulnerabilityTests.scene(self, "orisa")
        health = combat.Vitals(orisa.body, 1000, armour=250)
        run.match.combat.vitals[orisa.body] = health
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertTrue(mech.detached_bomb)
        for _ in range(160):
            run.step({})
        run.step({orisa: (0, 0, 8)})
        self.assertIsNotNone(defenses.active_fortify(orisa))
        start = tuple(orisa.mover.position)
        for _ in range(50):
            run.step({})
        self.assertNotIn(mech, run.match.bots)
        self.assertIsNotNone(defenses.active_fortify(orisa))
        self.assertAlmostEqual(1250 - health.total, 595, places=4)
        self.assertEqual(tuple(orisa.mover.position), start)

    def test_call_mech_landing_reduces_damage_without_applying_impulse(self):
        run, pilot, orisa, instance, old = created_body.CreatedBodyTests().ready_call_mech()
        match = run.match
        self.assertTrue(match.switch_hero(orisa, find_hero("orisa")))
        run.step({pilot: (0, 0, 32)})
        for _ in range(24):
            run.step({})
        mech, = match.bots
        self.assertIsNot(mech, old)
        x, y, z = mech.position
        orisa.mover.position[:] = (x + 1.5, y, z)
        orisa.mover.body = None
        run.step({orisa: (0, 0, 8)})
        self.assertIsNotNone(defenses.active_fortify(orisa))
        fight = match.combat
        health = fight.vitals_of(fight.player_target(orisa))
        before = health.total
        for _ in range(45):
            run.step({})
            if instance.state(20).counter:
                break
        self.assertEqual(instance.state(20).counter, 1)
        self.assertIsNotNone(defenses.active_fortify(orisa))
        self.assertAlmostEqual(before - health.total, 145, places=4)
        self.assertEqual(orisa.mover.velocity[0], 0)
