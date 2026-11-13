"""Actual encrypted Cryo descriptors retain the original PlayScript caster source."""
import unittest

import test_server_journal as fixtures

from ow174.game import barriers, mei, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr, graph


class MeiCryoDescriptorSourceTests(unittest.TestCase):
    def test_actual_cryo_source_for_owner_enemy_and_late_ally(self):
        self.audit = []
        case = fixtures.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, first_hero=mei.HERO, ally=True)
        owner, _enemy, ally = players
        ally.spawned = False  # existing peer starts observing this actor after its birth
        now = 101.1
        for peer, player in zip(peers, players, strict=True):
            peer.commands(player.body_script.frame + 1, 40, 0, now=now)
        peers[0].commands(owner.body_script.frame + 1, 1, 8, now=now)
        ice, = [b for b in owner.match.combat.barriers.scripted.values()
                if b.component.world.hero == barriers.MEI]
        child, = [i for i in ice.component.instances.values() if i.graph.index == 0x0623]
        self.assertEqual(child.source_entity(), owner.body)
        self.assertEqual(child.vars[27].value(), expr.Entity(owner.body))
        self.assertNotEqual(owner.body, ice.entity)
        self.assertEqual(ice.pet_owner, owner.entity)
        node = graph.graph(0x05FE).node(22)
        self.assertIsNone(node.field('m_6D7701B7'))  # original context instigator; no override
        for step in range(45):
            now += .016
            if step == 8:
                ally.spawned = True
            for peer, player in zip(peers, players, strict=True):
                peer.commands(player.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
        for role, peer, viewer in zip(('self', 'enemy', 'ally-late'), peers, players, strict=True):
            with self.subTest(viewer=role):
                vm = decode.ClientBody()
                chunks, descriptors, creates = set(), [], []
                for data, _ in sent:
                    if peek_connection(data) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(data)
                    if packet is None or packet.kind != KIND_FRAME:
                        continue
                    _, records = fixtures.wire_frame(packet)
                    for record, fields in records:
                        if record.entity != ice.entity:
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
                        descriptors.extend(i.source_entity for i in frame.instances.values()
                                           if i.descriptor and i.graph == 0x0623)
                self.audit.append({'viewer': role, 'cryo': ice.entity, 'casterBody': owner.body,
                                   'petController': owner.entity, 'serverSource': child.source_entity(),
                                   'wireVar27': getattr(vm.vars.get((child.id, 27)), 'id', None),
                                   'descriptorSources': descriptors,
                                   'nativeFactorySourceUnderDocumentedFallback':
                                       [source or ice.entity for source in descriptors],
                                   'uniqueChunks': len(chunks), 'creates': len(creates),
                                   'createACK': viewer.client.bot_create_acked(ice.entity)})
                self.assertEqual(len(creates), 1)
                self.assertEqual(creates[0][26][0], owner.team_bit)
                self.assertEqual(creates[0][72][0], owner.entity)
                self.assertTrue(descriptors)
                self.assertEqual(vm.vars[(child.id, 27)], expr.Entity(owner.body))
                # Descriptor input seeds the native cached builtin before init.
                # A missing input takes the current090B SELF fallback, even if
                # the later ordinary var27 update already carries the caster.
                self.assertEqual(set(descriptors), {owner.body})
                self.assertEqual(vm.sources[child.id], owner.body)
                self.assertTrue(viewer.client.bot_create_acked(ice.entity))
                self.assertEqual(viewer.client.bot_stream(ice.entity).base.sources[child.id], owner.body)


if __name__ == '__main__':
    unittest.main()
