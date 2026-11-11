"""Actual Ana and Orisa commands through the scoped Fortify filter."""

import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import fortify, stats
from ow174.game.content import heroes


class AnaFortifyTests(unittest.TestCase):
    def arena(self, *, distance=6, ally=False):
        run, ana, orisa, _ = fixture.AnaRifleTests().setup_fight(enemy=not ally)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[fortify.HERO]))
        x, y, z = ana.mover.position
        orisa.mover.position[:] = x, y, z + distance
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        entry = next(t for t in fight._targets() if t.owner is orisa)
        return run, ana, orisa, entry, fight.vitals_of(entry)

    def activate(self, arena):
        run, _, orisa, _, _ = arena
        run.step({orisa: (0, 0, 8)})
        state = fortify.active_filter(orisa)
        self.assertIsNotNone(state)
        self.assertAlmostEqual(fortify.damage_scale(state), .6)
        self.assertTrue(fortify.control_immune(orisa))

    def attack(self, arena, button, *, scoped=False):
        run, ana, _, entry, _ = arena
        direction = aim(run.match.combat._eye(ana),
                        (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2)})
        run.step({ana: (*direction, button | (2 if scoped else 0))})
        for _ in range(65):
            run.step({ana: (*direction, 2 if scoped else 0)})

    def test_actual_fortify_reduces_both_rifles_grenade_dart_and_melee(self):
        for button, scoped, loss in ((1, False, 42), (1, True, 42), (8, False, 1.5),
                                     (16, False, 25), (0x800, False, 13)):
            with self.subTest(button=button, scoped=scoped):
                arena = self.arena(distance=1.5 if button == 0x800 else 6)
                run, ana, orisa, _, vitals = arena
                self.activate(arena)
                before = vitals.total
                self.attack(arena, button, scoped=scoped)
                self.assertAlmostEqual(vitals.total, before - loss, places=4)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), loss, places=4)
                self.assertFalse(run.match.combat.is_asleep(orisa))
                self.assertEqual(vitals.healing_block_until > run.match.combat.now, button == 16)

    def test_fortify_expires_and_actual_dart_again_applies_sleep(self):
        arena = self.arena()
        run, _, orisa, _, vitals = arena
        self.activate(arena)
        for _ in range(270):
            run.step({})
        self.assertFalse(fortify.control_immune(orisa))
        self.assertIsNone(fortify.active_filter(orisa))
        before = vitals.total
        self.attack(arena, 8)
        self.assertEqual(vitals.total, before - 2.5)
        self.assertTrue(run.match.combat.is_asleep(orisa))
        self.assertTrue(run.match.switch_hero(orisa, heroes()[fixture.ANA]))
        self.assertFalse(run.match.combat.is_asleep(orisa))

    def test_existing_sleep_blocks_fortify_and_friendly_heals_still_work(self):
        arena = self.arena()
        run, _, orisa, _, _ = arena
        self.attack(arena, 8)
        self.assertTrue(run.match.combat.is_asleep(orisa))
        run.step({orisa: (0, 0, 8)})
        self.assertIsNone(fortify.active_filter(orisa))
        friendly = self.arena(ally=True)
        vitals = friendly[4]
        vitals.damage(100)
        before = vitals.total
        self.activate(friendly)
        self.attack(friendly, 1)
        self.assertEqual(vitals.total, before + 70)

    def test_rifle_dose_observes_live_fortify_expiry(self):
        arena = self.arena()
        run, _, orisa, _, vitals = arena
        self.activate(arena)
        state = fortify.active_filter(orisa)
        for _ in range(250):
            if state.now - state.start >= 3800:
                break
            run.step({})
        self.assertTrue(state.active)
        before = vitals.total
        self.attack(arena, 1)
        self.assertFalse(state.active)
        self.assertGreater(before - vitals.total, 42)
        self.assertLess(before - vitals.total, 70)
