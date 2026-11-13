"""Actual Q input publishes its original carrier and a single deployed actor."""
import unittest
from unittest.mock import patch

import test_server_journal as journal

from ow174.game import combat, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr


class SuperchargerCarrierTests(unittest.TestCase):
    def test_actual_q_publishes_observer_carrier_and_one_original_device(self):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=0x02E000000000013E)
        owner, observer = players
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        fight = owner.match.combat
        first_packet = len(sent)
        shot = device = None
        now = 100.95
        for index in range(100):
            peers[0].commands(owner.body_script.frame + 1, 1, 32 if index == 0 else 0, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            now += combat.TICK
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            for rocket in (*fight.rockets, *fight.ability_impacts):
                if rocket.source_graph == 0xCF5:
                    shot = rocket.shot
            if fight.orisa.superchargers.active:
                device = next(iter(fight.orisa.superchargers.active.values()))
        self.assertIsNotNone(shot)
        self.assertIsNotNone(device)
        self.assertFalse(shot.authoritative)  # owner keeps the original prediction lease
        self.assertEqual(len(shot.observers), 1)
        delivery, = shot.observers
        self.assertIs(delivery.viewer, observer)
        self.assertEqual(shot.definition, 0x0400000000001CCB)
        self.assertTrue(delivery.creation.received)
        self.assertTrue(delivery.launch_arrival.received)
        self.assertFalse(delivery.explode)  # server174E owns the actual deployment
        self.assertGreater(delivery.visual_ticks, 0)
        self.assertNotIn(delivery, fight.projectiles.observed)
        definitions = {instance.graph.index: instance.graph
                       for instance in device.component.instances.values()}
        fallback = decode.graph_of
        for peer, viewer in zip(peers, players, strict=True):
            creates, carrier_creates, carrier_destroys = [], [], []
            model = decode.ClientBody((0xD00, 0x25B4))
            for data, _ in sent[first_packet:]:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, fields in records:
                    if record.entity in delivery.record.ids and viewer is observer:
                        if record.op == world.OP_CREATE:
                            carrier_creates.append(record)
                        if record.op == world.OP_DESTROY:
                            carrier_destroys.append(record)
                    if record.entity != device.entity:
                        continue
                    if record.op == world.OP_CREATE:
                        creates.append(fields)
                    if record.chunk is not None:
                        with patch.object(decode, "graph_of",
                                          lambda index: definitions.get(index) or fallback(index)):
                            model.apply(record)
            self.assertEqual(len(creates), 1)
            self.assertEqual(model.instances[1], 0xD00)
            self.assertEqual(model.instances[2], 0x25B4)
            self.assertEqual(model.vars[(1, 27)], expr.Entity(owner.body))
            self.assertEqual(model.vars[(1, 40)], expr.Entity(owner.body))
            self.assertIn(51, creates[0])
            if viewer is observer:
                self.assertEqual(len(carrier_creates), 1)
                self.assertTrue(carrier_destroys)
