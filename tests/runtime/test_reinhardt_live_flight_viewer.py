"""A real peer joins during an original Fire Strike; no native client is launched."""

import dataclasses
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_repeated_lifecycles as wire
import test_server_journal as journal

from ow174.game import world
from ow174.game.bits import BitReader
from ow174.game.link import SYN

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / "fixtures/behavior/reinhardt-live-flight-viewer-v1.json").read_text())


class LiveFlightViewerTests(unittest.TestCase):
    def check_join(self, *, live):
        cfg, expected = CONTRACT["input"], CONTRACT["expected"]
        join = cfg["live_join"] if live else cfg["expired_join"]
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        scene = dataclasses.replace(journal.MAP, team_sizes=(2, 1), free_for_all=False)
        entries = [(1, "Alpha", int(cfg["hero"], 16), 0, False),
                   (2, "Beta", journal.SOLDIER, 1, False), (3, "Charlie", journal.SOLDIER, 0, False)]
        handoffs = session.create_match(scene, entries)
        peers = [journal.Peer(session, h, sent, ("127.0.0.1", 42000 + i))
                 for i, h in enumerate(handoffs)]
        for peer in peers[:2]:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        session.report_callbacks()
        journal.acknowledge_ownership(session, peers[:2])
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, entry in zip(peers[:2], players[:2], entries[:2], strict=True):
            peer.message(21616, journal.pick(player.entity, entry[2]), now=100.8)
        for tick in range(8):
            session.tick(100.816 + tick * .016)
            for peer in peers[:2]:
                peer.drain()
                peer.send(now=100.816 + tick * .016)
        owner, enemy, late = players
        enemy.mover.position = [0., 0., 20.]
        enemy.mover.body = None
        now, shot, original_deadline = cfg["start_time"], None, None
        definitions, creates, launches, destroys = {}, [], [], []
        identity = [None]
        native_update, native_record, native_shot = wire.EntityUpdate, wire.entity_record, wire.read_shot

        def update(entity):
            identity[0] = entity
            return native_update(entity)

        def record(reader, op):
            if op == world.OP_CREATE:
                peek = BitReader(reader.data, reader.pos)
                peek.bits(14)
                if not peek.bit():
                    definitions[identity[0]] = peek.bits(12 if peek.bit() else 20 if peek.bit() else 32)
            return native_record(reader, op)

        def read_shot(reader):
            value = native_shot(reader)
            launches.append((identity[0], value))
            return value

        for offset in range(cfg["ticks"]):
            now += cfg["tick_seconds"]
            fight = owner.match.combat
            if offset == join:
                self.assertIsNotNone(shot)
                self.assertEqual(any(r.shot is shot for r in fight.rockets), live)
                self.assertIsNone(late.client)
                peers[2].send(flags=SYN, now=now)
                peers[2].send(now=now)
                peers[2].message(21601, {}, now=now)
                session.report_callbacks()
            active = 3 if offset >= join else 2
            for index in range(active):
                player = players[index]
                if player.body_script is not None:
                    peers[index].commands(player.body_script.frame + 1, 1,
                                          16 if index == 0 and offset == cfg["fire_offset"] else 0, now=now)
            cursor = len(sent)
            session.tick(now)
            if shot is None and fight.rockets:
                shot = fight.rockets[0].shot
                original_deadline = min(d.deadline for d in shot.observers)
            for data, address in sent[cursor:]:
                if address != peers[2].address:
                    continue
                packet = peers[2].cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind != journal.KIND_FRAME:
                    continue
                with (patch.object(wire, "EntityUpdate", update), patch.object(wire, "entity_record", record),
                      patch.object(wire, "read_shot", read_shot)):
                    _, records = wire.wire_frame(packet)
                for item, _ in records:
                    if definitions.get(item.entity) == expected["definition"]:
                        if item.op == world.OP_CREATE:
                            creates.append(item.entity)
                        elif item.op == world.OP_DESTROY:
                            destroys.append(item.entity)
            for peer in peers[:active]:
                peer.drain()
                peer.send(now=now)
            if live and offset == 200:
                self.assertIsNotNone(fight.observers.watches[(late.slot, owner.body)].stream.base)
                self.assertEqual(len([d for d in shot.observers if d.viewer is late]), 1)

        late_deliveries = [d for d in shot.observers if d.viewer is late]
        self.assertEqual(len(late_deliveries), int(live))
        self.assertEqual(len(launches), int(live))
        self.assertTrue(all(d not in fight.projectiles.observed for d in shot.observers))
        if live:
            delivery = late_deliveries[0]
            original = shot.observers[0].record
            self.assertEqual(dataclasses.replace(delivery.record, ids=original.ids), original)
            self.assertEqual([len(creates), len(launches), len(destroys)],
                             expected["live_create_launch_destroy_counts"])
            self.assertEqual(creates, destroys)
            self.assertEqual(launches[0][0], shot.body)
            self.assertEqual(launches[0][1]["ids"], creates)
            for key in ("state", "activation", "volleys"):
                self.assertEqual(launches[0][1][key], expected[key])
            self.assertLessEqual(delivery.deadline, original_deadline)
            self.assertTrue(delivery.creation.received and delivery.launch_arrival.received)
            self.assertTrue(delivery.finished)
        else:
            self.assertEqual(creates + destroys, [])

    def test_join_during_flight_receives_original_activation_once(self):
        self.check_join(live=True)

    def test_join_after_flight_does_not_resurrect_it(self):
        self.check_join(live=False)
