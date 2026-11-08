"""Actual command-to-pool regressions; these are not original-client acceptance.

Use extracted 104319 body/weapon graphs, flat collision and acknowledged fake
peers. NPC coverage here is a training bot, not the five Archives enemy classes.
"""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots, combat
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes

WIDOWMAKER = 0x02E000000000000A
FIRE, SCOPE, MELEE = 1, 2, 0x800


class WidowmakerCombatTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Widowmaker", "Enemy"))
        self.a, self.b = self.run.players
        self.run.match.switch_hero(self.a, heroes()[WIDOWMAKER])
        self.run.match.bots.clear()
        self.a.mover.position[:] = (0.0, 0.0, 0.0)
        self.b.mover.position[:] = (0.0, 0.0, 1.5)
        self.run.match.collision_world = flat_world(0)
        for _ in range(60):
            self.run.step({})

    def target(self, owner):
        return next(t for t in self.run.match.combat._targets() if t.owner is owner)

    def health(self, owner):
        return self.run.match.combat.vitals_of(self.target(owner)).total

    def press(self, buttons, frames=1, *, attacker=None, defender=None):
        attacker, defender = attacker or self.a, defender or self.b
        x, y, z = defender.mover.position
        direction = aim(self.run.match.combat._eye(attacker), (x, y + 1.0, z))
        for _ in range(frames):
            self.run.step({attacker: (*direction, buttons)})

    def test_primary_body_hit_and_release(self):
        self.press(FIRE)
        self.press(0, 20)
        self.assertEqual(self.health(self.b), 187.0)
        self.assertEqual(self.a.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 13)

    def test_full_charge_scoped_body_hit_consumes_five_ammo(self):
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        before = weapon.vars[53].value()
        self.press(SCOPE, 100)
        self.press(FIRE | SCOPE)
        self.assertEqual(self.health(self.b), 80.0)
        self.assertEqual(before - weapon.vars[53].value(), 5)

    def test_partial_charge_is_weaker_than_full_charge(self):
        self.press(SCOPE, 25)
        self.press(FIRE | SCOPE)
        dealt = 200.0 - self.health(self.b)
        self.assertGreaterEqual(dealt, 12.0)
        self.assertLess(dealt, 120.0)

    def test_allied_player_is_not_damaged(self):
        self.b.team = self.a.team
        self.press(FIRE, 30)
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_quick_melee_damages_enemy_once(self):
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 170.0)
        self.assertEqual([h["+0x78"]["+0x4"] for h in self.a.client.messages(combat.HIT)], [30])

    def test_melee_does_not_hit_through_wall(self):
        self.run.match.collision_world = World([
            (-5, 0, -5, -5, 0, 5, 5, 0, 5),
            (-5, 0, -5, 5, 0, 5, 5, 0, -5),
            (-5, 0, 0.75, 5, 0, 0.75, 5, 5, 0.75),
            (-5, 0, 0.75, 5, 5, 0.75, -5, 5, 0.75),
        ], name="melee wall")
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_enemy_player_can_kill_widowmaker_and_dead_input_cannot_fire(self):
        self.press(FIRE, 100, attacker=self.b, defender=self.a)
        self.assertTrue(self.run.match.combat.is_dead(self.a))
        self.assertTrue(self.a.body_script.component.owner_dead)
        self.assertTrue(self.a.movement().flags & combat.DEAD)
        self.assertTrue(self.a.client.messages(combat.DEATH_NOTICE))
        self.press(FIRE, 30)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_manual_training_bot_corpse_stays_after_widowmaker_kills_it(self):
        # An actual Bot target through the same weapon command path. This does not
        # stand in for Archives AI, native body rendering, or their special attacks.
        from ow174.game.content import PRACTICE_RANGE

        other = Run(PRACTICE_RANGE)
        bot = next(bot for bot in other.match.bots if bot.team != other.players[0].team)
        bot.position = (0.0, 0.0, 1.5)
        bot.route = []
        bot.firing = False
        bot.retain_corpse = True
        self.run.match.bots[:] = [bot]
        self.b.team = self.a.team
        for _ in range(2):
            self.press(SCOPE, 100)
            self.press(FIRE | SCOPE)
            self.press(SCOPE, 60)
        self.assertTrue(bot.dead)
        for _ in range(round(combat.BOT_RESPAWN / combat.TICK) + 10):
            self.run.step({})
        self.assertIn(bot, self.run.match.bots)
        self.assertEqual(self.health(bot), 0)
        self.assertTrue(bot.movement(self.run.frame).flags & bots.DEAD)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertFalse(any(who is bot for _, who in self.run.match.combat.respawns))


if __name__ == "__main__":
    unittest.main()
