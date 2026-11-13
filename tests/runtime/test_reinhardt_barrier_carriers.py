"""Decode native shield carriers on the real protocol path; visibility remains pending."""

import struct
import unittest

import test_game_repeated_lifecycles as wire
import test_server_journal as journal
from ow174.game import barriers, world
from ow174.game.script import expr
from ow174.game.script.decode import ClientBody


class BarrierCarrierTests(unittest.TestCase):
    def test_create_health_update_filter_and_script_source_reach_owner(self):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, two=False,
                                          first_hero=0x02E0000000000007)
        player, = players
        peer, = peers
        now, entity = 100.944, None
        decoded_body = ClientBody()
        creates, pools = [], []
        for offset in range(65):
            now += .016
            peer.commands(player.body_script.frame + 1, 1,
                          2 if offset >= 30 else 0, now=now)
            if offset == 48:
                actor = player.match.combat.barriers.authored_for(player.body)
                self.assertIsNotNone(actor)
                self.assertEqual(barriers.damage(player.match.combat, actor, 400), 400)
            cursor = len(sent)
            session.tick(now)
            actor = player.match.combat.barriers.authored_for(player.body)
            if actor is not None:
                entity = actor.entity
            for data, address in sent[cursor:]:
                if address != peer.address:
                    continue
                packet = peer.cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind != journal.KIND_FRAME:
                    continue
                _, records = wire.wire_frame(packet)
                for item, fields in records:
                    if item.entity != entity:
                        continue
                    if item.op == world.OP_CREATE:
                        creates.append(fields)
                    if 51 in fields:
                        pools.append(struct.unpack_from("<Iff", fields[51][0]))
                    if item.chunk is not None:
                        decoded_body.apply(item)
            peer.drain()
            peer.send(now=now)

        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][26][0], player.team_bit)
        self.assertEqual(struct.unpack_from("<Iff", creates[0][51][0]), (1, 1600, 1600))
        self.assertIn((1, 1600, 1200), pools)
        self.assertEqual(set(decoded_body.instances.values()), {0x25B4, 0x96})
        for instance in decoded_body.instances:
            self.assertEqual(decoded_body.vars[(instance, 0x1B)], expr.Entity(player.body))
        self.assertNotIn((0, 253), decoded_body.vars)  # the client derives health from component51
