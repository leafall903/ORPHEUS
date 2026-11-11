"""Portable parent64bf71c route pause boundary; this does not prove native AI."""

import unittest

from test_bots import make_bot

from ow174.game import combat


class ControlRouteTests(unittest.TestCase):
    def test_route_retains_target_and_resumes_without_paused_time_catchup(self):
        def walk(advances):
            actor = make_bot([(0, 0, 0), (0, 0, 30)], index=0)
            actor.wait = 0
            for tick in range(1, 11):
                actor.step(tick, combat.TICK)
            position, target = actor.position, actor.target
            actor.step(20, combat.TICK, paused_until=31)
            self.assertEqual(actor.position, position)
            self.assertEqual(actor.target, target)
            self.assertEqual(actor.velocity, (0, 0, 0))
            for tick in advances:
                actor.step(tick, combat.TICK, paused_until=31)
            return actor.position, actor.velocity, actor.target

        self.assertEqual(walk(range(21, 41)), walk([40]))
