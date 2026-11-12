"""One-shot movement foundation for actual0263 wall-jump velocity actions.

Full Wall Ride/action dispatch is not implemented by these tests. Original
C32860/C329C0/C31870 native comparisons are separate lane evidence.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import physics_snapshot, velocity
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.mover import ADD, FELL, IN_AIR, JUMPED, MULTIPLY, SET, Mover, MoverData


class LucioVelocityTests(unittest.TestCase):
    def mover(self):
        data = replace(MoverData(), accel_air=0., accel_air_touching=0.,
                       decel_air_base=0., decel_air_per_speed=0.)
        actor = Mover((0., 10., 0.), world=flat_world(0.), data=data)
        actor.flags_word |= IN_AIR
        actor.air_ticks = 1
        actor.velocity = (1., 3., 2.)
        return actor

    def test_authored_vertical_multiplier_runs_after_gravity_before_translation(self):
        actor = self.mover()
        actor.velocity_changes.append(velocity.numeric(1, 3, MULTIPLY, 1.15))
        actor.step(Command(1), .016)
        self.assertAlmostEqual(actor.velocity[1], (3. - 17.5 * .016) * 1.15, delta=.001)
        self.assertAlmostEqual(actor.position[1], 10. + actor.velocity[1] * .016, delta=.002)
        self.assertEqual(actor.velocity_changes, [])

    def test_vector_set_captures_enqueue_velocity_and_runs_after_speed_cap(self):
        actor = self.mover()
        actor.velocity_changes.append(velocity.vector(1, SET, (4., 7., 8.), actor.velocity))
        actor.step(Command(1), .016)
        self.assertAlmostEqual(actor.velocity[0], 4., delta=.001)
        self.assertAlmostEqual(actor.velocity[1], 7. - 17.5 * .016, delta=.001)
        self.assertAlmostEqual(actor.velocity[2], 8., delta=.001)

    def test_upward_change_detaches_ground_before_the_collision_move(self):
        actor = Mover((0., 0., 0.), world=flat_world(0.))
        actor.velocity_changes.append(velocity.vector(1, ADD, (0., 6., 0.), actor.velocity))
        actor.step(Command(1), .016)
        self.assertTrue(actor.flags_word & FELL)
        self.assertFalse(actor.flags_word & JUMPED)
        self.assertGreater(actor.position[1], .05)
        self.assertAlmostEqual(actor.velocity[1], 6., delta=.001)

    def test_future_frame_preserved_and_active_request_does_not_repeat(self):
        actor = self.mover()
        actor.velocity_changes.append(velocity.vector(2, ADD, (2.5, 0., 0.), actor.velocity))
        actor.step(Command(1), .016)
        self.assertAlmostEqual(actor.velocity[0], 1., delta=.001)
        self.assertEqual(len(actor.velocity_changes), 1)
        actor.step(Command(3), .016)  # recovers frame2, consumes once before frame3
        self.assertAlmostEqual(actor.velocity[0], 3.5, delta=.001)
        self.assertEqual(actor.velocity_changes, [])

    def test_expired_request_is_not_shifted_to_a_later_pose(self):
        actor = self.mover()
        actor.velocity_changes.append(velocity.vector(2, ADD, (2.5, 0., 0.), actor.velocity))
        with patch.object(velocity, "consume", wraps=velocity.consume) as consume:
            actor.step_pose(Command(3), .016, 3)
        consume.assert_not_called()
        self.assertAlmostEqual(actor.velocity[0], 1., delta=.001)
        self.assertEqual(actor.velocity_changes, [])

    def test_numeric_precedes_vector_even_if_requested_in_reverse_order(self):
        changes = [velocity.vector(4, SET, (4., 7., 8.), (1., 3., 2.)),
                   velocity.numeric(4, 3, MULTIPLY, 1.15)]
        result = velocity.consume((1., 3., 2.), 6., changes, 4)
        self.assertAlmostEqual(result[1], 7.45, places=5)
        self.assertEqual((result[0], result[2]), (4., 8.))
        self.assertEqual(velocity.consume((1., 3., 2.), 0., changes, 4), (4., 7., 8.))

    def test_pending_changes_are_in_stored_physics_snapshot(self):
        actor = self.mover()
        before = physics_snapshot.snapshot_mover(actor)
        actor.velocity_changes.extend((velocity.numeric(2, 3, MULTIPLY, 1.15),
                                       velocity.vector(2, ADD, (2.5, 0., 0.), actor.velocity)))
        pending = physics_snapshot.snapshot_mover(actor)
        self.assertNotEqual(before, pending)
        self.assertEqual(pending, physics_snapshot.snapshot_mover(actor))
        actor.velocity_changes.clear()
        self.assertEqual(before, physics_snapshot.snapshot_mover(actor))

    def test_invalid_requests_fail_before_entering_mover_queue(self):
        for args in ((-1, 3, MULTIPLY, 1.), (1, 4, MULTIPLY, 1.), (1, 3, 5, 1.),
                     (1, 3, MULTIPLY, float('nan'))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                velocity.numeric(*args)
        with self.assertRaises(ValueError):
            velocity.vector(1, ADD, (float('inf'), 0., 0.), (0., 0., 0.))

    def test_same_frame_key_overwrites_without_reapplying_the_delta(self):
        changes = []
        first = replace(velocity.vector(4, ADD, (2.5, 0., 0.), (1., 3., 2.)), key=7)
        velocity.enqueue(changes, first)
        velocity.enqueue(changes, replace(first, value=(4., 0., 0.)))
        self.assertEqual(len(changes), 1)
        self.assertEqual(velocity.consume((1., 3., 2.), 6., changes, 4), (5., 3., 2.))

    def test_numeric_vector_keys_are_separate_and_frame_replacement_removes_old_row(self):
        changes = []
        scalar = replace(velocity.numeric(4, 3, MULTIPLY, 1.15), key=7)
        vector = replace(velocity.vector(4, ADD, (2.5, 0., 0.), (1., 3., 2.)), key=7)
        velocity.enqueue(changes, scalar)
        velocity.enqueue(changes, vector)
        velocity.enqueue(changes, replace(scalar, frame=5))
        self.assertEqual(len(changes), 2)
        self.assertEqual(velocity.consume((1., 3., 2.), 6., changes, 4), (3.5, 3., 2.))
        self.assertAlmostEqual(velocity.consume((1., 3., 2.), 6., changes, 5)[1], 3.45, places=5)

    def test_priority_order_and_same_frame_priority_change_preserve_native_position(self):
        changes = []
        set_row = replace(velocity.numeric(4, 2, SET, 10.), key=9, priority=2.)
        multiply_row = replace(velocity.numeric(4, 2, MULTIPLY, 2.), key=8, priority=0.)
        velocity.enqueue(changes, set_row)
        velocity.enqueue(changes, multiply_row)
        self.assertEqual(velocity.consume((1., 3., 2.), 6., changes, 4)[1], 10.)
        velocity.enqueue(changes, replace(multiply_row, priority=3.))
        self.assertEqual(velocity.consume((1., 3., 2.), 6., changes, 4)[1], 10.)
        with self.assertRaises(ValueError):
            velocity.enqueue(changes, velocity.numeric(4, 2, SET, 3.))


if __name__ == '__main__':
    unittest.main()
