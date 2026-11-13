"""Encrypted mine/0057 and grapple presentation delivery, not native rendering."""

import json
import os
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import test_game_repeated_lifecycles as wire_reader
import test_server_journal as journal
from test_game_combat import aim

from ow174.game import venom, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, graph
from ow174.game.server_snapshot import export_server_state, snapshot_digest

FIXTURES = Path(os.environ.get(
    "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures/behavior",
))


def records_for(peer, sent):
    records, shots = [], []
    original = wire_reader.read_shot

    def shot(reader):
        result = original(reader)
        shots.append(result)
        return result

    with patch.object(wire_reader, "read_shot", shot):
        for data, _ in sent:
            if peek_connection(data) != peer.handoff.conn:
                continue
            packet = peer.cipher.open(data)
            if packet.kind == KIND_FRAME:
                _, updates = journal.wire_frame(packet)
                records.extend(record for record, _ in updates)
    return records, shots


class WidowmakerPresentationDeliveryTests(unittest.TestCase):
    traces: ClassVar[dict] = {}

    def connect(self):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make(journal.wall_world(depth=10))
        peers, players = fixture.connected(session, sent, first_hero=venom.HERO)
        players[1].mover.position[:] = (100, 0, 0)
        players[1].mover.body = None
        return session, sent, peers, players

    def test_deployed_mine_binds_original_graph_and_animation_once(self):
        expected = json.loads((FIXTURES / "widowmaker-mine-presentation-v1.json").read_text())
        session, sent, peers, players = self.connect()
        owner, observer = players
        fight = owner.match.combat
        now, mine, flight, entity = 100.95, None, None, None
        pre_ack = []
        for index in range(130):
            peers[0].commands(owner.body_script.frame + 1, 1, 16 if index == 0 else 0, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            now += .016
            session.tick(now)
            if flight is None and fight.venom.flights:
                flight = fight.venom.flights[0]
            if mine is None and fight.venom.mines:
                mine = fight.venom.mines[0]
                entity = fight.venom.replicas[mine.number][0]
                for player in players:
                    stream = player.client.bot_stream(entity)
                    self.assertIsNotNone(stream)
                    self.assertIsNone(stream.base)
                    self.assertFalse(any(item.entity == entity and item.chunk is not None
                                         for item in player.client.entities))
                    pre_ack.append(True)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertIsNotNone(mine)
        self.assertEqual(flight.shot.definition, venom.DEFINITION)
        self.assertEqual(flight.shot.spawn, venom.MINE_DEFINITION)
        self.assertEqual(len(pre_ack), 2)
        projection = fight.venom.presentations[mine.number]
        component = projection.component
        self.assertEqual(component.entity, entity)
        self.assertEqual([item.graph.index for item in component.instances.values()], [0x25B4, 0x57])
        self.assertTrue(all(item.source_entity() == mine.body for item in component.instances.values()))
        stored = export_server_state(session.server, archives=session._matches.values(),
                                     collision_provider=session._collision_reads, assets=session.registry)
        stored_digest = snapshot_digest(stored)
        del stored  # The exported root contains private transport material; never write it.
        # The enemy's real rifle input retires the already-ACKed model and stream.
        observer.mover.position[:] = (2, 0, mine.position[2])
        observer.mover.body = None
        yaw, pitch = aim(fight._eye(observer), mine.position)
        retired_frame = None
        for index in range(20):
            peers[0].commands(owner.body_script.frame + 1, 1, 0, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 1 if index == 0 else 0,
                              yaw=yaw, pitch=pitch, now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            if mine not in fight.venom.mines:
                if retired_frame is None:
                    retired_frame = component.frame
                self.assertEqual(component.frame, retired_frame)
        self.assertNotIn(mine, fight.venom.mines)
        self.assertNotIn(mine.number, fight.venom.presentations)
        trace = []
        for peer, player in zip(peers, players, strict=True):
            records, shots = records_for(peer, sent)
            own = [record for record in records if record.entity == entity]
            self.assertEqual(sum(record.op == world.OP_CREATE for record in own), 1)
            self.assertEqual(sum(record.op == world.OP_DESTROY for record in own), 1)
            model = decode.ClientBody(tuple(expected["initial_graphs"]))
            animation = []
            for record in own:
                if record.chunk is None:
                    continue
                frame = model.apply(record)
                for item in frame.instances.values():
                    if item.graph == expected["deployed_graph"]:
                        animation.extend(payload for index, (active, payload) in item.states.items()
                                         if index == graph.graph(0x57).node(17).state and active)
            self.assertEqual(model.instances, {1: expected["initial_graphs"][0],
                                               2: expected["deployed_graph"]})
            self.assertIn({"counter": 1}, animation)
            delivery = next(item for item in flight.shot.observers if item.viewer is player)
            launches = [shot for shot in shots if set(shot["ids"]) & set(delivery.record.ids)]
            self.assertTrue(launches)
            self.assertTrue(delivery.creation.received and delivery.launch_arrival.received)
            self.assertIsNone(player.client.actor_generation(entity))
            self.assertIsNone(player.client.bot_stream(entity))
            self.assertFalse(player.client.bind_actor_component(entity, projection, generation=1))
            trace.append({"viewer": player.body, "modelEntity": entity, "creates": 1, "destroys": 1,
                          "graphs": model.instances, "anim17": animation, "wireLaunches": launches,
                          "createACK": True, "launchACK": True, "storedDigest": stored_digest,
                          "oldEpochStoppedAt": retired_frame, "native": "NOT_TESTED"})
        self.traces["mine"] = trace

    def test_actual_grapple_delivers_original_cosmetics_animation_and_cancel(self):
        session, sent, peers, players = self.connect()
        owner, observer = players
        now = 100.95
        for index in range(135):
            buttons = 8 if index == 0 else 0x40 if index == 40 else 0
            peers[0].commands(owner.body_script.frame + 1, 1, buttons, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        trace = []
        for peer, player in zip(peers, players, strict=True):
            records, _ = records_for(peer, sent)
            model = decode.ClientBody.for_hero(venom.HERO)
            events = []
            for record in records:
                if record.entity != owner.body or record.chunk is None:
                    continue
                frame = model.apply(record)
                for item in frame.instances.values():
                    if item.graph == 0x5A:
                        events.extend((graph.graph(0x5A).state(index).pos, active, payload)
                                      for index, (active, payload) in item.states.items())
            self.assertTrue(any(node == 10 and active for node, active, _ in events))
            self.assertTrue(any(node == 104 and active for node, active, _ in events))
            self.assertTrue(any(node == 104 and not active for node, active, _ in events))
            if player is observer:
                self.assertTrue(any(node == 1 and active and payload == {}
                                    for node, active, payload in events))
                self.assertTrue(any(node == 52 and active for node, active, _ in events))
            trace.append({"viewer": player.body,
                          "events": [event for event in events if event[0] in (1, 10, 14, 16, 52, 104)],
                          "native": "NOT_TESTED"})
        self.traces["grapple"] = trace
