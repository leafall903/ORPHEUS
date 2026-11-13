"""Real Halt receiver descriptors retain the existing caster source on all peers."""

import unittest
from unittest.mock import patch

import test_server_journal as journal

from ow174.game import orisa_halt
from ow174.game.link import KIND_FRAME, SYN, peek_connection
from ow174.game.script import decode, expr


class HaltDescriptorSourceTests(unittest.TestCase):
    def test_actual_pull_source_reaches_caster_receiver_and_third_observer(self):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=0x02E000000000013E, ally=True)
        caster, target, _observer = players
        fight = caster.match.combat
        original_health = fight.vitals[target.body].total
        now = 100.95
        receiver = None
        orb_entity = None
        late = None
        late_picked = False
        for index in range(180):
            for position, (peer, player) in enumerate(zip(peers, players, strict=True)):
                if player.body_script is not None:
                    peer.commands(player.body_script.frame + 1, 1,
                                  2 if position == 0 and index in (0, 25) else 0, now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            pull = fight.orisa.pulls.get(target.body)
            if pull is not None and receiver is None:
                receiver = pull.script
                self.assertEqual(receiver.source_entity(), caster.entity)
                self.assertNotEqual(caster.entity, caster.body)
                orb_entity = expr.to_entity(receiver.find_var(0, 40).value())
                self.assertNotEqual(orb_entity, caster.body)
                self.assertNotEqual(orb_entity, target.body)
                handoffs = session.apply('join_match', {
                    'match_id': caster.match.id,
                    'players': [(4, 'LateEnemy', journal.SOLDIER, 1, False)],
                })
                late = journal.Peer(session, handoffs[0], sent, ('127.0.0.1', 42009))
                peers.append(late)
                late.send(flags=SYN, now=now)
                late.send(now=now)
                late.message(21601, {}, now=now)
                session.report_callbacks()
            if late is not None and players[-1].spawned and not late_picked:
                late.message(21616, journal.pick(players[-1].entity, journal.SOLDIER), now=now)
                late_picked = True
        self.assertIsNotNone(receiver)  # real selected target; no empty/failed cast
        self.assertTrue(late_picked)
        self.assertEqual(fight.vitals[target.body].total, original_health)
        self.assertNotIn(target.body, fight.orisa.pulls)
        self.assertFalse(fight.orisa.controls(target.body))
        fallback = decode.graph_of
        control_graph = orisa_halt.control_graph()
        for peer, viewer in zip(peers, players, strict=True):
            model = decode.ClientBody.for_hero(target.hero.guid)
            descriptors = []
            chunks = set()
            for raw, _ in sent:
                if peek_connection(raw) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(raw)
                self.assertIsNotNone(packet)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, _ in records:
                    if record.entity != target.body or record.chunk is None:
                        continue
                    key = record.chunk.count, record.chunk.getvalue()
                    if key in chunks:
                        continue
                    chunks.add(key)
                    with patch.object(decode, 'graph_of', lambda index:
                                      control_graph if index == 0xCCE else fallback(index)):
                        frame = model.apply(record)
                    descriptors.extend(item.source_entity for item in frame.instances.values()
                                       if item.descriptor and item.graph == 0xCCE)
            with self.subTest(viewer=viewer.slot, receiver_self=viewer is target):
                self.assertTrue(descriptors)
                self.assertEqual(set(descriptors), {caster.entity})
                self.assertNotIn(receiver.id, model.instances)  # original cleanup still reaches each peer
