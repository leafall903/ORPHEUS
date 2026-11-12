"""Actual Ana contacts against Bastion's authored reconfiguration."""
import unittest

import test_game_ana as fixture
import test_game_ana_fortify as attacks
import test_game_ana_orisa_barrier as placement

from ow174.game import stats
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class AnaIroncladTests(unittest.TestCase):
    def arena(self, distance=8):
        run, ana, bastion, _ = fixture.AnaRifleTests().setup_fight(enemy=True, distance=distance)
        self.assertTrue(run.match.switch_hero(bastion, heroes()[0x02E0000000000015]))
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(bastion, (x, y, z + distance))
        for _ in range(30):
            run.step({bastion: (0, 0, 0)})
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is bastion)
        return run, ana, bastion, target, fight.vitals_of(target)

    @staticmethod
    def reconfigure(arena):
        run, _, bastion, _, _ = arena
        run.step({bastion: (0, 0, 8)})
        for _ in range(120):
            run.step({bastion: (0, 0, 0)})

    def test_scoped_damage_tracks_actual_reconfiguration_and_reversion(self):
        arena = self.arena()
        _, _, _, _, vitals = arena
        for index, amount in enumerate((70, 56, 70)):
            with self.subTest(phase=index):
                if index:
                    self.reconfigure(arena)
                before = vitals.total
                attacks.AnaFortifyTests().attack(arena, 1, scoped=True)
                self.assertAlmostEqual(before - vitals.total, amount, places=4)

    def test_ironclad_reduces_other_contacts_without_blocking_sleep_or_antiheal(self):
        for button, loss in ((1, 56), (8, 2), (16, 35), (0x800, 19)):
            with self.subTest(button=button):
                arena = self.arena(distance=1.5 if button == 0x800 else 8)
                run, ana, bastion, _, vitals = arena
                self.reconfigure(arena)
                before = vitals.total
                attacks.AnaFortifyTests().attack(arena, button)
                self.assertAlmostEqual(before - vitals.total, loss, places=4)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), loss, places=4)
                self.assertEqual(run.match.combat.is_asleep(bastion), button == 8)
                self.assertEqual(vitals.healing_block_until > run.match.combat.now, button == 16)

    def test_tank_cast_spends_charge_and_reuses_ironclad_until_expiry(self):
        arena = self.arena()
        run, _, bastion, _, vitals = arena
        script = bastion.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({bastion: (0, 0, 32)})
        for _ in range(120):
            run.step({bastion: (0, 0, 0)})
        self.assertLess(script.component.entity_var(ULT_CHARGE).value(), cost)
        before = vitals.total
        attacks.AnaFortifyTests().attack(arena, 1, scoped=True)
        self.assertAlmostEqual(before - vitals.total, 56, places=4)
        for _ in range(600):
            run.step({bastion: (0, 0, 0)})
        before = vitals.total
        attacks.AnaFortifyTests().attack(arena, 1, scoped=True)
        self.assertAlmostEqual(before - vitals.total, 70, places=4)
