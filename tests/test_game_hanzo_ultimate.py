"""Dragonstrike's reconstructed cast and initial arrow; later dragons remain open."""

import unittest
from unittest.mock import patch

import test_game_hanzo as hanzo_tests
from test_game_combat import aim

from ow174.game import bodytrace, movelog


class HanzoInitialUltimateTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, hero, victim = hanzo_tests.HanzoCombatTests().arena()
        hero.body_script.ult_seconds = 0  # isolate the resource gate from the debug passive refill
        return run, hero, victim

    def test_insufficient_charge_rejects_cast_and_does_not_spend(self):
        run, hero, victim = self.arena()
        body = hero.body_script
        cost = body.component.entity_var(338).value()
        self.assertEqual(cost, 1680)
        body.set_ult_charge(cost - 1)
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            run.step({hero: (0, 0, 32)})
            for _ in range(100):
                run.step({})
        self.assertFalse(fire.called)
        target = next(t for t in fight._targets() if t.owner is victim)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(body.component.entity_var(342).value(), cost - 1)

    def test_ready_cast_spends_once_waits_one_second_and_hits_with_initial_arrow(self):
        run, hero, victim = self.arena()
        body = hero.body_script
        body.set_ult_charge(body.component.entity_var(338).value())
        start = run.frame + 1
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            run.step({hero: (0, 0, 32)})
            self.assertEqual(body.component.entity_var(342).value(), 0)
            for _ in range(61):
                run.step({})
            self.assertFalse(fire.called)
            for _ in range(100):
                run.step({hero: (0, 0, 32 if run.frame % 2 else 0)})
        call, = fire.call_args_list
        self.assertEqual((call.args[2].instance.graph.index, call.args[2].index), (0x101, 21))
        self.assertEqual(call.args[4] - start * 16, 1000)
        self.assertEqual(fight.vitals[victim.body].total, 75)
        self.assertEqual(body.component.entity_var(342).value(), 0)
        instance = next(i for i in body.component.instances.values() if i.graph.index == 0x101)
        self.assertFalse(instance.states[7].active)
        self.assertFalse(instance.states[10].active)

    def test_death_during_cast_prevents_initial_arrow_damage(self):
        run, hero, enemy = self.arena()
        body = hero.body_script
        body.set_ult_charge(body.component.entity_var(338).value())
        fight = run.match.combat
        x, y, z = hero.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.55, z))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            run.step({hero: (0, 0, 32), enemy: (yaw, pitch, 1)})
            for _ in range(100):
                run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(fight.vitals[hero.body].dead)
        self.assertFalse(any(call.args[0] is hero for call in fire.call_args_list))
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.assertEqual(fight.vitals_of(target).total, 200)
