"""Actual repeated Sombra weapon/skill scopes after notification-marker retirement."""

import unittest

import test_game_sombra_combat as fixture
import test_game_sombra_npc as actual_npcs

from ow174.game.commands import FIRE


def fire_after_camo(case, f, bot=None):
    def press(buttons, frames=1):
        if bot is None:
            f.press(buttons, frames)
        else:
            actual_npcs.SombraActualNpcTests.press(f, bot, buttons, frames)

    press(8)
    press(0, 60)
    ability = f.camo().state(13)
    case.assertTrue(ability.flags & ability.RUNNING)
    ammo = f.weapon().vars[53].value()
    for _ in range(120):
        press(FIRE)
        if f.weapon().vars[53].value() < ammo:
            break
    case.assertFalse(ability.flags & ability.RUNNING)
    case.assertEqual(f.weapon().vars[53].value(), ammo - 1)
    press(0, 4)


class SombraNotificationTests(unittest.TestCase):
    def make(self):
        f = fixture.SombraCombatTests()
        f.setUp()
        return f

    def test_two_actual_camo_exits_resume_primary_without_lost_ammo_debits(self):
        f = self.make()
        for index in range(2):
            fire_after_camo(self, f)
            self.assertEqual(f.health(f.b), 200 - 8 * (index + 1))
            self.assertEqual(f.weapon().vars[53].value(), 60 - (index + 1))
            f.press(0, 400)

    def test_two_actual_emp_scope_exits_resume_held_fire_with_correct_charge_and_ammo(self):
        f = self.make()
        f.a.body_script.ult_seconds = 0
        for index in range(2):
            f.a.body_script.set_ult_charge(1400)
            f.press(32)
            for _ in range(120):
                f.press(FIRE)
                if f.weapon().vars[53].value() < 60 - index:
                    break
            self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
            self.assertEqual(f.health(f.b), 200 - 8 * (index + 1))
            self.assertEqual(f.weapon().vars[53].value(), 60 - (index + 1))
            f.press(0, 400)


class SombraNotificationNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_two_automatic_reloads_reconcile_actual_heavy_damage_and_every_round(self):
        f, bot = actual_npcs.SombraActualNpcTests.make(self, '5')
        fight = f.run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        health = fight.vitals_of(target).total
        previous = 60
        refills = 0
        for _ in range(900):
            actual_npcs.SombraActualNpcTests.press(f, bot, FIRE)
            ammo = f.weapon().vars[53].value()
            if ammo > previous:
                refills += 1
            previous = ammo
            if refills == 2:
                break
        self.assertEqual(refills, 2)
        reload_switch = f.weapon().node_state(f.weapon().graph.node(3))
        for _ in range(130):
            actual_npcs.SombraActualNpcTests.press(f, bot, 0)
            if not reload_switch.current:
                break
        self.assertFalse(reload_switch.current)  # refill occurs before the authored reload scope ends
        ammo = f.weapon().vars[53].value()
        self.assertEqual(health - fight.vitals_of(target).total, 8 * (60 * 3 - ammo))
        before = fight.vitals_of(target).total
        actual_npcs.SombraActualNpcTests.press(f, bot, FIRE)
        self.assertEqual(before - fight.vitals_of(target).total, 8)
        self.assertEqual(f.weapon().vars[53].value(), ammo - 1)

    def test_two_actual_camo_primary_handoffs_for_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                fight = f.run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                before = fight.vitals_of(target).total
                for index in range(2):
                    fire_after_camo(self, f, bot)
                    self.assertEqual(fight.vitals_of(target).total, before - 8 * (index + 1))
                    self.assertEqual(f.weapon().vars[53].value(), 60 - (index + 1))
                    actual_npcs.SombraActualNpcTests.press(f, bot, 0, 400)
