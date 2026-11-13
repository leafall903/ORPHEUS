"""Local lifetime changes and controller replacements retain exact queue behavior."""

import unittest

from ow174.game import world
from ow174.game.bits import BitWriter
from tests.runtime import test_game_packet_budget as budget


class EntityQueuePruneTests(unittest.TestCase):
    def test_controller_close_replacement_and_unrelated_lifetime_keep_fifo(self):
        _server, client, player, _peer = budget.connection()

        def close():
            item = world.EntityUpdate(player.entity, chunk=BitWriter())
            item.controller_close = True
            item.chunk_last = player.script.data_last
            return item

        old = close()
        first = world.EntityUpdate(0xA0000201, chunk=BitWriter())
        client.queue_entities([old, first])
        player.script.data_last += 1
        current = close()
        client.queue_entities([current])
        self.assertEqual(client.entities, [first, current])
        create = budget.placeable(first.entity)
        client.queue_entities([create])
        self.assertEqual(client.entities, [current, create])
        player.script.data_last += 1
        other = budget.placeable(0x80000078)
        client.queue_entities([other])
        self.assertEqual(client.entities, [create, other])

    def test_no_transition_keeps_fresh_lists_and_other_entity_generation(self):
        _server, client, _player, _peer = budget.connection()
        first, other = budget.placeable(0x80000077), budget.placeable(0x80000078)
        client.queue_entities([first, other])
        preserved = client._entity_record_generations[other]
        update = world.EntityUpdate(first.entity, chunk=BitWriter())
        before = client.entities
        client.queue_entities([update])
        self.assertIsNot(client.entities, before)
        frozen = list(before)
        next_update = world.EntityUpdate(other.entity, chunk=BitWriter())
        client.queue_entities([next_update])
        self.assertEqual(before, frozen)
        destroy = world.EntityUpdate(first.entity, world.OP_DESTROY)
        client.queue_entities([destroy])
        self.assertEqual(client.entities, [other, next_update, destroy])
        self.assertEqual(client._entity_record_generations[other], preserved)
