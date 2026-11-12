"""Actual copied-body input restrictions; complete native Roll movement remains open."""
import math
import unittest

import test_echo_combat as echo_tests
import test_echo_movement as movement

from ow174.game.content import heroes
from ow174.game.mover import MOVEMENT_MOD


class EchoInputModTests(unittest.TestCase):
    def test_actual_copied_cassidy_input_is_suppressed_then_restored(self):
        for forward, right in ((0, -127), (0, 127), (-127, 0), (127, 0), (127, 127), (-127, -127)):
            with self.subTest(forward=forward, right=right):
                run, echo, enemy, _, angles = echo_tests.EchoPrimaryTests().session()
                run.match.switch_hero(enemy, heroes()[0x02E0000000000042], preserve_pose=True)
                echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
                run.step({echo: (*angles, 32)})
                self.assertTrue(run.match.combat.echo_duplicates.active(echo))
                self.assertEqual(echo.hero.guid, 0x02E0000000000042)
                for _ in range(25):
                    movement.EchoMovementTests.step(run, echo)
                instance = next(i for i in echo.body_script.component.instances.values()
                                if i.graph.index == 0x1F0)
                movement.EchoMovementTests.step(run, echo, 8)
                phase = next(s for s in instance.states.values() if s.node.cls == MOVEMENT_MOD
                             and any(m.get('m_A9561CA0') == 19
                                     for m in s.node.field('m_BC5E91CF') or ()))
                self.assertTrue(phase.active)
                before = tuple(echo.mover.position)
                for _ in range(3):
                    movement.EchoMovementTests.step(run, echo, forward=forward, right=right)
                    self.assertTrue(phase.active)
                    self.assertEqual(echo.mover.throttles, (right, forward))
                    self.assertAlmostEqual(echo.mover.position[0], before[0], delta=.001)
                    self.assertAlmostEqual(echo.mover.velocity[0], 0, delta=.001)
                    self.assertAlmostEqual(echo.mover.velocity[2], 15, delta=.001)
                for _ in range(50):
                    movement.EchoMovementTests.step(run, echo, forward=forward, right=right)
                    if not phase.active:
                        break
                self.assertFalse(phase.active)
                # State end is observed after this pose; its mod end is the next frame.
                self.assertAlmostEqual(echo.mover.position[0], before[0], delta=.001)
                self.assertAlmostEqual(echo.mover.position[2]-before[2], 6, delta=.02)
                movement.EchoMovementTests.step(run, echo, forward=forward, right=right)
                self.assertLessEqual(math.hypot(echo.mover.velocity[0], echo.mover.velocity[2]), 5.51)
                for _ in range(10):
                    movement.EchoMovementTests.step(run, echo, forward=forward, right=right)
                self.assertGreater(math.hypot(echo.mover.velocity[0], echo.mover.velocity[2]), .1)
                if right:
                    self.assertLess(echo.mover.velocity[0]*right, 0)
                if forward:
                    self.assertGreater(echo.mover.velocity[2]*forward, 0)

    def test_actual_flight_takeoff_ignores_raw_throttles_until_next_pose_after_scope_end(self):
        fixture = movement.EchoMovementTests()
        run0, player0 = fixture.session()
        run1, player1 = fixture.session()
        previous_scope = False
        observed = False
        for tick in range(20):
            fixture.step(run0, player0, 8 if tick == 0 else 0)
            fixture.step(run1, player1, 8 if tick == 0 else 0, forward=-127, right=127)
            instance = next(i for i in player1.body_script.component.instances.values()
                            if i.graph.index == 0x1BB6)
            scope = instance.states[23].active
            self.assertEqual(player1.mover.throttles, (127, -127))
            distance = math.dist(player0.mover.velocity, player1.mover.velocity)
            if tick > 0 and (scope or previous_scope):
                self.assertLess(distance, .001)
            elif tick > 0:
                self.assertGreater(distance, 1)
                observed = True
                break
            previous_scope = scope
        self.assertTrue(observed)
