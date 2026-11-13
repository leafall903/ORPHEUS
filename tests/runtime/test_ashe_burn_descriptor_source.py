"""Actual E/fuse or owner shot -> target150F source on encrypted body frames."""
import dataclasses
import unittest

import test_server_journal as protocol

from ow174.game.content import Spawn
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, graph

ASHE = 0x02E0000000000200


class AsheBurnDescriptorSourceTests(unittest.TestCase):
    def scenario(self, *, owner_shot):
        original = graph.graph(0x1502).node(21)
        self.assertIsNone(original.field('m_6D7701B7'))
        self.assertEqual(original.field('m_AA4DE426'), 3)
        self.assertEqual(original.field('m_7ABA409C'), 1)
        self.assertEqual(original.field('m_script.m_graph'), '0x058000000000150F')
        self.assertEqual(graph.graph(0x150F).node(10).field('m_effect.m_effect'), '0x03000000000027B8')
        fixture = protocol.ServerJournalTests()
        session, sent = fixture.make()
        scene = dataclasses.replace(protocol.MAP, spawns=(Spawn(0, (0., 0., 0.), 0.),
                                                        Spawn(1, (0., 0., 15.5 if owner_shot else 2.), 180.)))
        peers, players = fixture.connected(session, sent, first_hero=ASHE, ally=True, scene=scene)
        owner, enemy, ally = players
        match, now = owner.match, 101.1

        def step(button=0, *, pitch=0, yaw=0):
            nonlocal now
            now += .016
            for index, (peer, player) in enumerate(zip(peers, players, strict=True)):
                peer.commands(player.body_script.frame + 1, 1, button if index == 0 else 0,
                              pitch=pitch if index == 0 else 0, yaw=yaw if index == 0 else 0, now=now)
            session.tick(now)

        for _ in range(40):
            step()
        first_packet = len(sent)
        if owner_shot:
            ally.spawned = False  # Existing late-viewer admission; no effect state is injected.
        for index in range(45):
            step(16 if index < 5 else 0, pitch=0 if owner_shot else 12288)
        engine = match.combat.dynamite
        grenade, = engine.grenades
        self.assertIsNotNone(grenade.managed_source)
        if owner_shot:
            yaw, pitch = protocol.aim(match.combat._eye(owner), grenade.position)
            step(1, pitch=pitch, yaw=yaw)
            self.assertLess(match.tick, grenade.fuse)
        else:
            while match.tick < grenade.fuse:
                step()
        self.assertNotIn(grenade, engine.grenades)
        expected_targets = (enemy,) if owner_shot else (owner, enemy)
        burns = [engine.burns[owner.body, target.body] for target in expected_targets]
        self.assertNotIn((owner.body, ally.body), engine.burns)
        if owner_shot:
            ally.spawned = True
        for _ in range(8):
            step()
        models = {}
        observations = []
        for viewer_index, (peer, viewer) in enumerate(zip(peers, players, strict=True)):
            for target, burn in zip(expected_targets, burns, strict=True):
                model = decode.ClientBody.for_hero(target.hero.guid)
                descriptors, chunks = [], set()
                for raw, _address in sent:
                    if peek_connection(raw) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(raw)
                    if packet is None or packet.kind != KIND_FRAME:
                        continue
                    _, records = protocol.wire_frame(packet)
                    for record, _fields in records:
                        if record.entity != target.body or record.chunk is None:
                            continue
                        key = record.chunk.count, record.chunk.getvalue()
                        if key in chunks:
                            continue
                        chunks.add(key)
                        frame = model.apply(record)
                        descriptors.extend(item.source_entity for item in frame.instances.values()
                                           if item.descriptor and item.graph == 0x150F)
                observations.append({'viewer': viewer_index, 'target': target.body,
                                     'receiver_self': viewer is target, 'late': owner_shot and viewer is ally,
                                     'source': model.sources.get(burn.instance), 'descriptors': descriptors})
                models[viewer_index, target.body] = model, chunks
                with self.subTest(viewer=viewer_index, target=target.body, owner_shot=owner_shot):
                    self.assertTrue(descriptors)
                    self.assertEqual(set(descriptors), {owner.body})
                    self.assertEqual(model.sources[burn.instance], owner.body)
                    self.assertEqual(model.vars[burn.instance, 27].id, owner.body)
                    definition = burn.script.component.instances[burn.instance].graph
                    self.assertTrue(model.states[burn.instance, definition.node(24).state])
                    self.assertEqual(burn.script.component.instances[burn.instance].source_entity(),
                                     owner.body)
        self.audit = observations
        burn_packets_end = len(sent)
        until = max(burn.until for burn in burns)
        while match.tick <= until + 4:
            step()
        self.assertFalse(engine.burns)
        for burn in burns:
            self.assertEqual(burn.pulses, 20)
            self.assertNotIn(burn.instance, burn.script.component.instances)
        for viewer_index, peer in enumerate(peers):
            for target, burn in zip(expected_targets, burns, strict=True):
                model, chunks = models[viewer_index, target.body]
                for raw, _address in sent[burn_packets_end:]:
                    if peek_connection(raw) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(raw)
                    if packet is None or packet.kind != KIND_FRAME:
                        continue
                    _, records = protocol.wire_frame(packet)
                    for record, _fields in records:
                        if record.entity != target.body or record.chunk is None:
                            continue
                        key = record.chunk.count, record.chunk.getvalue()
                        if key not in chunks:
                            chunks.add(key)
                            model.apply(record)
                self.assertNotIn(burn.instance, model.instances)
                self.assertNotIn(burn.instance, model.sources)
        self.assertEqual(match.combat.vitals[enemy.body].total, 50.)
        if not owner_shot:
            self.assertEqual(match.combat.vitals[owner.body].total, 125.)
        hits = peers[0].hit_notices(first_packet)
        self.assertEqual(len(hits), 21)
        self.assertEqual(sum(hit['+0x78']['+0x4'] for hit in hits), 150.)

    def test_owner_shot_burn_source_reaches_receiver_caster_and_late_ally(self):
        self.scenario(owner_shot=True)

    def test_fuse_self_enemy_and_ally_exclusion_preserve_burn_source(self):
        self.scenario(owner_shot=False)
