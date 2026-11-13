"""Actual Jump Pack cleanup -> single original bounded-Y request -> mover."""

import unittest
from unittest import mock

import test_delivered_winston as fixtures

from ow174.game import mover
from ow174.game.script import modifiers


class WinstonJumpClampTests(unittest.TestCase):
    def test_real_cleanup_clamps_next_frame_once_without_replacing_motion(self):
        for incoming_y in (-150., 0., 100.):
            with self.subTest(incoming_y=incoming_y):
                fixture = fixtures.TeslaTests()
                fixture.setUp()
                owner = fixture.winston
                fixture.enemy.mover.position[:] = [0, 1, 100]
                world = owner.body_script.world
                original = world.velocity_action
                reached = []

                def observe(instance, node):
                    if instance.graph.index == 0x10A and node.pos == 86:
                        reached.append((instance, node, instance.component.frame))
                    return original(instance, node)

                with mock.patch.object(world, "velocity_action", side_effect=observe):
                    fixture.run.step({owner: (0, -16384, 8)})
                    for _ in range(30):
                        fixture.run.step({})
                        if reached:
                            break
                self.assertEqual(len(reached), 1)
                instance, node, frame = reached[0]
                rows = [mod for mod in owner.mover.mods.values()
                        if mod.op == mover.CLAMP and mod.index == mover.VELOCITY_Y]
                self.assertEqual(len(rows), 1)
                row = rows[0]
                config = node.field("m_BC5E91CF")[0]
                low = instance.evaluate(config["m_8AD739CE"])
                high = instance.evaluate(config["m_8213EB22"])
                self.assertEqual(row.value, (mover.f32(low), mover.f32(high)))
                self.assertEqual((row.start, row.end), (frame + 1, frame + 2))
                self.assertEqual(row.key, (instance.id, node.rid, (frame + 1) & 0xFFFF, 0))
                original_mover = owner.mover
                # Incoming physical velocity is a fixture precondition, never an
                # expected output injection. Gravity and collision still run.
                owner.mover.velocity = (3., incoming_y, 7.)
                before_hp = fixture.health(fixture.enemy)
                applied = []
                modify = owner.mover._modified_velocity

                def consume(records, velocity):
                    result = modify(records, velocity)
                    if any(record is row for record in records):
                        applied.append((velocity, result))
                    return result

                with mock.patch.object(owner.mover, "_modified_velocity", side_effect=consume):
                    fixture.run.step({})
                    fixture.run.step({})
                self.assertEqual(len(applied), 1)
                before, after = applied[0]
                self.assertEqual(after[1], modifiers.apply(5, before[1], low, high))
                self.assertEqual((after[0], after[2]), (before[0], before[2]))
                self.assertIs(owner.mover, original_mover)
                self.assertEqual(fixture.health(fixture.enemy), before_hp)
                self.assertIsNone(owner.body_script.component.fault)
