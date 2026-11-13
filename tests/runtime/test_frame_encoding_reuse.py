"""Budget trial encoding preserves exact bits, origins and real send/loss semantics."""

import unittest
from unittest.mock import patch

from ow174.game import projectiles, world
from ow174.game.bits import BitWriter
from tests.runtime import test_game_packet_budget as budget


class FrameEncodingReuseTests(unittest.TestCase):
    def test_mixed_origin_records_match_uncached_across_budget_prefixes(self):
        server, client, player, _peer = budget.connection()
        budget.large_messages(client, player)
        chunk = BitWriter()
        chunk.bits(0x12345, 19)
        scripted = world.EntityUpdate(0xA0000201, chunk=chunk)
        moving = world.EntityUpdate(0xA0000301, movement=world.Movement((1.25, 2.0, -3.5)))
        moving.ch2 = projectiles.release(1005)
        origins = []

        def build(origin):
            origins.append(origin)
            return world.create_placeable(origin, 0x77)

        create = world.EntityUpdate(0x80000077, world.OP_CREATE, build)
        records = [scripted, create, moving]
        cache = {}
        writes = world._write_entity
        with patch.object(world, '_write_entity', wraps=writes) as encode:
            for reliable in [client.reliable, client.reliable[:1], []]:
                for count in [3, 1, 2, 3]:
                    actual = world.frame(1002, 1001, reliable, [], records[:count], entity_cache=cache)
                    before = encode.call_count
                    expected = world.frame(1002, 1001, reliable, [], records[:count])
                    self.assertEqual(actual, expected)
                    self.assertEqual(encode.call_count - before, count)
            cached_writes = [call.args[1] for call in encode.call_args_list
                             if call.args[0] in cache.values()]
        self.assertEqual(cached_writes.count(scripted), 1)
        self.assertEqual(cached_writes.count(moving), 1)
        self.assertNotIn(create, [key[0] for key in cache])
        self.assertGreater(len(set(origins)), 1)  # actual alignment-dependent builders rerun
        self.assertEqual(server.sock.sent, [])

    def test_encrypted_budget_send_failure_and_retry_match_uncached_control(self):
        def execute(reuse):
            server, client, player, peer = budget.connection()
            budget.large_messages(client, player)
            event = world.EntityUpdate(0xA0000201)
            event.ch2 = projectiles.release(1005)
            moving = world.EntityUpdate(0xA0000301, movement=world.Movement((1.0, 1.0, 1.0)))
            client.queue_entities([budget.placeable(), event, moving])
            original = world.frame

            def encode(*args, **kwargs):
                if not reuse:
                    kwargs['entity_cache'] = None
                return original(*args, **kwargs)

            with patch.object(world, 'frame', side_effect=encode):
                server.sock.fail_next = True
                client.send_frame(1002, 10.0)
                self.assertEqual(client.in_flight, {})
                moving.movement.position = (2.0, 1.0, 3.0)
                client.send_frame(1002, 10.0)
                packet = budget.decoded(self, peer, server.sock.sent[-1][0])[0]
                client.acked(packet.seq + 1, 0)
                client.send_frame(1005, 10.1)
            return (server.sock.attempted, server.sock.sent, client.seq,
                    [(item.entity, item.op) for item in client.entities],
                    [(seq, [item.entity for item in rows])
                     for seq, (_reliable, rows) in client.in_flight.items()])

        self.assertEqual(execute(True), execute(False))
