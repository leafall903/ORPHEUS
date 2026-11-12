"""Actual input/pose checks for Orisa's authored weapon movement modifiers."""

import unittest

import test_orisa_combat as fixture

from ow174.game.commands import FIRE, Command


class OrisaMovementTests(unittest.TestCase):
    def arena(self):
        run, player, enemy = fixture.OrisaCombatTests().arena()
        enemy.spawned = False
        return run, player

    @staticmethod
    def step(run, player, buttons=0):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for actor in run.players:
            actor.take_commands([Command(run.frame, yaw=0, pitch=0, extra=0,
                                         buttons=buttons if actor is player else 0,
                                         forward=127 if actor is player else 0)])

    def advance(self, run, player, count, buttons=0):
        for _ in range(count):
            self.step(run, player, buttons)

    def speed(self, player, expected):
        # Network poses quantize velocity to1/1024.
        self.assertAlmostEqual(player.mover.velocity[2], expected, delta=1 / 1024)

    @staticmethod
    def rifle(player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xBD4)

    def test_primary_held_input_applies_authored_thirty_percent_slow_and_release_recovers(self):
        run, player = self.arena()
        self.advance(run, player, 40)
        self.speed(player, 5.5)
        self.advance(run, player, 50, FIRE)
        self.speed(player, 3.85)
        self.assertEqual(self.rifle(player).vars[53].value(), 140)
        self.assertAlmostEqual(player.body_script.component.entity_var(23184).value(), 0.3)
        self.advance(run, player, 30)
        self.speed(player, 5.5)
        self.assertFalse(player.body_script.component.entity_var(23184).value())

    def test_manual_reload_recovers_speed_and_held_fire_reapplies_slow_after_reload(self):
        run, player = self.arena()
        self.advance(run, player, 50, FIRE)
        rifle = self.rifle(player)
        self.step(run, player, FIRE | 0x400)
        self.advance(run, player, 100, FIRE)
        self.assertTrue(rifle.vars[51].value())
        self.assertEqual(rifle.vars[53].value(), 140)
        self.speed(player, 5.5)
        for _ in range(150):
            self.step(run, player, FIRE)
            if not rifle.vars[51].value() and rifle.vars[53].value() < 150:
                break
        self.assertFalse(rifle.vars[51].value())
        self.assertLess(rifle.vars[53].value(), 150)
        self.advance(run, player, 5, FIRE)
        self.speed(player, 3.85)

    def test_actual_empty_magazine_auto_reload_restores_speed_and_firing_slow_returns(self):
        run, player = self.arena()
        rifle = self.rifle(player)
        for _ in range(2000):
            self.step(run, player, FIRE)
            if rifle.vars[53].value() == 0 and rifle.vars[51].value():
                break
        self.assertEqual(rifle.vars[53].value(), 0)
        self.assertTrue(rifle.vars[51].value())
        self.advance(run, player, 80, FIRE)
        self.assertEqual(rifle.vars[53].value(), 0)
        self.speed(player, 5.5)
        for _ in range(180):
            self.step(run, player, FIRE)
            if not rifle.vars[51].value() and rifle.vars[53].value() > 0:
                break
        self.assertFalse(rifle.vars[51].value())
        self.assertGreater(rifle.vars[53].value(), 0)
        self.advance(run, player, 5, FIRE)
        self.speed(player, 3.85)

    def test_actual_fortify_does_not_erase_intrinsic_primary_slow(self):
        run, player = self.arena()
        self.advance(run, player, 50, FIRE | 8)
        fortify = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xCF4)
        self.assertTrue(fortify.state(4).active)
        self.assertTrue(player.body_script.component.entity_var(9161).value())
        self.speed(player, 3.85)
        self.advance(run, player, 30, 8)
        self.assertTrue(fortify.state(4).active)
        self.speed(player, 5.5)
