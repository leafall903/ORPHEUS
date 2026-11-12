"""External NPC motion: catch-up, route arbitration and rejected input."""

import unittest

from test_bots import make_bot

from ow174.game.collision import MOVER, flat_world


class BotExternalMotionTests(unittest.TestCase):
    def test_elapsed_ticks_match_individual_ticks_and_duplicate_tick_does_not_repeat_motion(self):
        world = flat_world(0)
        a, b = make_bot(), make_bot()
        for actor in (a, b):
            actor.apply_impulse((4, 6, 0), 0, world, category=MOVER)
        for tick in range(1, 31):
            a.step(tick, .016, world)
        b.step(30, .016, world)
        self.assertEqual((a.position, a.velocity), (b.position, b.velocity))
        history = list(b.impulse_mover.history)
        b.step(30, .016, world)
        self.assertEqual((a.position, a.velocity), (b.position, b.velocity))
        self.assertEqual(list(b.impulse_mover.history), history)
        for tick in range(31, 100):
            a.step(tick, .016, world, paused_until=70)
        b.step(99, .016, world, paused_until=70)
        self.assertEqual((a.position, a.velocity), (b.position, b.velocity))
        self.assertIsNone(a.impulse_mover)
        self.assertIsNone(b.impulse_mover)

    def test_route_pauses_during_force_then_resumes_current_target_without_catch_up(self):
        world = flat_world(0)
        actor = make_bot(((0, 0, 0), (0, 0, 8)), index=0)
        tick = 0
        while not actor.keys and tick < 500:
            tick += 1
            actor.step(tick, .016, world)
        self.assertTrue(actor.keys)
        target, index, wait = actor.target, actor.index, actor.wait
        actor.apply_impulse((4, 6, 0), tick, world, category=MOVER)
        limit = tick + 250
        while actor.impulse_mover is not None and tick < limit:
            tick += 1
            actor.step(tick, .016, world)
            self.assertEqual((actor.target, actor.index, actor.wait), (target, index, wait))
            self.assertEqual(actor.held, (0, 0))
        self.assertIsNone(actor.impulse_mover)
        position = actor.position
        actor.step(tick + 1, .016, world)
        self.assertNotEqual(actor.position, position)
        self.assertEqual(actor.target, target)
        self.assertLess(sum(abs(a - b) for a, b in zip(actor.position, position, strict=True)), .2)

    def test_invalid_force_does_not_allocate_or_mutate_and_dead_actor_cannot_move(self):
        actor, world = make_bot(), flat_world(0)
        before = (actor.position, actor.velocity, actor.last_tick)
        for value in ((1, 2), (float('nan'), 0, 0), (0, float('inf'), 0)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                actor.apply_impulse(value, 0, world, category=MOVER)
            self.assertIsNone(actor.impulse_mover)
            self.assertEqual((actor.position, actor.velocity, actor.last_tick), before)
        actor.died(1)
        actor.apply_impulse((4, 6, 0), 1, world, category=MOVER)
        actor.step(20, .016, world)
        self.assertIsNone(actor.impulse_mover)
        self.assertEqual(actor.position, before[0])
