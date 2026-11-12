"""Actual0263 action nodes with declared invocation, not Wall Ride acceptance.

The real input gate, controller registration and continuous wall contact remain
open. These tests run real resource expressions, enqueue, mover and collision.
"""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_lucio_combat as fixtures

from ow174.game import lucio_movement
from ow174.game.commands import Command
from ow174.game.mover import IN_AIR
from ow174.game.script import expr, graph


@unittest.skipUnless(graph.graph(0x0263), "requires actual0263 graph")
class LucioWallJumpTests(unittest.TestCase):
    def scenario(self):
        run, player, _, _ = fixtures.LucioCombatTests().scenario()
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x0263)
        # Authored entry normally supplies this; direct node invocation is the fixture.
        instance.write((expr.INSTANCE, 10395), 2.5)
        actor = player.mover
        actor.position[:] = (0., 100., 0.)
        actor.yaw = 0
        actor.velocity = (0., 3., 4.)
        actor.flags_word |= IN_AIR
        actor.air_ticks = 1
        actor.data = replace(actor.data, accel_air=0., accel_air_touching=0.,
                             decel_air_base=0., decel_air_per_speed=0.)
        return run, player, instance

    def test_three_authored_direction_expressions_queue_and_move_next_frame_once(self):
        for position, expected in ((60, (-1.5, 0., 2.)), (61, (0., 1.5, 2.)),
                                   (63, (0., -math.sqrt(.5) * 2.5, math.sqrt(.5) * 2.5))):
            with self.subTest(position=position):
                _, player, instance = self.scenario()
                actor = player.mover
                actor.pitch = 8192
                instance.component.world.throttles = (124, 93)  # real-byte normalized direction3:4
                frame = instance.component.frame
                node = instance.graph.node(position)
                before = actor.velocity
                with patch.object(instance, "follow", wraps=instance.follow) as follow:
                    self.assertTrue(instance.run_node(node, None))
                    self.assertTrue(instance.run_node(node, None))  # same native key: overwrite
                self.assertEqual(follow.call_count, 2)
                self.assertEqual(actor.velocity, before)
                self.assertEqual(len(actor.velocity_changes), 2)
                scalar, vector = actor.velocity_changes
                self.assertEqual((scalar.frame, vector.frame), (frame + 1, frame + 1))
                key = (instance.id << 48) | (node.rid << 32) | (((frame + 1) & 65535) << 16)
                self.assertEqual((scalar.key, vector.key), (key, key | 1))
                for actual, wanted in zip(vector.value, expected, strict=True):
                    self.assertAlmostEqual(actual, wanted, delta=2e-5)
                actor.step_pose(Command(frame + 1, pitch=8192), .016, frame + 1)
                target = (expected[0], (3. - 17.5 * .016) * 1.15 + expected[1], 4. + expected[2])
                for actual, wanted in zip(actor.velocity, target, strict=True):
                    self.assertAlmostEqual(actual, wanted, delta=.002)
                self.assertGreater(actor.position[2], 0.)
                self.assertEqual(actor.velocity_changes, [])

    def test_real_input_throttle_bytes_feed_the_authored_basis_expression(self):
        _, player, instance = self.scenario()
        script = player.body_script
        script.command(script.frame + 1, 0, forward=127, right=-127)
        instance.run_node(instance.graph.node(60), None)
        vector = player.mover.velocity_changes[1]
        self.assertAlmostEqual(vector.value[0], 2.5 * math.sqrt(.5), delta=2e-5)
        self.assertAlmostEqual(vector.value[2], 2.5 * math.sqrt(.5), delta=2e-5)

    def test_key_uses_low_next_frame_bits_without_truncating_consumer_frame(self):
        _, player, instance = self.scenario()
        instance.component.frame = 65535
        instance.run_node(instance.graph.node(63), None)
        for index, row in enumerate(player.mover.velocity_changes):
            self.assertEqual(row.frame, 65536)
            self.assertEqual(row.key & 0xFFFFFFFF, index)

    def test_missing_live_owner_follows_output_without_writing_old_mover(self):
        run, player, instance = self.scenario()
        run.match.players.remove(player)
        with patch.object(instance, "follow", wraps=instance.follow) as follow:
            self.assertTrue(instance.run_node(instance.graph.node(63), None))
        follow.assert_called_once()
        self.assertEqual(player.mover.velocity_changes, [])

    def test_native_verified_yaw_pitch_queries_and_scope_rejection(self):
        _, player, instance = self.scenario()
        world = instance.component.world
        configs = {cls: {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}}
                   for cls in lucio_movement.QUERIES}
        for yaw in (0, 8192, 16384, -16384, -32768):
            for pitch in (-8192, 0, 8192):
                player.mover.yaw, player.mover.pitch = yaw, pitch
                angle, elevation = yaw * math.tau / 65536, pitch * math.tau / 65536
                forward = instance.evaluate(configs["STU_021D01CD"])
                side = instance.evaluate(configs["STU_66F3B89E"])
                look = instance.evaluate(configs["STU_2200A66D"])
                for actual, expected in ((tuple(forward), (math.sin(angle), 0., math.cos(angle))),
                                         (tuple(side), (math.cos(angle), 0., -math.sin(angle))),
                                         (tuple(look), (math.sin(angle) * math.cos(elevation),
                                                        -math.sin(elevation),
                                                        math.cos(angle) * math.cos(elevation)))):
                    for a, b in zip(actual, expected, strict=True):
                        self.assertAlmostEqual(a, b, delta=2e-6)
        self.assertEqual(instance.evaluate(configs["STU_309B0570"]), expr.Vec3(0., 3., 4.))
        relative = dict(configs["STU_309B0570"], m_5B94EDB8={"$": "STUConfigVarBool", "m_value": True})
        self.assertIs(lucio_movement.query(world, relative, instance), NotImplemented)
        foreign = dict(configs["STU_2200A66D"], m_entity={"$": "STUConfigVarInt", "m_value": 3})
        self.assertIs(lucio_movement.query(world, foreign, instance), NotImplemented)

    def test_actual_logical_button_queries_follow_real_edges_without_disabling_held(self):
        _, player, instance = self.scenario()
        script = player.body_script
        jump = instance.graph.node(100).field("m_condition")
        crouch = instance.graph.node(108).field("m_condition")["m_configVars"][1]
        for bits, wanted in ((0, (False, False)), (0x40, (True, False)),
                             (0xC0, (True, True)), (0x80, (False, True)), (0, (False, False))):
            script.command(script.frame + 1, bits)
            self.assertEqual((instance.evaluate(jump), instance.evaluate(crouch)), wanted)
        script.command(script.frame + 1, 0x40)
        script.component.disabled[11] = 1  # disabled Ability input is distinct from a held-list query
        self.assertTrue(instance.evaluate(jump))

    def test_native_missing_button_owner_is_a_no_output_evaluation_failure(self):
        run, player, instance = self.scenario()
        query = instance.graph.node(100).field("m_condition")
        run.match.players.remove(player)
        self.assertIs(lucio_movement.query(instance.component.world, query, instance), expr.EVALUATION_FAILED)
        self.assertIs(lucio_movement.query(instance.component.world, query, None), NotImplemented)
        with patch.object(instance, "graph", graph.graph(0x0266)):
            self.assertIs(lucio_movement.query(instance.component.world, query, instance), NotImplemented)

    def test_actual_release_branch_waits_for_button_up_then_queues_next_frame(self):
        _, player, instance = self.scenario()
        script = player.body_script
        script.command(script.frame + 1, 0x40, forward=127)
        # Declared controller-loss entry; native ENTITY1204 producer remains open.
        instance.run_node(instance.graph.node(100), None)
        instance.update()
        self.assertTrue(instance.state(11).active)
        self.assertTrue(instance.state(12).active)
        self.assertEqual(player.mover.velocity_changes, [])
        script.command(script.frame + 1, 0, forward=127)
        self.assertEqual(len(player.mover.velocity_changes), 2)
        self.assertTrue(all(row.frame == script.component.frame + 1 for row in player.mover.velocity_changes))
        self.assertFalse(instance.state(12).active)

    def test_authored_point_two_second_release_window_expires_without_a_jump(self):
        for held_frames, expected_rows in ((11, 2), (13, 0)):
            with self.subTest(held_frames=held_frames):
                _, player, instance = self.scenario()
                script = player.body_script
                script.command(script.frame + 1, 0x40, forward=127)
                instance.run_node(instance.graph.node(100), None)
                instance.update()
                # Release at192ms; or first process a held frame at208ms,
                # then release at224ms. Same-frame input/timer ordering is separate.
                for _ in range(held_frames):
                    script.command(script.frame + 1, 0x40, forward=127)
                script.command(script.frame + 1, 0, forward=127)
                self.assertEqual(len(player.mover.velocity_changes), expected_rows)

    def test_real_crouch_cancels_only_when_authored_32856_gate_is_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                _, player, instance = self.scenario()
                script = player.body_script
                # Actual0263 startup state13 temporarily binds the input block1347.
                for _ in range(80):
                    script.command(script.frame + 1, 0)
                # Declared external input; native producer32856 remains unproved.
                script.component.set(32856, enabled)
                script.command(script.frame + 1, 0x40)
                self.assertTrue(instance.state(8).active)
                script.command(script.frame + 1, 0xC0)
                self.assertEqual(instance.state(8).active, not enabled)
                self.assertEqual(player.mover.velocity_changes, [])

    def test_declared_contact_loss_uses_authored_release_or_repress_window(self):
        for release_mode in (False, True):
            for expired in (False, True):
                with self.subTest(release_mode=release_mode, expired=expired):
                    _, player, instance = self.scenario()
                    script = player.body_script
                    for _ in range(80):
                        script.command(script.frame + 1, 0)
                    self.assertIs(script.component.entity_var(32856).value(), True)
                    self.assertIs(script.component.entity_var(9473).value(), False)
                    self.assertIs(script.component.entity_var(7766).value(), False)
                    if not release_mode:
                        script.component.set(32856, False)
                    script.component.set(1204, True)
                    script.command(script.frame + 1, 0x40, forward=127)
                    self.assertTrue(instance.state(8).active)
                    self.assertTrue(instance.state(10).active)

                    # Declare the external producer's falling edge. Let the VM
                    # select and enter its own exit branch from that edge.
                    script.component.set(1204, False)
                    script.command(script.frame + 1, 0x40, forward=127)
                    self.assertFalse(instance.state(8).active)
                    timer, button = (11, 12) if release_mode else (35, 34)
                    self.assertTrue(instance.state(timer).active)
                    self.assertTrue(instance.state(button).active)
                    self.assertEqual(player.mover.velocity_changes, [])
                    if not release_mode:
                        script.command(script.frame + 1, 0, forward=127)
                        self.assertEqual(player.mover.velocity_changes, [])

                    if expired:
                        for _ in range(13):
                            script.command(script.frame + 1, 0x40 if release_mode else 0, forward=127)
                        self.assertFalse(instance.state(timer).active)
                        self.assertFalse(instance.state(button).active)
                    script.command(script.frame + 1, 0 if release_mode else 0x40, forward=127)
                    changes = player.mover.velocity_changes
                    self.assertEqual(len(changes), 0 if expired else 2)
                    if not expired:
                        self.assertTrue(all(change.frame == script.frame + 1 for change in changes))


if __name__ == '__main__':
    unittest.main()
