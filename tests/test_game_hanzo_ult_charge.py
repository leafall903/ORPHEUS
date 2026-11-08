"""Manual0101 cast lockout for the existing debug refill; native generation is open."""

import unittest
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_control as control_tests

from ow174.game import bodytrace, movelog
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import BodyScript

HANZO = 0x02E0000000000005


class HanzoManualChargeTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    @staticmethod
    def phases(body):
        return {state.node.pos for instance in body.component.instances.values()
                if instance.graph.index == 0x101 for state in instance.states.values()
                if state.active and state.node.pos in (29, 37)}

    @staticmethod
    def charge(body):
        return body.component.entity_var(342).value()

    def test_no_debug_refill_during_both_phases_then_refill_and_fresh_recast(self):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        body = hero.body_script
        body.ult_seconds = 0.1  # amplify any missed gate; this is explicitly a debug rate
        body.set_ult_charge(1680)
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            run.step({hero: (0, 0, 32)})
            self.assertEqual(self.phases(body), {29, 37})
            self.assertEqual(self.charge(body), 0)
            for offset in range(1, 75):
                run.step({hero: (0, 0, 32 if offset == 65 else 0)})
                self.assertEqual(self.charge(body), 0, offset)
                self.assertEqual(self.phases(body), {29, 37} if offset < 62 else {37})
            self.assertEqual(sum(c.args[0] is hero for c in fire.call_args_list), 1)
            run.step({})  # authored1.2s recovery ends; refill may resume in this frame
            self.assertFalse(self.phases(body))
            self.assertGreater(self.charge(body), 0)
            self.assertLess(self.charge(body), 1680)
            for _ in range(10):
                run.step({})
            self.assertEqual(fight.vitals[target.body].total, 75)
            self.assertEqual(self.charge(body), 1680)
            run.step({hero: (0, 0, 32)})
            self.assertEqual(self.charge(body), 0)
            for _ in range(90):
                run.step({})
        self.assertEqual(sum(c.args[0] is hero for c in fire.call_args_list), 2)
        self.assertTrue(fight.vitals[target.body].dead)

    def test_actual_flashbang_abort_releases_debug_refill_without_a_late_arrow(self):
        control = control_tests.HanzoControlTests()
        run, caster, hero = control.arena()
        body = hero.body_script
        body.ult_seconds = 1
        body.set_ult_charge(1680)
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            control.step(run, caster, hero, target_buttons=32)
            self.assertEqual(self.charge(body), 0)
            control.cast(run, caster, hero)
            self.assertTrue(fight.is_stunned(hero))
            self.assertFalse(self.phases(body))
            self.assertGreater(self.charge(body), 0)
            for _ in range(150):
                control.step(run, caster, hero)
            self.assertFalse(any(c.args[0] is hero for c in fire.call_args_list))
            self.assertEqual(self.charge(body), 1680)
            control.step(run, caster, hero, target_buttons=32)
            self.assertEqual(self.charge(body), 0)
            for _ in range(90):
                control.step(run, caster, hero)
        self.assertEqual(sum(c.args[0] is hero for c in fire.call_args_list), 1)
        self.assertEqual(fight.vitals[caster.body].total, 100)

    def test_replacement_does_not_inherit_the_old_manual_cast_lockout(self):
        run, hero, _target = hero_tests.HanzoCombatTests().arena()
        body = hero.body_script
        body.ult_seconds = 0.1
        body.set_ult_charge(1680)
        run.step({hero: (0, 0, 32)})
        self.assertEqual(self.charge(body), 0)
        self.assertEqual(self.phases(body), {29, 37})
        run.match.switch_hero(hero, heroes()[SOLDIER])
        self.assertTrue(body.retired)
        replacement = hero.body_script
        replacement.ult_seconds = 1
        run.step({})
        self.assertFalse(self.phases(replacement))
        self.assertGreater(self.charge(replacement), 0)
        run.match.switch_hero(hero, heroes()[HANZO])
        replacement = hero.body_script
        replacement.ult_seconds = 1
        run.step({})
        self.assertFalse(self.phases(replacement))
        self.assertGreater(self.charge(replacement), 0)

    def test_skipped_commands_cross_phase_deadline_without_refilling_the_locked_frames(self):
        bodies = [BodyScript(HANZO, 100 + index, 1000, ult_seconds=1) for index in range(2)]
        for body in bodies:
            body.set_ult_charge(1680)
            body.command(1001, 32)
            self.assertEqual(self.charge(body), 0)
        continuous, skipped = bodies
        for frame in range(1002, 1081):
            continuous.command(frame, 0)
        skipped.command(1080, 0)
        self.assertFalse(self.phases(continuous))
        self.assertFalse(self.phases(skipped))
        self.assertEqual(self.charge(continuous), self.charge(skipped))
        self.assertGreater(self.charge(skipped), 0)
        self.assertLess(self.charge(skipped), 1680 / 8)
