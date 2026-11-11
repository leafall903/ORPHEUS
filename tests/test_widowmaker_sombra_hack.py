"""Actual Sombra cast against Widow skill, weapon and world controls."""

import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import combat, sombra_control
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class WidowmakerSombraHackTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[sombra_control.HERO]))
        self.b.mover.position[:] = (0, 0, 2)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        self.press(0, 8, attacker=self.b, defender=self.a)

    def cast(self, emp):
        if emp:
            script = self.b.body_script
            script.set_ult_charge(script.component.vars[338].value())
        for n in range(80):
            self.press((32 if n == 0 else 0) if emp else 2, attacker=self.b, defender=self.a)
            if self.fight.sombra_hacks.silenced(self.a.body, self.fight.tick):
                return self.fight.sombra_hacks.active[self.a.body][-1][1]
        self.fail('Actual Sombra cast failed to silence Widowmaker')

    def test_actual_hack_and_emp_silence_skills_while_weapon_remains_live(self):
        for emp in (False, True):
            with self.subTest(emp=emp):
                self.setUp()
                end = self.cast(emp)
                self.assertEqual(end - self.fight.tick, 313)
                self.assertEqual(self.health(self.a), 175)
                script = self.a.body_script
                script.ult_seconds = 0
                charge = script.component.vars[338].value()
                script.set_ult_charge(charge)
                hp = self.health(self.b)
                direction = aim(self.fight._eye(self.a), (0, 1, 2))
                self.run.frame += 1
                self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
                self.a.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1],
                                              buttons=FIRE | 8 | 16 | 32, forward=127, extra=0)])
                self.assertLess(self.health(self.b), hp)
                self.assertEqual(script.component.vars[342].value(), charge)
                self.assertFalse(self.fight.venom.flights)
                self.assertIsNone(script.infra_window())
                owner_frame = script.frame
                self.run.frame = end
                self.run.match.update(end * combat.TICK, end)
                self.assertNotIn(self.a.body, self.fight.sombra_hacks.active)
                self.assertEqual(script.frame, owner_frame)
                self.a.take_commands([Command(end, buttons=32, extra=0)])
                self.assertEqual(script.component.vars[342].value(), 0)
                self.assertIsNotNone(script.infra_window())

    def ability(self):
        instance = next(i for i in self.b.body_script.component.instances.values()
                        if i.graph.index == sombra_control.HACK)
        return instance.state(53)

    def test_actual_widow_hit_interrupts_hack_and_starts_interruption_cooldown(self):
        self.press(2, 10, attacker=self.b, defender=self.a)
        ability = self.ability()
        self.assertTrue(ability.flags & ability.RUNNING)
        source = aim(self.fight._eye(self.b), (0, 1, 0))
        target = aim(self.fight._eye(self.a), (0, 1, 2))
        before = self.health(self.b)
        self.run.step({self.a: (*target, FIRE), self.b: (*source, 2)})
        self.assertEqual(self.health(self.b), before - 13)
        self.assertGreater(ability.remaining(), 0)
        self.press(2, 55, attacker=self.b, defender=self.a)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(self.fight.sombra_hacks.active, {})

    def test_channel_release_or_new_wall_rejects_application(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                self.setUp()
                self.press(2, 10, attacker=self.b, defender=self.a)
                self.assertTrue(self.ability().flags & self.ability().RUNNING)
                if wall:
                    self.run.match.collision_world = World([
                        (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                        (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                        (-5, 0, 1, 5, 0, 1, 5, 3, 1),
                        (-5, 0, 1, 5, 3, 1, -5, 3, 1),
                    ])
                self.press(2 if wall else 0, 60, attacker=self.b, defender=self.a)
                self.assertFalse(self.ability().instance.vars[8909].value())
                self.assertEqual(self.fight.sombra_hacks.active, {})
                self.press(0, 50, attacker=self.b, defender=self.a)
                self.assertFalse(self.ability().flags & self.ability().RUNNING)
