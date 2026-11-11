"""Actual Ana input against Sigma's authored grasp and recovery."""

import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.content import heroes


class AnaSigmaTests(unittest.TestCase):
    def arena(self, *, yaw=-32768):
        run, ana, sigma, _ = fixture.AnaRifleTests().setup_fight(enemy=True)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        x, y, z = ana.mover.position
        sigma.mover.position[:] = x, y, z + 7
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sigma)
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        run.step({sigma: (yaw, 0, 8)})
        for _ in range(25):
            run.step({sigma: (yaw, 0, 0)})
        self.assertTrue(instance.states[1].active)
        return run, ana, sigma, target, fight.vitals_of(target), instance

    def attack(self, arena, button, *, yaw=-32768, scoped=False):
        run, ana, sigma, target, *_ = arena
        direction = aim(run.match.combat._eye(ana),
                        (target.feet[0], target.feet[1] + 1, target.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2), sigma: (yaw, 0, 0)})
        run.step({ana: (*direction, button | (2 if scoped else 0)), sigma: (yaw, 0, 0)})
        for _ in range(48):
            run.step({ana: (*direction, 2 if scoped else 0), sigma: (yaw, 0, 0)})

    def test_actual_rifle_modes_dart_and_grenade_convert_absorbed_amount(self):
        for button, scoped, payload in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                arena = self.arena()
                run, ana, sigma, _, vitals, instance = arena
                self.attack(arena, button, scoped=scoped)
                self.assertEqual(vitals.total, 400)
                self.assertEqual(instance.find_var(0, 21312).value(), payload)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(ana.client.messages(combat.HIT))
                self.assertFalse(run.match.combat.is_asleep(sigma))
                self.assertEqual(vitals.healing_block_until, 0)
                for _ in range(100):
                    run.step({})
                self.assertAlmostEqual(vitals.bonus_shields, payload * .6, places=4)

    def test_rear_sleep_interrupts_grasp_without_granting_shields(self):
        arena = self.arena(yaw=0)
        run, _, sigma, _, vitals, instance = arena
        self.attack(arena, 8, yaw=0)
        self.assertTrue(run.match.combat.is_asleep(sigma))
        self.assertFalse(instance.states[1].active)
        self.assertEqual(vitals.total, 395)
        self.assertEqual(vitals.bonus_shields, 0)

    def test_temporary_shields_take_damage_first_then_decay_and_retire(self):
        arena = self.arena()
        run, _, sigma, target, vitals, _ = arena
        self.attack(arena, 16)
        for _ in range(100):
            run.step({})
        self.assertAlmostEqual(vitals.bonus_shields, 30, places=4)
        self.attack(arena, 8)
        self.assertAlmostEqual(vitals.bonus_shields, 25, places=4)
        self.assertEqual(sum(vitals.now.values()), 400)
        for _ in range(240):
            run.step({})
        self.assertGreater(vitals.bonus_shields, 0)
        self.assertLess(vitals.bonus_shields, 25)
        for _ in range(180):
            run.step({})
        self.assertEqual(vitals.bonus_shields, 0)
        old_body = sigma.body
        self.assertTrue(run.match.switch_hero(sigma, heroes()[fixture.ANA]))
        self.assertNotIn(old_body, run.match.combat.vitals)
        self.assertFalse(any(key[0] == target.entity for key in run.match.combat.sigma.conversions))
