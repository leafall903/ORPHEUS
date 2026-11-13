"""Actual encrypted Hook input, original carrier inputs, target VM and ACKs.

03F7 is the original client-only CosmeticEntity35. Its carrier is the caster
body's01C8, not an invented projectile or another server-created hook model.
Native model rendering is not established by decoding these records.
"""
import dataclasses
import json
import math
import unittest
from pathlib import Path

import test_server_journal as journal

from ow174.game import combat, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr, graph

ROADHOG = 0x02E0000000000040
TRAINING_BODY = 0x040000000000146E
CONTRACT = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior/'
                       'roadhog-hook-origin-carrier-v1.json').read_text(encoding='utf-8'))
ARRIVAL = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior/'
                     'roadhog-hook-arrival-message-v1.json').read_text(encoding='utf-8'))


class RoadhogHookCarrierDeliveryTests(unittest.TestCase):
    def connect(self, practice):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        if not practice:
            peers, players = fixture.connected(session, sent, first_hero=ROADHOG)
            return session, sent, peers, players, 100.95
        if graph.entity_body(TRAINING_BODY) is None:
            self.skipTest('requires selected original146E body supplement, not a substitute model')
        scene = dataclasses.replace(journal.PRACTICE_RANGE, spawns=journal.MAP.spawns,
                                    team_sizes=(1, 1))
        entries = [(1, 'Alpha', ROADHOG, 0, False), (2, 'Beta', journal.SOLDIER, 1, False)]
        handoffs = session.create_match(scene, entries)
        peers = [journal.Peer(session, h, sent, ('127.0.0.1', 42000 + i))
                 for i, h in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=journal.SYN)
            peer.send()
            peer.message(21601, {})
        session.report_callbacks()
        journal.acknowledge_ownership(session, peers)
        # Stock15 bot creates/initial VMs need more packets than the small arena.
        for index in range(100):
            now = 100.8 + index * .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        players = session.server.matches[0].players
        self.assertTrue(all(p.spawned for p in players))
        for peer, player, entry in zip(peers, players, entries, strict=True):
            peer.message(21616, journal.pick(player.entity, entry[2]), now=now)
        for _ in range(10):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        return session, sent, peers, players, now + .016

    def exercise(self, *, practice=False, yaw=0, training_body=TRAINING_BODY, immediate=False):
        session, sent, peers, players, now = self.connect(practice)
        hog, observer = players
        match = hog.match
        fight = match.combat
        victim = observer
        model_node = graph.graph(0x1C8).node(35)
        self.assertTrue(model_node.client_only)
        self.assertEqual(model_node.field('m_entityDef.m_entityDef'),
                         CONTRACT['source']['hook_model_definition'])
        if yaw:
            observer.mover.position[:], observer.mover.body = (5., 0., 0.), None
        if practice:
            victim = next(b for b in match.bots if b.body == training_body)
            self.assertEqual(len(match.bots), 15)
            victim.position, victim.route, victim.target, victim.team = (0., 0., 5.), (), None, 1
            hog.mover.position[:], hog.mover.body = (0., 0., 0.), None
            observer.mover.position[:], observer.mover.body = (20., 0., 0.), None
        entity = victim.entity if practice else victim.body
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        initial_health = pool.total
        expected_origin = None
        launch_acks = {p.name: False for p in players}
        victim_acks = {p.name: False for p in players}
        contact_seen = False
        for index in range(110):
            peers[0].commands(hog.body_script.frame + 1, 1,
                              CONTRACT['input']['hook_button'] if index == 0 else 0,
                              yaw=yaw, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            now += .016
            session.tick(now)
            flight = fight.hooks.flights.get(hog.body)
            if flight is not None:
                contact_seen |= flight.target is not None
                expected = flight.instance.evaluate(flight.instance.graph.node(190).field('m_value'))
                expected_origin = expected
                self.assertEqual(flight.instance.find_var(0, 10534).value(), expected)
                self.assertEqual(tuple(flight.origin), tuple(expected))
                self.assertAlmostEqual(math.dist(flight.origin, fight._eye(hog)),
                                       CONTRACT['expected']['forward_origin_offset'], places=5)
                endpoint = flight.instance.find_var(0, 1179).value()
                actual_end = combat._along(flight.origin, flight.direction, flight.reach)
                self.assertLess(math.dist(actual_end, endpoint), 1e-5)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            for viewer in players:
                source = (hog.body_script.stream if viewer is hog
                          else fight.observers.acknowledged_stream(viewer, hog.body))
                base = source.base if source is not None else None
                if base is not None:
                    ids = [i for i, (g, _) in base.instances.items() if g == 0x1C8]
                    launch_acks[viewer.name] |= any(
                        base.states.get((i, 8), (None, False))[1]
                        and base.vars.get((i, 10534), (None,))[0] == expected_origin for i in ids)
                target = (victim.body_script.stream if viewer is victim and not practice
                          else viewer.client.bot_stream(entity) if practice
                          else fight.observers.acknowledged_stream(viewer, entity))
                base = target.base if target is not None else None
                if base is not None:
                    victim_acks[viewer.name] |= any(g == 0x1C9 for g, _ in base.instances.values())
            if immediate and contact_seen and flight is None:
                break
        self.assertIsInstance(expected_origin, expr.Vec3)
        self.assertEqual(pool.total, initial_health - CONTRACT['expected']['contact_damage'])
        self.assertFalse(fight.hooks.controlled(entity))
        self.assertTrue(all(launch_acks.values()), launch_acks)
        self.assertTrue(all(victim_acks.values()), victim_acks)
        # Actual primary input must still damage the same pulled body.
        target = next(t for t in fight._targets() if t.entity == entity)
        a, b = target.segment()
        look = journal.aim(fight._eye(hog), tuple((x + y) / 2 for x, y in zip(a, b, strict=True)))
        primary_start = len(sent)
        for index in range(30):
            peers[0].commands(hog.body_script.frame + 1, 1, 1 if index == 0 else 0,
                              yaw=look[0], pitch=look[1], now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertLess(pool.total, initial_health - 30)
        hits = peers[0].hit_notices(primary_start)
        self.assertTrue(any(hit['+0x78']['+0x0']['+0x0'] == entity for hit in hits))
        victim_graphs = ([row['graph'] for row in graph.entity_body(victim.body)['graphs']]
                         if practice else None)
        rows = []
        for peer, viewer in zip(peers, players, strict=True):
            models = {hog.body: decode.ClientBody.for_hero(ROADHOG),
                      entity: decode.ClientBody(victim_graphs) if practice
                      else decode.ClientBody.for_hero(victim.hero.guid)}
            creates = dict.fromkeys(models, 0)
            origin_seen = victim_seen = False
            chunks = dict.fromkeys(models, 0)
            for data, _ in sent:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, _ in records:
                    if record.entity not in models:
                        continue
                    creates[record.entity] += record.op == world.OP_CREATE
                    if record.chunk is None:
                        continue
                    chunks[record.entity] += 1
                    model = models[record.entity]
                    model.apply(record)
                    if record.entity == hog.body:
                        origin_seen |= any(model.vars.get((i, 10534)) == expected_origin
                                           for i, g in model.instances.items() if g == 0x1C8)
                    else:
                        victim_seen |= any(g == 0x1C9 for g in model.instances.values())
            self.assertEqual(list(creates.values()), [1, 1])
            self.assertTrue(origin_seen)
            self.assertTrue(victim_seen)
            self.assertFalse(any(g == 0x1C9 for g in models[entity].instances.values()))
            health = peer.health_updates(primary_start, entity)
            self.assertTrue(any(current < initial_health - 30 for _, _, current in health), health)
            rows.append({'viewer': viewer.name, 'creates': creates, 'chunks': chunks,
                         'authoredOriginReceived': origin_seen, 'victimGraphReceived': victim_seen,
                         'launchAcknowledged': launch_acks[viewer.name],
                         'victimVmAcknowledged': victim_acks[viewer.name],
                         'postpullHealthRecords': sorted(health)})
        self.evidence = {'practice': practice, 'victim': entity, 'healthAfterPrimary': pool.total,
                         'expectedOrigin': tuple(expected_origin), 'viewers': rows,
                         'encryptedPackets': [{'data': data.hex(), 'address': address}
                                              for data, address in sent]}

    def test_original_hook_origin_and_victim_vm_reach_both_encrypted_peers(self):
        self.exercise(yaw=16384, immediate=True)

    def test_original146e_hook_vm_retires_and_same_bot_remains_hittable(self):
        self.exercise(practice=True)

    def test_original_enemy1403_is_hittable_on_first_input_after_hook_release(self):
        self.exercise(practice=True, training_body=int(ARRIVAL['target_definition'], 16), immediate=True)

    def test_caster_team_and_original_carrier_reach_enemy_and_late_ally(self):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=ROADHOG,
            scene=dataclasses.replace(journal.MAP, team_sizes=(2, 1)))
        hog, enemy = players
        now = 100.95

        def step(button=0):
            nonlocal now
            peers[0].commands(hog.body_script.frame + 1, 1, button, now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        step(CONTRACT['input']['hook_button'])
        for _ in range(20):
            step()
        self.assertIn(hog.body, hog.match.combat.hooks.flights)
        handoff, = session.apply('join_match', {'match_id': hog.match.id,
            'players': [(3, 'LateAlly', journal.SOLDIER, 0, False)]})
        late = journal.Peer(session, handoff, sent, ('127.0.0.1', 42002))
        peers.append(late)
        late.send(flags=journal.SYN, now=now)
        late.send(now=now)
        late.message(21601, {}, now=now)
        session.report_callbacks()
        for _ in range(40):
            step()
        ally = hog.match.players[-1]
        late.message(21616, journal.pick(ally.entity, journal.SOLDIER), now=now)
        for _ in range(8):
            step()
        self.assertIsNotNone(ally.client)
        self.assertEqual((hog.team, enemy.team, ally.team), (0, 1, 0))
        rows = []
        for peer, viewer in zip(peers, hog.match.players, strict=True):
            model = decode.ClientBody()
            teams, graphs_seen = [], set()
            for data, _ in sent:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet is None or packet.kind != KIND_FRAME:
                    continue
                _, records = journal.wire_frame(packet)
                for record, fields in records:
                    if record.entity != hog.body:
                        continue
                    if 26 in fields:
                        teams.append(fields[26][0])
                    if record.chunk is not None:
                        model.apply(record)
                        graphs_seen.update(model.instances.values())
            self.assertEqual(set(teams), {hog.team_bit}, viewer.name)
            self.assertTrue({0x1C8, 0x1C6, 0x0240} <= graphs_seen)
            rows.append({'viewer': viewer.name, 'viewerTeam': viewer.team,
                         'casterFilterBits': teams, 'originalGraphs': sorted(graphs_seen)})
        self.color_evidence = rows
