"""Explicit physical effect time through the shared damage/effect interface."""

import math
import unittest
from unittest.mock import patch

from ow174.game import combat
from tests.runtime import test_game_ana as fixture


class ImpactEffectClockTests(unittest.TestCase):
    def test_timed_rifle_hit_keeps_the_explicit_physical_start(self):
        run, shooter, _, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight = run.match.combat
        impact = fight.tick - 20
        hit = combat.HitDef(70, duration=0.6, ally_heal=70)
        fight._apply_hit(shooter, entry, hit, 70, False, impact, world_tick=impact)
        dose, = fight.timed_hits
        self.assertAlmostEqual(dose.started, fight.now - 20 * combat.TICK)
        fight._advance_timed_hits()
        expected = 200 - round(70 * (20 * combat.TICK / 0.6) * 1024) / 1024
        self.assertEqual(fight.vitals_of(entry).total, expected)

    def test_historical_flashbang_wakes_sleep_using_the_same_physical_tick(self):
        run, shooter, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight = run.match.combat
        fight._hit_effect(entry, combat.SLEEP_DART_EFFECT, shooter=shooter, world_tick=fight.tick - 100)
        impact = fight.tick - 20
        hit = combat.HitDef(5, effect=combat.FLASHBANG_EFFECT)
        fight._apply_hit(shooter, entry, hit, 5, False, impact, world_tick=impact)
        self.assertEqual(fight.vitals_of(entry).total, 195)
        self.assertFalse(fight.is_asleep(target))
        self.assertEqual(fight.stuns[target.body], impact + math.ceil(0.8 / combat.TICK))

    def test_damage_before_a_later_sleep_cannot_wake_that_sleep(self):
        run, shooter, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight = run.match.combat
        fight._hit_effect(entry, combat.SLEEP_DART_EFFECT, shooter=shooter)
        before = fight.vitals_of(entry).sleep_until
        fight._damage(shooter, entry, 5, False, None, fight.tick - 20, world_tick=fight.tick - 20)
        self.assertEqual(fight.vitals_of(entry).sleep_until, before)

    def test_late_flashbang_uses_impact_deadline_and_original_mode_duration(self):
        for extended in (False, True):
            with self.subTest(extended=extended):
                run, shooter, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
                fight = run.match.combat
                impact = fight.tick - 20
                duration = 1.7 if extended else 0.8
                expected = impact + math.ceil(duration / combat.TICK)
                before = fight.tick, fight.now
                variables = {combat.FLASHBANG_MODE_FLAG: extended}
                hit = combat.HitDef(5, effect=combat.FLASHBANG_EFFECT)
                with patch.object(combat.graph, "mode_variables", return_value=variables):
                    fight._apply_hit(shooter, entry, hit, 5, False, impact, world_tick=impact)
                self.assertEqual(fight.vitals_of(entry).total, 195)
                self.assertEqual(fight.stuns[target.body], expected)
                lock, = fight.input_locks[target.body]
                self.assertEqual((lock.start, lock.end), (impact, expected))
                self.assertEqual((fight.tick, fight.now), before)

    def test_expired_historical_effect_deals_damage_without_new_control(self):
        for effect, age in ((combat.FLASHBANG_EFFECT, 120), (combat.SLEEP_DART_EFFECT, 400)):
            with self.subTest(effect=effect):
                run, shooter, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
                fight = run.match.combat
                impact = fight.tick - age
                hit = combat.HitDef(5, effect=effect)
                fight._apply_hit(shooter, entry, hit, 5, False, impact, world_tick=impact)
                self.assertEqual(fight.vitals_of(entry).total, 195)
                self.assertFalse(fight.is_asleep(target))
                self.assertFalse(fight.is_stunned(target))
                self.assertNotIn(target.body, fight.input_locks)

    def test_sleep_uses_explicit_impact_and_default_call_uses_current_tick(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                run, shooter, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
                fight = run.match.combat
                impact = fight.tick - 20 if historical else fight.tick
                kwargs = {"world_tick": impact} if historical else {}
                fight._hit_effect(entry, combat.SLEEP_DART_EFFECT, shooter=shooter, **kwargs)
                vitals = fight.vitals_of(entry)
                self.assertEqual(vitals.sleep_until, impact + combat.SLEEP_TICKS)
                self.assertEqual(vitals.sleep_wake_floor, impact + combat.SLEEP_WAKE_FLOOR_TICKS)
                lock, = fight.input_locks[target.body]
                self.assertEqual(lock.start, impact)
