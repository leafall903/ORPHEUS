"""Actual Storm Bow, Sonic Arrow and Storm Arrows critical contacts."""

import unittest
from unittest.mock import patch

from test_game_combat import aim
import test_game_hanzo as hero_tests
from test_game_hanzo_dragons import prepared_profile
from test_game_hanzo_sonic import profile

from ow174.game import combat


class HanzoArrowCriticalTests(unittest.TestCase):
    def fire(self, *, frames, ability, upper, health=500):
        run, hanzo, victim = hero_tests.HanzoCombatTests().arena(3)
        fight = run.match.combat
        vitals = fight.vitals[victim.body] = combat.Vitals(victim.body, health)
        target = next(target for target in fight._targets() if target.owner is victim)
        x, y, z = target.feet
        height = target.eye - .05 if upper else .8
        angles = aim(fight._eye(hanzo), (x, y + height, z))
        if ability == 8:
            fight.configure_sonic(profile())
        if ability == 32:
            fight.configure_dragonstrike(prepared_profile())
            hanzo.body_script.set_ult_charge(1680)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as launch:
            if ability:
                run.step({hanzo: (*angles, ability)})
                run.step({hanzo: (*angles, 0)})
            if ability in (16, 32):
                for _ in range(100):
                    if fight.rockets:
                        break
                    run.step({hanzo: (*angles, 1 if ability == 16 else 0)})
            else:
                for _ in range(frames):
                    run.step({hanzo: (*angles, 1)})
                run.step({hanzo: (*angles, 0)})
        arrow, = fight.rockets
        launch.assert_called_once()
        for _ in range(55):
            if hanzo.client.messages(combat.HIT):
                break
            run.step({hanzo: (*angles, 0)})
        hit, = hanzo.client.messages(combat.HIT)
        self.assertEqual(arrow.position[1] - y >= target.eye - combat.HEAD_BELOW, upper)
        self.assertEqual(arrow.direct.critical, 1 if ability == 32 else 2)
        return hanzo, victim, vitals, hit["+0x78"], launch.call_args.args[3], arrow

    def test_actual_torso_and_upper_contacts_at_both_charges(self):
        for ability, frames, definition, base in (
            (0, 70, 0x0144, 125), (0, 5, 0x020B, 35.8696518),
            (8, 70, 0x0149, 125), (8, 5, 0x020D, 35.8684616), (16, 0, 0x270F, 70),
        ):
            for upper in (False, True):
                with self.subTest(ability=ability, frames=frames, upper=upper):
                    _, victim, vitals, notice, launched, arrow = self.fire(
                        frames=frames, ability=ability, upper=upper,
                    )
                    expected = base * (2 if upper else 1)
                    self.assertAlmostEqual(500 - vitals.total, expected, delta=.001)
                    self.assertEqual(launched, 0x0400000000000000 | definition)
                    self.assertEqual(notice, {"+0x0": {"+0x0": victim.body},
                                              "+0x4": round(expected), "+0x8": combat.CRITICAL if upper else 0})
                    self.assertEqual(arrow.sonar is not None, ability == 8)

    def test_dragonstrike_initial_arrow_keeps_its_own_upper_hit_policy(self):
        _, _, vitals, notice, definition, _ = self.fire(frames=0, ability=32, upper=True)
        self.assertEqual(definition, 0x040000000000034F)
        self.assertEqual(vitals.total, 375)
        self.assertEqual(notice["+0x4"], 125)
        self.assertFalse(notice["+0x8"] & combat.CRITICAL)

    def test_full_upper_contact_eliminates_a_250_hp_target(self):
        hanzo, victim, vitals, notice, _, _ = self.fire(frames=70, ability=0, upper=True, health=250)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, hanzo.body)
        self.assertEqual(notice, {"+0x0": {"+0x0": victim.body},
                                  "+0x4": 250, "+0x8": combat.CRITICAL | combat.KILLING})

    def test_active_fortify_suppresses_the_upper_critical_before_damage_reduction(self):
        from ow174.game.content import heroes

        run, hanzo, orisa = hero_tests.HanzoCombatTests().arena(3)
        fight = run.match.combat
        position = tuple(orisa.mover.position)
        self.assertTrue(run.match.switch_hero(orisa, next(hero for hero in heroes().values() if hero.name == "Orisa")))
        orisa.mover.position[:] = position
        orisa.mover.body = None
        for _ in range(80):
            run.step({})
        vitals = fight.vitals[orisa.body] = combat.Vitals(orisa.body, 500)
        target = next(target for target in fight._targets() if target.owner is orisa)
        x, y, z = target.feet
        angles = aim(fight._eye(hanzo), (x, y + target.eye - .05, z))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        run.step({hanzo: (*angles, 0), orisa: (-32768, 0, 8)})
        arrow, = fight.rockets
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        modifier = fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False)
        for _ in range(15):
            run.step({})
        self.assertGreaterEqual(arrow.position[1], y + target.eye - combat.HEAD_BELOW)
        self.assertEqual(arrow.direct.critical, 2)
        self.assertAlmostEqual(500 - vitals.total, 125 * (1 + modifier), delta=.001)
        notice, = hanzo.client.messages(combat.HIT)
        self.assertFalse(notice["+0x78"]["+0x8"] & combat.CRITICAL)
