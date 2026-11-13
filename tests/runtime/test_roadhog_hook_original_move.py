"""Actual Hook resolves original relative MoveTo and returns one capsule."""
import math
import unittest

import test_delivered_roadhog_hook as fixtures
from test_roadhog_public_force_producer import CONTRACT
from ow174.game import combat
from ow174.game.script import expr


class RoadhogOriginalMoveTests(unittest.TestCase):
    def test_actual_hook_uses_victim_move_request_and_returns_hittable_body(self):
        helper = fixtures.HookTests()
        run, hog, victim, _, pool = helper.session()
        fight = run.match.combat
        helper.step(run, hog, victim, shift=True)
        saw_move = False
        moving_steps = 0
        flight = None
        for _ in range(120):
            previous = tuple(victim.mover.position)
            current = fight.hooks.flights.get(hog.body)
            request = None
            if current is not None and current.victim_instance is not None:
                graph = current.victim_instance
                state = graph.states.get(graph.graph.node(0).state)
                if state is not None and state.active:
                    # This failed as 0 before the caster collection/scalar bridge.
                    self.assertEqual(state.evaluate(state.node.field('m_speed'), watch=False),
                                     CONTRACT['expected']['hook_move_speed'])
                    request = fight.hooks.victim_move(current)
                    self.assertIsNotNone(request)
                    self.assertIsInstance(graph.find_var(0, 9482).value(), expr.Vec3)
                    self.assertGreater(graph.find_var(0, 9482).value().z, .99)
                    saw_move = True
            helper.step(run, hog, victim)
            flight = current or flight
            distance = math.dist(previous, victim.mover.position)
            if request is not None and current.arrived_at is None:
                self.assertAlmostEqual(distance, request[1] * combat.TICK, delta=.003)
                moving_steps += 1
            elif current is not None and current.arrived_at is None:
                self.assertLess(distance, .001)
        self.assertTrue(saw_move)
        self.assertGreater(moving_steps, 5)
        self.assertIsNotNone(flight.arrived_at)
        self.assertFalse(fight.hooks.controlled(victim.body))
        self.assertFalse(victim.mover.driven)
        self.assertEqual(victim.mover.velocity, (0, 0, 0))
        self.assertNotIn(flight.victim_instance.id, flight.victim_instance.component.instances)
        self.assertEqual(pool.total, 170)
        target = next(t for t in fight._targets() if t.owner is victim)
        low, high = target.segment()
        look = fixtures.aim(fight._eye(hog), tuple((a + b) / 2 for a, b in zip(low, high, strict=True)))
        run.step({hog: (*look, 1)})
        for _ in range(30):
            run.step({})
        self.assertLess(pool.total, 170)
