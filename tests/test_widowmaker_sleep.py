"""Actual Ana dart admission and Widow command recovery in the selected profile."""

import unittest

from test_game_combat import SOLDIER, aim, in_world
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes

ANA = 0x02E000000000013B


class WidowmakerSleepTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[ANA]))
        self.b.mover.position[:] = (0, 0, 2)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        self.press(0, 10, attacker=self.b, defender=self.a)

    def advance(self, tick):
        self.run.frame = tick
        self.run.match.update(tick * combat.TICK, tick)

    def dart(self):
        self.press(8, attacker=self.b, defender=self.a)
        for _ in range(48):
            self.press(0, attacker=self.b, defender=self.a)
            if self.a.body in self.fight.sleeps:
                return self.fight.sleeps[self.a.body]
        self.fail('Actual Sleep Dart did not control Widowmaker')

    def test_actual_dart_blocks_movement_and_actions_until_world_deadline(self):
        start, end, waking = self.dart()
        self.assertEqual(self.health(self.a), 170)
        self.assertEqual(end - start, 313)
        self.assertFalse(waking)
        self.advance(end - 1)
        before = tuple(self.a.mover.position)
        script = self.a.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        enemy_hp = self.health(self.b)
        direction = aim(self.fight._eye(self.a), (0, 1, 2))
        self.a.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1], forward=127,
                                      buttons=FIRE | SCOPE | MELEE | 8 | 16 | 32, extra=0)])
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertEqual(tuple(self.a.mover.position), before)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertFalse(script.component.held)
        self.assertEqual(self.health(self.b), enemy_hp)
        self.advance(end)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertNotIn(self.a.body, self.fight.sleeps)
        self.a.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1],
                                      buttons=FIRE, extra=0)])
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        self.assertLess(self.health(self.b), enemy_hp)

    def test_actual_rifle_damage_after_grace_shortens_sleep_to_wake_phase(self):
        attacker = self.run.match.add_player(3, 'Rifle', SOLDIER, self.b.team, False)
        in_world(self.run.match, attacker)
        self.assertTrue(self.run.match.switch_hero(attacker, heroes()[SOLDIER]))
        attacker.mover.position[:] = (2, 0, 0)
        attacker.mover.body = None
        self.run.players.append(attacker)
        start, end, _ = self.dart()
        self.press(FIRE, attacker=attacker, defender=self.a)
        self.assertLess(self.health(self.a), 170)
        self.assertEqual(self.fight.sleeps[self.a.body], (start, end, False))
        self.press(0, attacker=attacker, defender=self.a)
        self.advance(start + 39)
        self.press(FIRE, attacker=attacker, defender=self.a)
        woke = self.fight.tick + 32
        self.assertEqual(self.fight.sleeps[self.a.body], (start, woke, True))
        self.press(FIRE, 12, attacker=attacker, defender=self.a)
        self.assertEqual(self.fight.sleeps[self.a.body], (start, woke, True))
        self.advance(woke - 1)
        self.assertTrue(self.fight.is_stunned(self.a))
        self.advance(woke)
        self.assertFalse(self.fight.is_stunned(self.a))
