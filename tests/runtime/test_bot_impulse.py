"""External capsule motion must survive controls, stop on death and resume route intent."""

import unittest

from test_bots import make_bot

from ow174.game import bots
from ow174.game.collision import MOVER, TEAM0, TEAM1, World, flat_world
from ow174.game.mover import IN_AIR


class BotImpulseTests(unittest.TestCase):
    def test_team_only_obstacle_and_ffa_use_same_proxy_policy_as_players(self):
        from test_game_mover import box

        floor = box(-30, 30, -1, 0, -30, 30)
        wall = box(-8, 8, 0, 6, 3, 3.3)
        world = World(floor + wall, masks=[MOVER] * len(floor) + [TEAM1] * len(wall))
        for team, ffa, blocked in ((0, False, False), (1, False, True), (0, True, True)):
            with self.subTest(team=team, ffa=ffa):
                bot = make_bot()
                bot.team = team
                bot.impulse((0, 3, 25), 0, world, free_for_all=ffa)
                for at in range(1, 100):
                    bot.step(at, .016, world)
                self.assertEqual(bot.position[2] < 3, blocked)

    def test_repeated_impact_updates_proxy_and_invalidates_cached_capsule(self):
        bot = make_bot()
        bot.team = 0
        bot.impulse((0, 3, 25), 0)
        bot.step(1, .016)
        mover = bot.impulse_mover
        self.assertEqual(mover.category, TEAM0)
        self.assertIsNotNone(mover.body)
        bot.team = 1
        bot.impulse((0, 3, 25), 1)
        self.assertIs(bot.impulse_mover, mover)
        self.assertEqual(mover.category, TEAM1)
        self.assertIsNone(mover.body)
        bot.step(2, .016)
        self.assertEqual(mover.body.category, TEAM1)

    def test_control_pause_preserves_motion_duplicate_ticks_do_not_advance(self):
        bot = make_bot()
        bot.impulse((0, 3, 25), 10)
        bot.step(11, .016, paused_until=100)
        position = bot.position
        self.assertGreater(position[1], 0)
        self.assertGreater(position[2], 0)
        self.assertEqual(bot.movement(11).throttles, (0, 0))
        self.assertTrue(bot.movement(11).flags & IN_AIR)
        bot.step(11, .016, paused_until=100)
        self.assertEqual(bot.position, position)
        for at in range(12, 100):
            bot.step(at, .016, paused_until=100)
        self.assertIsNone(bot.impulse_mover)
        self.assertGreater(bot.position[2], position[2])
        self.assertEqual(bot.velocity, (0, 0, 0))

    def test_repeated_impulse_keeps_physics_state_death_and_respawn_clear_it(self):
        bot = make_bot()
        bot.impulse((0, 3, 25), 10)
        bot.step(11, .016)
        mover = bot.impulse_mover
        before = bot.velocity
        bot.impulse((4, 1, 0), 11)
        self.assertIs(bot.impulse_mover, mover)
        self.assertEqual(bot.velocity, (before[0] + 4, before[1], before[2]))
        bot.died(12)
        position = bot.position
        bot.step(80, .016)
        self.assertEqual(bot.position, position)
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(bot.velocity, (0, 0, 0))
        fresh = bots.respawned(bot, bot.entity + 1, 81)
        self.assertIsNone(fresh.impulse_mover)
        self.assertFalse(fresh.dead)

    def test_route_waits_during_impulse_then_resumes_toward_original_target(self):
        bot = make_bot([(0, 0, 0), (0, 0, 20)], index=0)
        bot.wait = 0
        world = flat_world(0)
        bot.step(1, .016, world)
        self.assertTrue(bot.keys)
        target, wait = bot.target, bot.wait
        bot.impulse((15, 3, 0), 1, world)
        bot.step(2, .016, world)
        self.assertEqual(bot.target, target)
        self.assertEqual(bot.wait, wait)
        for at in range(3, 150):
            bot.step(at, .016, world)
        self.assertIsNone(bot.impulse_mover)
        displaced_x = bot.position[0]
        self.assertGreater(displaced_x, 0)
        bot.step(150, .016, world)
        self.assertLess(bot.position[0], displaced_x)
        self.assertEqual(bot.target, target)
