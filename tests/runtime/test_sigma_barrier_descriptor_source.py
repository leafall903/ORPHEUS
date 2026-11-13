"""A real shield action preserves the original played graph's source on both peers."""

import unittest

import test_server_journal as fixtures

from ow174.game import sigma_barrier, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr, graph


class SigmaBarrierDescriptorSourceTests(unittest.TestCase):
    def test_actual_shield_action_publishes_body_source_distinct_from_Pet_controller(self):
        case = fixtures.ServerJournalTests()
        session, sent = case.make()
        peers, (owner, enemy) = case.connected(session, sent, first_hero=sigma_barrier.HERO)
        initial_bots = tuple(owner.match.bots)
        actor, = (a for a in owner.match.combat.sigma_barriers.values() if a.owner is owner)
        child = actor.child
        self.assertEqual(child.source_entity(), owner.body)
        for pos in (62, 63):
            node = graph.graph(0x1A2A).node(pos)
            self.assertEqual(child.evaluate(node.field("m_targets")), expr.Entity(owner.body))
        self.assertNotEqual(owner.body, actor.entity)
        self.assertNotEqual(owner.body, actor.pet_source)
        now = 100.944
        for step in range(80):
            now += .016
            peers[0].commands(owner.body_script.frame + 1, 1, 2 if step < 20 else 0, now=now)
            peers[1].commands(enemy.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
        for peer, viewer in zip(peers, (owner, enemy), strict=True):
            vm = decode.ClientBody()  # 2D25 declares no initial graphs; 1A2A is played.
            chunks = set()
            creates = []
            descriptors = []
            for data, _ in sent:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet is None or packet.kind != KIND_FRAME:
                    continue
                _, records = fixtures.wire_frame(packet)
                for record, fields in records:
                    if record.entity != actor.entity:
                        continue
                    if record.op == world.OP_CREATE:
                        creates.append(fields)
                    if record.chunk is None:
                        continue
                    key = (record.chunk.count, record.chunk.getvalue())
                    if key in chunks:
                        continue
                    chunks.add(key)
                    frame = vm.apply(record)
                    descriptors.extend(item.source_entity for item in frame.instances.values()
                                       if item.descriptor and item.graph == 0x1A2A)
            self.assertEqual(len(creates), 1)
            self.assertEqual(creates[0][72][0], owner.entity)
            self.assertTrue(descriptors)
            self.assertEqual(set(descriptors), {owner.body})
            self.assertEqual(vm.sources[child.id], owner.body)
            self.assertEqual(vm.vars[(child.id, 587)], (700,))  # Ordered PlayScript parameter values.
            self.assertTrue(viewer.client.bot_create_acked(actor.entity))
            self.assertEqual(viewer.client.bot_stream(actor.entity).base.sources[child.id], owner.body)
        self.assertEqual(tuple(owner.match.bots), initial_bots)
