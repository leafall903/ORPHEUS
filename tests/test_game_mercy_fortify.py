"""Actual Mercy attacks into Orisa's 104319 Fortify filter."""

import unittest

import test_game_mercy_staff
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip, shot

from ow174.game import phase_defenses, stats
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE


class MercyFortifyTests(unittest.TestCase):
    def encounter(self, distance):
        fixture = test_game_mercy_staff.MercyStaffTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        run, fight, mercy, orisa = fixture.run, fixture.fight, fixture.mercy, fixture.enemy
        run.match.switch_hero(orisa, heroes()[phase_defenses.ORISA])
        orisa.mover.position[:] = (0, 1, distance)
        orisa.mover.body = None
        equip(run, mercy)
        target = next(t for t in fight._targets() if t.owner is orisa)
        return run, fight, mercy, orisa, target, fight.vitals_of(target)

    def test_actual_blaster_and_melee_use_fortify_before_armour_and_recover_after_expiry(self):
        run, fight, mercy, orisa, target, health = self.encounter(1.5)
        component = mercy.body_script.component
        component.set(12918, 0.0)  # isolate damage charge from passive awards
        charge = component.entity_var(ULT_CHARGE)
        charge_before = charge.value()
        initial = health.total
        shot(run, mercy, target.feet, 1.0)
        self.assertAlmostEqual(initial - health.total, 15.0)

        run.step({orisa: (0, 0, 0x08)})
        self.assertAlmostEqual(phase_defenses.fortify_damage_scale(orisa, orisa.body), 0.6)
        before = health.total
        shot(run, mercy, target.feet, 1.0)
        self.assertAlmostEqual(before - health.total, 7.0, places=5)

        before = health.total
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(mercy), (x, y + 1, z))
        run.step({mercy: (yaw, pitch, 0x800)})
        for _ in range(20):
            run.step({mercy: (yaw, pitch, 0)})
        self.assertAlmostEqual(before - health.total, 13.0, places=5)

        for _ in range(300):
            run.step({})
            if phase_defenses.fortify_damage_scale(orisa, orisa.body) == 1:
                break
        self.assertEqual(phase_defenses.fortify_damage_scale(orisa, orisa.body), 1)
        before = health.total
        shot(run, mercy, target.feet, 1.0)
        self.assertAlmostEqual(before - health.total, 15.0)
        self.assertAlmostEqual(mercy.stats.value(stats.HERO_DAMAGE), initial - health.total)
        self.assertAlmostEqual(charge.value() - charge_before, initial - health.total, places=4)

    def test_fortify_started_after_launch_reduces_damage_at_impact(self):
        run, fight, mercy, orisa, target, health = self.encounter(28.0)
        initial = health.total
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(mercy), (x, y + 1, z))
        self.assertEqual(phase_defenses.fortify_damage_scale(orisa, orisa.body), 1.0)
        run.step({mercy: (yaw, pitch, FIRE)})
        self.assertEqual(len(fight.rockets), 1)
        self.assertEqual(health.total, initial)
        run.step({orisa: (0, 0, 0x08)})
        for _ in range(45):
            run.step({})
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(initial - health.total, 7.0, places=5)
