"""Wall Ride through real player commands and static triangle collision."""

import unittest
from unittest.mock import patch

import test_lucio_combat as fixtures

from ow174.game import combat, wall_ride
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.physics_snapshot import snapshot_mover


class LucioWallRideTests(unittest.TestCase):
    def scene(self, *, wall_x=.45, top=8., start=-8., extra=()):
        run, player, _, _ = fixtures.LucioCombatTests().scenario()
        run.match.collision_world = World([
            (-50, 1, -50, -50, 1, 50, 50, 1, 50),
            (-50, 1, -50, 50, 1, 50, 50, 1, -50),
            (wall_x, 1, start, wall_x, top, start, wall_x, top, 6),
            (wall_x, 1, start, wall_x, top, 6, wall_x, 1, 6),
            *extra,
        ], name="Lucio finite side wall")
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0263)
        for _ in range(80):
            self.step(run, player, forward=0)
        for _ in range(10):
            self.step(run, player)
        return run, player, instance

    def step(self, run, player, buttons=0, *, forward=127):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        player.take_commands([Command(run.frame, forward=forward, buttons=buttons)])

    def test_sustained_ride_uses_ground_friction_and_acceleration(self):
        run, player, _ = self.scene()
        friction = wall_ride.stationary_friction
        samples = []

        def record(velocity, *args):
            result = friction(velocity, *args)
            samples.append((velocity, result))
            return result

        with (patch.object(wall_ride, "stationary_friction", side_effect=record),
              patch.object(wall_ride, "accelerate_tangent", wraps=wall_ride.accelerate_tangent) as accelerate):
            for _ in range(50):
                self.step(run, player, JUMP)
                if samples:
                    break

        self.assertTrue(samples)
        before, after = samples[0]
        self.assertLess(after[1], before[1])
        self.assertEqual(accelerate.call_args.args[0], after)
        values = player.mover.values
        self.assertAlmostEqual(accelerate.call_args.args[3], values.accel_ground * values.run_forward)
        self.assertTrue(player.mover.airborne)
        self.assertGreaterEqual(player.mover.velocity[1], 3.0)

    def test_finite_wall_contact_moves_and_drives_the_authored_release_jump(self):
        run, player, instance = self.scene()
        kinds = []
        sustained_height = None
        for _ in range(70):
            self.step(run, player, JUMP)
            contact = player.mover.wall_contact
            if contact is None:
                if kinds:
                    break
                continue
            self.assertIs(instance.component.entity_var(1204).value(), True)
            self.assertEqual(instance.vars[14980].value(), contact[1])
            self.assertEqual(instance.vars[14965].value(), 1)
            kinds.append(contact[1])
            if contact[1] == 3 and sustained_height is None:
                sustained_height = player.mover.position[1]
        self.assertEqual(list(dict.fromkeys(kinds)), [2, 3, 4])
        self.assertGreater(player.mover.position[1], sustained_height + .5)
        self.assertGreater(player.mover.position[2], 6)
        self.assertLess(player.mover.position[0], .45)
        self.assertIsNone(player.mover.wall_contact)
        self.assertIs(instance.component.entity_var(1204).value(), False)
        self.assertEqual(instance.vars[14980].value(), 1)
        self.assertTrue(instance.state(12).active)
        self.assertFalse(player.mover.velocity_changes)

        self.step(run, player)
        self.assertEqual(len(player.mover.velocity_changes), 2)
        self.assertTrue(all(change.frame == run.frame + 1 for change in player.mover.velocity_changes))
        before_speed = player.mover.velocity[2]
        self.step(run, player)
        self.assertFalse(player.mover.velocity_changes)
        self.assertGreater(player.mover.velocity[2], before_speed + 2)

    def test_low_and_distant_walls_do_not_acquire(self):
        for options in ({"top": 1.6}, {"wall_x": .8}):
            with self.subTest(**options):
                run, player, instance = self.scene(**options)
                for _ in range(35):
                    self.step(run, player, JUMP)
                    self.assertIsNone(player.mover.wall_contact)
                slot = instance.component.entity_var(1204, create=False)
                self.assertFalse(slot is not None and slot.value())

    def test_release_while_still_on_wall_ends_controller_and_jumps(self):
        run, player, instance = self.scene()
        for _ in range(23):
            self.step(run, player, JUMP)
        mover = player.mover
        contact = mover.wall_contact
        self.assertEqual(contact[1], 3)
        before = snapshot_mover(mover)
        mover.wall_contact = (contact[0] - 1, *contact[1:])
        self.assertNotEqual(snapshot_mover(mover), before)
        mover.wall_contact = contact
        self.assertEqual(snapshot_mover(mover), before)

        self.step(run, player)
        self.assertFalse(instance.state(8).active)
        self.step(run, player)
        self.assertIsNone(mover.wall_contact)
        self.assertEqual(len(mover.velocity_changes), 2)
        self.step(run, player)
        self.assertFalse(mover.velocity_changes)

    def test_adjacent_faces_transfer_without_restarting_the_ride(self):
        run, player, instance = self.scene(extra=(
            (-4, 1, 5.6, .45, 1, 5.6, .45, 8, 5.6),
            (-4, 1, 5.6, .45, 8, 5.6, -4, 8, 5.6),
        ))
        first_sustained = None
        blended_corner = False
        for _ in range(70):
            self.step(run, player, JUMP)
            contact = player.mover.wall_contact
            if contact is None:
                self.assertIsNone(first_sustained)
                continue
            entered, kind, normal, _ = contact
            if kind == 4 and normal[0] < -.9 and first_sustained is None:
                first_sustained = entered
            if first_sustained is not None and normal[0] < -.1 and normal[2] < -.1:
                blended_corner = True
            if normal[2] < -.9:
                break
        else:
            self.fail("The command path did not reach the front face")
        self.assertIsNotNone(first_sustained)
        self.assertTrue(blended_corner)
        self.assertEqual(kind, 4)
        self.assertLessEqual(entered, first_sustained)
        self.assertIs(instance.component.entity_var(1204).value(), True)
        self.assertFalse(player.mover.velocity_changes)
        self.assertLess(player.mover.position[2], 5.6)

    def test_gap_reacquires_after_authored_half_second_without_input_bypass(self):
        for early_repress in (False, True):
            with self.subTest(early_repress=early_repress):
                run, player, instance = self.scene(extra=(
                    (.45, 1, 7, .45, 8, 7, .45, 8, 16),
                    (.45, 1, 7, .45, 8, 16, .45, 1, 16),
                ))
                saw_contact = False
                for _ in range(70):
                    self.step(run, player, JUMP)
                    if player.mover.wall_contact is not None:
                        saw_contact = True
                    elif saw_contact:
                        break
                else:
                    self.fail("The first wall did not end before the second")
                self.assertTrue(instance.state(14).active)
                self.assertIs(instance.component.entity_var(1347).value(), True)

                for elapsed in range(1, 32):
                    self.step(run, player, 0 if early_repress and elapsed == 2 else JUMP)
                    self.assertIsNone(player.mover.wall_contact)
                self.assertFalse(instance.state(14).active)
                self.assertGreater(player.mover.position[2], 7.)
                self.step(run, player, JUMP)
                self.assertEqual(player.mover.wall_contact[:2], (run.frame, 2))
                self.assertIs(instance.component.entity_var(1204).value(), True)

    def test_force_clock_starts_before_delayed_contact(self):
        run, player, instance = self.scene(start=3.)
        self.step(run, player, JUMP)
        self.assertTrue(instance.state(8).active)
        controller_frame = run.frame + 1
        with patch.object(wall_ride, "wall_force", wraps=wall_ride.wall_force) as force:
            for _ in range(35):
                self.step(run, player, JUMP)
                if player.mover.wall_contact is not None:
                    break
        self.assertIsNotNone(player.mover.wall_contact)
        self.assertGreater(run.frame, controller_frame + 5)
        self.assertEqual(force.call_count, 1)
        self.assertAlmostEqual(force.call_args.args[3], (run.frame - controller_frame) * combat.TICK)

    def test_default_sustained_request_uses_both_wall_sides_with_reverse_input(self):
        for x, expected_side in ((.45, 1), (-.45, 2)):
            with self.subTest(wall_x=x):
                run, player, instance = self.scene(wall_x=x)
                for _ in range(30):
                    self.step(run, player, JUMP)
                self.assertEqual(player.mover.wall_contact[1], 4)
                self.assertEqual(instance.vars[14965].value(), expected_side)
                start = player.mover.position[2]
                for _ in range(5):
                    self.step(run, player, JUMP, forward=-127)
                    self.assertEqual(player.mover.wall_contact[1], 4)
                    self.assertGreater(player.mover.velocity[2], 0.)
                self.assertGreater(player.mover.position[2], start)

    def test_control_and_death_clear_contact_without_a_wall_jump(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, player, instance = self.scene()
                for _ in range(23):
                    self.step(run, player, JUMP)
                self.assertEqual(player.mover.wall_contact[1], 3)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is player)
                if lethal:
                    fight._damage(run.players[1], target, 500, False, None, run.frame)
                else:
                    fight._hit_effect(target, combat.FLASHBANG_EFFECT)
                self.assertIsNone(player.mover.wall_contact)
                self.assertIs(instance.component.entity_var(1204).value(), False)
                self.step(run, player)
                self.assertFalse(player.mover.velocity_changes)


if __name__ == "__main__":
    unittest.main()
