"""The five real Ice Wall VMs publish their actual0636 allocation source."""
import unittest

import test_server_journal as fixtures
from test_mei_ice_presentation import CONTRACT

from ow174.game import mei, world
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr


class MeiWallDescriptorSourceTests(unittest.TestCase):
    def test_command_wall_source_for_owner_enemy_and_late_ally(self):
        self.audit = []
        case = fixtures.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, first_hero=mei.HERO, ally=True)
        owner, _enemy, ally = players
        ally.spawned = False
        now = 101.1
        for peer, player in zip(peers, players, strict=True):
            peer.commands(player.body_script.frame + 1, 40, 0, now=now)
        x, y, z = owner.mover.position
        yaw, pitch = fixtures.aim(owner.match.combat._eye(owner), (x, y, z + 3))
        for button in (16, 0, 1, 0):
            now += .016
            peers[0].commands(owner.body_script.frame + 1, 1, button,
                              yaw=yaw, pitch=pitch, now=now)
            session.tick(now)
        fight = owner.match.combat
        pieces = list(fight.walls.pieces.values())
        self.assertEqual(len(pieces), CONTRACT['expected']['pillar_count_per_viewer'])
        self.assertEqual(CONTRACT['expected']['played0636_descriptor_source'], 'caster body')
        for step in range(45):
            now += .016
            if step == 8:
                ally.spawned = True
            for peer, player in zip(peers, players, strict=True):
                peer.commands(player.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
        for role, peer, viewer in zip(('self', 'enemy', 'ally-late'), peers, players, strict=True):
            for piece in pieces:
                actor = fight.deployables[piece.entity]
                component = actor.body_script.component
                initial, = [i for i in component.instances.values() if i.graph.index == 0x25B4]
                self.assertEqual(initial.source_entity(), actor.entity)
                self.assertIsNone(initial.descriptor_source_entity)
                child, = [i for i in component.instances.values() if i.graph.index == 0x0636]
                self.assertEqual(child.source_entity(), owner.body)
                self.assertEqual(child.vars[27].value(), expr.Entity(owner.body))
                self.assertEqual(component.vars[8117].value(), (expr.Entity(owner.body),))
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
                        descriptors.extend(i.source_entity for i in frame.instances.values()
                                           if i.descriptor and i.graph == 0x0636)
                with self.subTest(viewer=role, pillar=actor.entity):
                    self.audit.append({'viewer': role, 'pillar': actor.entity,
                                       'casterBody': owner.body, 'petController': owner.entity,
                                       'descriptorSources': descriptors,
                                       'wireVar27': getattr(vm.vars.get((child.id, 27)), 'id', None),
                                       'createACK': viewer.client.bot_create_acked(actor.entity)})
                    self.assertEqual(len(creates), 1)
                    self.assertEqual(creates[0][26][0], owner.team_bit)
                    self.assertEqual(creates[0][72][0], owner.entity)
                    self.assertEqual(vm.vars[(child.id, 27)], expr.Entity(owner.body))
                    self.assertEqual(vm.vars[(initial.id, 27)], expr.Entity(actor.entity))
                    self.assertIsNone(vm.sources.get(initial.id))
                    self.assertEqual(vm.vars[(0, 8117)], (expr.Entity(owner.body),))
                    self.assertTrue(descriptors)
                    self.assertEqual(set(descriptors), {owner.body})
                    self.assertEqual(vm.sources[child.id], owner.body)
                    self.assertTrue(viewer.client.bot_create_acked(actor.entity))
                    cached = viewer.client.bot_stream(actor.entity).base
                    self.assertEqual(cached.sources[child.id], owner.body)


if __name__ == '__main__':
    unittest.main()
