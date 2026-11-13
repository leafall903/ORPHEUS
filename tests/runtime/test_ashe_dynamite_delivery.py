"""Actual Ashe fuse -> encrypted owner/observer confirmation and retirement."""

import unittest

import test_ashe_combat as fixtures
from test_game_packet_budget import MemorySocket, decoded

from ow174.game import combat, projectiles, world
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import graph
from ow174.game.server import Client, GameServer


@unittest.skipUnless(graph.GRAPHS_PATH.is_file(), 'requires external 104319 graphs')
class DynamiteDeliveryTests(unittest.TestCase):
    def scene(self):
        run, owner, viewer = fixtures.AsheCombatTests().fight(distance=100)
        self.assertTrue(run.match.switch_hero(owner, owner.hero, preserve_pose=True,
                                             start_frame=run.frame + 5000))
        peers = []
        server = GameServer()
        server.sock = MemorySocket()
        for number, player in enumerate((owner, viewer), 1):
            client = Client(server, ('memory.test', 40000+number), number,
                            LinkCipher(b'i' * 32, b'o' * 32), player)
            server.clients[number] = client
            client.open = True
            player.client = client
            client._ack_entity_create(player.entity)
            peers.append({'client': client, 'cipher': LinkCipher(b'o' * 32, b'i' * 32),
                          'window': ReceiveWindow(), 'seq': 0, 'records': []})
        return run, owner, viewer, server, peers

    def pump(self, run, server, peer, *, acknowledge=True):
        client = peer['client']
        client.send_frame(run.frame, run.frame * combat.TICK)
        packet, _, _, _, _, _ = decoded(self, peer['cipher'], server.sock.sent[-1][0])
        records = client.in_flight.get(packet.seq, (None, []))[1]
        peer['records'].extend((run.frame, record) for record in records)
        self.assertTrue(peer['window'].accept(packet.seq))
        if acknowledge:
            ack = Packet(client.conn, KIND_CONTROL, peer['seq'],
                         peer['window'].expected, peer['window'].bits, ACK)
            peer['seq'] += 1
            server._datagram(peer['cipher'].seal(ack), client.address, run.frame * combat.TICK)

    def lifecycle(self, late_ack):
        run, owner, viewer, server, peers = self.scene()
        start = run.frame
        engine = run.match.combat.dynamite
        grenade = None
        for step in range(1, 12):
            run.frame = start+step
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame+5000, buttons=16 if step <= 5 else 0)])
            viewer.take_commands([Command(run.frame)])
            for peer in peers:
                self.pump(run, server, peer)
            if engine.grenades:
                grenade, = engine.grenades
        self.assertIsNotNone(grenade)
        shot = grenade.shot
        observed, = shot.observers
        self.assertEqual(shot.frame, grenade.born + 5000)
        end = grenade.fuse + projectiles.REPORT_WINDOW + projectiles.DELIVERY_GRACE_TICKS + 12
        while run.frame <= end:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame+5000)])
            viewer.take_commands([Command(run.frame)])
            for index, peer in enumerate(peers):
                acknowledge = not (late_ack and index == 0 and run.frame < grenade.fuse + 5)
                self.pump(run, server, peer, acknowledge=acknowledge)
            if run.frame == grenade.fuse:
                self.assertNotIn(grenade, engine.grenades)
                self.assertLessEqual(shot.release_at, grenade.fuse + projectiles.REPORT_WINDOW + 6)
        self.assertTrue(shot.created)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        self.assertNotIn(observed, run.match.combat.projectiles.observed)
        for peer, ids in ((peers[0], set(shot.created)), (peers[1], set(observed.record.ids))):
            records = [(at, record) for at, record in peer['records'] if record.entity in ids]
            created = {record.entity for _, record in records if record.op == world.OP_CREATE}
            destroyed = {record.entity for _, record in records if record.op == world.OP_DESTROY}
            self.assertTrue(ids.issubset(created))
            self.assertTrue(ids.issubset(destroyed))
            releases = [(at, record) for at, record in records
                        if record.op == world.OP_DESTROY and record.ch2 is not None]
            self.assertTrue(releases)
            for at, record in releases:
                expected = projectiles.release(at + 5000 + projectiles.RELEASE_AHEAD)
                self.assertEqual(record.ch2.getvalue(), expected.getvalue())

    def test_natural_fuse_retires_both_encrypted_consumers_in_distinct_epochs(self):
        self.lifecycle(False)

    def test_late_owner_ack_does_not_leave_projectile_proxies(self):
        self.lifecycle(True)

    def test_body_replacement_retains_the_old_shot_clock_and_retires_proxies(self):
        run, owner, viewer, server, peers = self.scene()
        start = run.frame
        engine = run.match.combat.dynamite
        for step in range(1, 31):
            run.frame = start+step
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame+5000, buttons=16 if step <= 5 else 0)])
            viewer.take_commands([Command(run.frame)])
            for peer in peers:
                self.pump(run, server, peer)
        grenade, = engine.grenades
        shot = grenade.shot
        observed, = shot.observers
        birth = owner.body_script.ashe_birth
        self.assertEqual(shot.clock_origin, birth)
        self.assertEqual(observed.clock_origin, birth)
        self.assertTrue(shot.created)
        command_start = run.frame + 9000
        self.assertTrue(run.match.switch_hero(owner, owner.hero, preserve_pose=True,
                                             start_frame=command_start))
        self.assertNotEqual(owner.body_script.ashe_birth, birth)
        self.assertEqual(shot.clock_origin, birth)
        self.assertEqual(observed.clock_origin, birth)
        for step in range(1, 21):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(command_start+step)])
            viewer.take_commands([Command(run.frame)])
            for peer in peers:
                self.pump(run, server, peer)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        self.assertNotIn(observed, run.match.combat.projectiles.observed)
        for peer, ids in ((peers[0], set(shot.created)), (peers[1], set(observed.record.ids))):
            destroyed = {record.entity for _, record in peer['records'] if record.op == world.OP_DESTROY}
            self.assertTrue(ids.issubset(destroyed))
