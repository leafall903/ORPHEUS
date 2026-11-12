"""Partial Valkyrie execution evidence; free flight and chained effects stay open."""

import unittest
from unittest.mock import patch

import test_game_mercy_staff as staff_tests

from ow174.game import combat
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ULTIMATE = 0x20


class ValkyrieTests(unittest.TestCase):
    def setUp(self):
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.mercy = fixture.mercy
        self.step(35)
        self.script = self.mercy.body_script
        self.instance = next(i for i in self.script.component.instances.values()
                             if i.graph.index == 0x10DA)
        self.cost = self.script.component.entity_var(ULT_COST).value()

    def step(self, count, buttons=0):
        self.fixture.step(count, buttons=buttons)

    def running(self):
        return bool(self.instance.state(9).flags & nodes.Ability.RUNNING)

    def charge(self):
        return self.script.component.entity_var(ULT_CHARGE).value()

    def start(self):
        self.script.set_ult_charge(self.cost)
        self.step(1, ULTIMATE)
        self.assertTrue(self.running())

    def test_insufficient_charge_rejects_activation_without_spending_charge(self):
        self.script.set_ult_charge(self.cost - 10)
        self.step(1, ULTIMATE)
        self.assertFalse(self.running())
        self.assertGreater(self.charge(), self.cost - 10)
        self.assertLess(self.charge(), self.cost)

    def test_actual_start_spends_authored_cost_and_suspends_passive_charge(self):
        self.assertEqual(self.cost, 1820)
        self.start()
        self.assertEqual(self.charge(), 0)
        self.step(60)
        self.assertTrue(self.running())
        self.assertEqual(self.charge(), 0)
        self.assertTrue(self.script.component.entity_var(842).value())

    def test_release_keeps_fifteen_second_duration_then_clears_mods_and_resumes_charge(self):
        self.start()
        self.step(900)
        self.assertTrue(self.running())
        self.assertGreater(self.instance.find_var(0, 215).value(), 0)
        self.assertTrue(any(m.index == 11 and m.value == 0 for m in self.mercy.mover.mods.values()))
        self.step(50)
        self.assertFalse(self.running())
        self.assertEqual(self.instance.find_var(0, 215).value(), 0)
        self.assertFalse(self.script.component.entity_var(842).value())
        self.assertFalse(any(m.index == 11 for m in self.mercy.mover.mods.values()))
        self.assertGreater(self.charge(), 0)

    def test_repeated_press_does_not_reset_duration_or_reconsume_charge(self):
        self.start()
        self.step(20)
        before = self.instance.find_var(0, 215).value()
        for _ in range(10):
            self.step(1, ULTIMATE)
            self.step(1)
        self.assertLess(self.instance.find_var(0, 215).value(), before - 0.3)
        self.assertEqual(self.charge(), 0)

    def test_duplicate_command_does_not_advance_duration(self):
        self.start()
        duration = self.instance.find_var(0, 215).value()
        self.mercy.take_commands([Command(self.fixture.run.frame, buttons=ULTIMATE)])
        self.assertEqual(self.instance.find_var(0, 215).value(), duration)
        self.assertEqual(self.charge(), 0)

    def test_active_self_regeneration_continues_after_repeated_damage(self):
        self.start()
        target = next(t for t in self.fixture.fight._targets() if t.owner is self.mercy)
        health = self.fixture.fight.vitals_of(target)
        self.fixture.fight._damage(self.fixture.enemy, target, 100, False, None,
                                   self.fixture.run.frame)
        self.step(10)
        self.assertGreater(health.total, 102)
        self.fixture.fight._damage(self.fixture.enemy, target, 10, False, None,
                                   self.fixture.run.frame)
        before = health.total
        self.step(10)
        self.assertAlmostEqual(health.total - before, 3.2, places=4)

    def test_blaster_fires_more_than_one_magazine_without_depleting_during_ultimate(self):
        self.start()
        self.step(35)
        run = self.fixture.run
        run.frame += 1
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.mercy.take_commands([Command(run.frame, action=2)])
        self.step(35)
        weapon = next(i for i in self.script.component.instances.values() if i.graph.index == 0x006D)
        with patch.object(self.fixture.fight, "_shoot", wraps=self.fixture.fight._shoot) as shoot:
            self.step(300, FIRE)
            self.assertGreater(shoot.call_count, 20)
        self.assertEqual(weapon.find_var(0, 53).value(), 20)
        self.step(650)
        self.assertFalse(self.running())
        self.step(20, FIRE)
        self.assertLess(weapon.find_var(0, 53).value(), 20)

    def test_caster_death_ends_ultimate_and_prevents_self_regeneration(self):
        self.start()
        target = next(t for t in self.fixture.fight._targets() if t.owner is self.mercy)
        self.fixture.fight._damage(self.fixture.enemy, target, 500, False, None,
                                   self.fixture.run.frame)
        self.step(30)
        self.assertFalse(self.running())
        self.assertEqual(self.fixture.fight.vitals_of(target).total, 0)
        self.assertFalse(self.script.component.entity_var(842).value())

    def test_hero_replacement_does_not_keep_ultimate_movement_modifiers(self):
        self.start()
        self.fixture.match.switch_hero(self.mercy, heroes()[SOLDIER])
        self.step(1)
        self.assertFalse(any(m.index == 11 for m in self.mercy.mover.mods.values()))
        self.assertFalse(any(i.graph.index == 0x10DA
                             for i in self.mercy.body_script.component.instances.values()))


if __name__ == "__main__":
    unittest.main()
