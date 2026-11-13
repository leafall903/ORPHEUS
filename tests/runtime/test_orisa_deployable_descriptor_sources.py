"""Real E/Q deployments preserve existing sources on actor and aura descriptors."""

import unittest
from unittest.mock import patch

import test_server_journal as journal

from ow174.game.link import KIND_FRAME, SYN, peek_connection
from ow174.game.script import decode, expr


class DeployableDescriptorSourceTests(unittest.TestCase):
    def test_actual_e_q_and_aura_sources_reach_existing_and_true_late_peers(self):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=0x02E000000000013E, ally=True)
        owner, enemy, ally = players
        fight = owner.match.combat
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        now = 100.95

        def step(button=0):
            nonlocal now
            for index, (peer, player) in enumerate(zip(peers, players, strict=True)):
                if player.body_script is not None:
                    peer.commands(player.body_script.frame + 1, 1, button if index == 0 else 0, now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        for index in range(180):
            step(16 if index == 0 else 32 if index == 90 else 0)
        barrier, = fight.orisa_barriers.active.values()
        manager = fight.orisa.superchargers
        device, = manager.active.values()
        self.assertEqual((barrier.health, device.health), (600, 200))
        self.assertEqual((manager.scale(owner), manager.scale(ally), manager.scale(enemy)), (1.5, 1.5, 1.0))
        cases = [(barrier.entity, (0x252, 0x25B4), barrier.script),
                 (device.entity, (0xD00, 0x25B4), device.script)]
        for recipient in (owner, ally):
            aura = manager.buffs[device.entity, recipient.body]
            self.assertEqual(aura.source_entity(), owner.body)
            self.assertEqual(aura.vars[174].value(), .5)
            self.assertTrue(aura.state(3).active)
            cases.append((recipient.body, recipient.hero.guid, aura))
        for _entity, _initial, instance in cases:
            self.assertEqual(instance.source_entity(), owner.body)

        handoffs = session.apply('join_match', {
            'match_id': owner.match.id,
            'players': [(4, 'LateEnemy', journal.SOLDIER, 1, False)],
        })
        late = journal.Peer(session, handoffs[0], sent, ('127.0.0.1', 42009))
        peers.append(late)
        late.send(flags=SYN, now=now)
        late.send(now=now)
        late.message(21601, {}, now=now)
        session.report_callbacks()
        for _ in range(48):
            step()
        self.assertTrue(players[-1].spawned)
        late.message(21616, journal.pick(players[-1].entity, journal.SOLDIER), now=now)
        for _ in range(16):
            step()
        fallback = decode.graph_of
        definitions = manager.graphs
        for peer in peers:
            for entity, initial, instance in cases:
                model = (decode.ClientBody(initial) if isinstance(initial, tuple)
                         else decode.ClientBody.for_hero(initial))
                sources, chunks = [], set()
                for raw, _ in sent:
                    if peek_connection(raw) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(raw)
                    if packet.kind != KIND_FRAME:
                        continue
                    _, records = journal.wire_frame(packet)
                    for record, _ in records:
                        if record.entity != entity or record.chunk is None:
                            continue
                        key = record.chunk.count, record.chunk.getvalue()
                        if key in chunks:
                            continue
                        chunks.add(key)
                        with patch.object(decode, 'graph_of', lambda index:
                                          definitions.get(index) or fallback(index)):
                            frame = model.apply(record)
                        sources.extend(item.source_entity for item in frame.instances.values()
                                       if item.descriptor and item.graph == instance.graph.index)
                with self.subTest(viewer=peer.handoff.conn, entity=entity, graph=instance.graph.index):
                    self.assertTrue(sources)
                    self.assertEqual(set(sources), {instance.source_entity()})
                    self.assertEqual(model.sources[instance.id], owner.body)
                self.assertEqual(model.vars[(instance.id, 40)], expr.Entity(
                    owner.body if entity in (barrier.entity, device.entity) else device.entity))
