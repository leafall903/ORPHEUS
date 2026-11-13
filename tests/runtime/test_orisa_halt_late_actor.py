"""Real Halt commands and encrypted actor chunks; no native rendering claim."""
import unittest
from unittest.mock import patch

import test_server_journal as journal

from ow174.game import observers, orisa_halt, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, runtime


class OrisaHaltLateActorTests(unittest.TestCase):
    def exercise(self, *, defer_create_ack=False):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=0x02E000000000013E)
        owner, target = players
        fight = owner.match.combat
        first_packet = len(sent)
        orb = component = None
        now = 100.95
        withheld = False
        checked_binding = False
        press = (12, 37) if defer_create_ack else (0, 25)
        for index in range(100):
            peers[0].commands(owner.body_script.frame + 1, 1, 2 if index in press else 0, now=now)
            peers[1].commands(target.body_script.frame + 1, 1, 0, now=now)
            now += .016
            session.tick(now)
            if fight.orisa.orbs:
                orb = next(iter(fight.orisa.orbs.values()))
            if orb is not None and orb.query is not None and not checked_binding:
                component = orb.query.instance.component
                for player in players:
                    client = player.client
                    generation = client.actor_generation(orb.entity)
                    self.assertEqual(generation, 1)
                    stream = client.bot_stream(orb.entity)
                    self.assertIsNotNone(stream)
                    self.assertTrue(client.bind_actor_component(
                        orb.entity, observers.SceneBody(component), generation=generation))
                    self.assertIs(client.bot_stream(orb.entity), stream)
                    self.assertFalse(client.bind_actor_component(
                        orb.entity, observers.SceneBody(runtime.Component(orb.entity)),
                        generation=generation))
                    if defer_create_ack:
                        self.assertIsNone(stream.base)
                        self.assertFalse(any(item.entity == orb.entity and item.chunk is not None
                                             for item in client.entities))
                        withheld = True
                checked_binding = True
            for peer in peers:
                peer.drain(lose=defer_create_ack and orb is not None and not checked_binding)
                peer.send(now=now)
        self.assertTrue(checked_binding)
        self.assertEqual(withheld, defer_create_ack)
        self.assertIsNotNone(component)
        self.assertNotIn(orb.entity, fight.orisa.orbs)
        original_query_graph = orisa_halt.query_graph()
        for peer, player in zip(peers, players, strict=True):
            model = decode.ClientBody((0xCCC,))
            creates, chunks = [], []
            for data, _ in sent[first_packet:]:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, _ in records:
                    if record.entity != orb.entity:
                        continue
                    if record.op == world.OP_CREATE:
                        creates.append(record)
                    if record.chunk is not None:
                        chunks.append(record)
                        original_graph = decode.graph_of
                        with patch.object(decode, "graph_of", lambda index, fallback=original_graph:
                                          original_query_graph if index == 0xCCC else fallback(index)):
                            model.apply(record)
            if defer_create_ack:
                self.assertGreaterEqual(len(creates), 1)  # retransmissions keep the same generation
            else:
                self.assertEqual(len(creates), 1)
            self.assertGreater(len(chunks), 0)
            self.assertEqual(model.instances[1], 0xCCC)
            self.assertGreater(model.applied, 0)
            self.assertIsNone(player.client.actor_generation(orb.entity))
            self.assertIsNone(player.client.bot_stream(orb.entity))
            self.assertFalse(player.client.bind_actor_component(
                orb.entity, observers.SceneBody(component), generation=1))

    def test_actual_halt_publishes_original_orb_chunk_without_second_create(self):
        self.exercise()

    def test_actual_halt_component_waits_for_original_create_ack(self):
        self.exercise(defer_create_ack=True)
