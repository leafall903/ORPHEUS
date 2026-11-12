"""Actual McCree ammo/refill resource path; no Roll motion/hero acceptance claim."""
import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game.content import heroes


class ReloadArbitrationTests(unittest.TestCase):
    def arena(self):
        run = Run()
        player = run.players[0]
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000042]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for _ in range(30):
            run.step({})
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1D2)
        return run, player, weapon

    @staticmethod
    def step(run, player, buttons=0):
        run.step({player: (0, 0, buttons)})

    def test_held_primary_after_actual_roll_samples_refill_for_partial_and_empty_magazine(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                run, player, weapon = self.arena()
                self.step(run, player, 1)
                if empty:
                    for _ in range(300):
                        if weapon.vars[53].value() == 0:
                            break
                        self.step(run, player, 1)
                    self.assertEqual(weapon.vars[53].value(), 0)
                else:
                    for _ in range(40):
                        self.step(run, player)
                    self.assertEqual(weapon.vars[53].value(), 5)
                self.step(run, player)
                self.step(run, player, 8)
                with patch.object(run.match.combat, '_shoot', wraps=run.match.combat._shoot) as shots:
                    for _ in range(24):
                        self.step(run, player, 1)
                    self.assertEqual(shots.call_count, 0)
                    for _ in range(10):
                        self.step(run, player, 1)
                        if shots.call_count:
                            break
                    self.assertEqual(shots.call_count, 1)
                    self.assertEqual(weapon.vars[53].value(), 5)

    def test_actual_roll_refill_then_held_fan_can_report_six_rounds(self):
        run, player, weapon = self.arena()
        self.step(run, player, 1)
        for _ in range(40):
            self.step(run, player)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.step(run, player, 8)
        with patch.object(run.match.combat, '_shoot', wraps=run.match.combat._shoot) as shots:
            for _ in range(100):
                self.step(run, player, 2)
                if shots.call_count == 6:
                    break
            self.assertEqual(shots.call_count, 6)
            self.assertEqual(weapon.vars[53].value(), 0)
