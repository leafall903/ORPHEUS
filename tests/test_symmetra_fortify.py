"""Actual weapon and Flashbang inputs against Orisa's authored Fortify."""

import os
import unittest
from unittest.mock import patch

import test_mccree_flashbang as flashbang
from test_game_combat import aim
from test_symmetra_combat import arena, health
from test_symmetra_turrets import wall

from ow174.game import combat, fortify
from ow174.game.content import heroes


class SymmetraFortifyTests(unittest.TestCase):
    def scene(self, distance):
        run, owner, orisa = arena()
        self.assertTrue(run.match.switch_hero(orisa, heroes()[fortify.HERO]))
        orisa.mover.position[:] = (0, 1, distance)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                angles = aim(fight._eye(owner), (0, 2, distance))
                run.step({owner: (*angles, attack), orisa: (0, 0, defense)})

        step(count=10)
        return run, owner, orisa, step

    def test_fortify_reduces_actual_beam_orb_and_melee_before_armour(self):
        for button, expected in ((1, 5 * 60 * .016 * .6 * .7), (2, 62), (0x800, 13)):
            with self.subTest(button=button):
                run, _, orisa, step = self.scene(2 if button == 0x800 else 6)
                pool = health(run, orisa)
                before = pool.now[combat.ARMOUR_POOL]
                base = pool.now[combat.HEALTH_POOL]
                if button == 2:
                    step(2, count=90)
                step(2 if button == 2 else 0, 8)
                self.assertAlmostEqual(fortify.scale(orisa), .6, places=5)
                step(button if button != 2 else 0, count=5 if button == 1 else 1)
                step(count=25)
                self.assertAlmostEqual(before - pool.now[combat.ARMOUR_POOL], expected, places=4)
                self.assertEqual(pool.now[combat.HEALTH_POOL], base)

    def test_beam_returns_to_normal_when_fortify_ends(self):
        run, _, orisa, step = self.scene(6)
        step(defense=8)
        self.assertIsNotNone(fortify.scale(orisa))
        for _ in range(270):
            if fortify.scale(orisa) is None:
                break
            step()
        self.assertIsNone(fortify.scale(orisa))
        pool = health(run, orisa)
        before = pool.total
        step(1)
        self.assertAlmostEqual(before - pool.total, 60 * .016 * .7, places=4)

    def test_actual_flashbang_damages_but_does_not_stun_fortified_orisa(self):
        fixture = flashbang.FlashbangTests()
        run, caster, orisa = fixture.arena(distance=3)
        position = tuple(orisa.mover.position)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[fortify.HERO]))
        orisa.mover.position[:] = position
        fixture.step(run, caster, orisa, target_buttons=8)
        self.assertAlmostEqual(fortify.scale(orisa), .6, places=5)
        before = health(run, orisa).total
        fixture.step(run, caster, orisa, cast=16)
        for _ in range(20):
            fixture.step(run, caster, orisa)
        self.assertAlmostEqual(before - health(run, orisa).total, 10, places=4)
        self.assertFalse(run.match.combat.is_stunned(orisa))
        self.assertIsNotNone(fortify.scale(orisa))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_fortify_reduces_sentry_damage_and_rejects_slow_until_expiry(self):
        run, owner, orisa, step = self.scene(6)
        orisa.mover.position[:] = (2, 1, 1)
        orisa.mover.body = None
        fight = run.match.combat
        pool = health(run, orisa)
        before = pool.total
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            step(defense=8)
            step(count=60)
            self.assertIsNotNone(fortify.scale(orisa))
            self.assertEqual(sentry.target, orisa.body)
            self.assertIsNone(sentry.slow)
            elapsed = fight.now - sentry.attached_at - .5
            self.assertAlmostEqual(before - pool.total, elapsed * 40 * .6 * .7, places=4)
            for _ in range(240):
                if fortify.scale(orisa) is None:
                    break
                step()
            self.assertIsNone(fortify.scale(orisa))
            before = pool.total
            step(count=5)
            self.assertIsNotNone(sentry.slow)
            self.assertAlmostEqual(before - pool.total, 5 * .016 * 40 * .7, places=4)
