"""A budget retry encodes current mutable data and preserves builder effects."""
import unittest

from ow174.game import world
from ow174.game.bits import BitWriter


class MutableFrameCacheTests(unittest.TestCase):
    def test_budget_retries_match_fresh_wire_after_builder_mutates_prior_records(self):
        def sequence(kind, use_cache):
            state = world.Movement((0.0, 1.0, 0.0), frame=98)
            chunk, ch2 = BitWriter(), BitWriter()
            chunk.bits(1, 2)
            ch2.bits(1, 2)
            first = world.EntityUpdate(0x80000077, movement=state if kind == 'movement' else None,
                                       chunk=chunk if kind == 'chunk' else None)
            if kind == 'ch2':
                first.ch2 = ch2
            calls = []

            def build(origin):
                calls.append(origin)
                if kind == 'movement':
                    state.position = (float(8 + len(calls)), 1.0, 0.0)
                elif kind == 'chunk':
                    chunk.bits(len(calls), 4)
                else:
                    ch2.bits(len(calls), 4)
                return world.update(origin, {26: [1]})

            second = world.EntityUpdate(0x80000078, world.OP_UPDATE, build=build)
            cache, payloads = {}, []
            for _ in range(3):
                payloads.append(world.frame(100, 99, entities=[first, second],
                                            entity_cache=cache if use_cache else None))
            return payloads, calls

        for kind in ('movement', 'chunk', 'ch2'):
            with self.subTest(kind=kind):
                expected, expected_origins = sequence(kind, False)
                actual, actual_origins = sequence(kind, True)
                self.assertNotEqual(expected[0], expected[1])
                self.assertEqual(actual, expected)
                self.assertEqual(actual_origins, expected_origins)
                self.assertEqual(len(actual_origins), 3)

    def test_retry_does_not_hide_invalid_float_yaw_behind_equal_integer_value(self):
        state = world.Movement((0.0, 1.0, 0.0), yaw=1)
        item = world.EntityUpdate(0x80000077, movement=state)
        cache = {}
        world.frame(100, 99, entities=[item], entity_cache=cache)
        state.yaw = 1.0
        with self.assertRaises(TypeError):
            world.frame(100, 99, entities=[item], entity_cache=cache)
