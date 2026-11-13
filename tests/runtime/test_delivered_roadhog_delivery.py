"""Roadhog slug lifecycle through production encrypted client acknowledgments."""
import unittest

import test_game_roadhog as scenes
from tests.runtime.test_game_packet_budget import MemorySocket, decoded

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY


class RoadhogDeliveryTests(unittest.TestCase):
    def test_late_owner_ack_after_slug_hit_retires_both_copies(self):
        self.exercise(distance=5, expected_damage=50)

    def test_expired_slug_retires_both_copies(self):
        self.exercise(distance=200, expected_damage=0)

    def exercise(self, *, distance, expected_damage):
        run, hog, target, pool = scenes.RoadhogSecondaryTests().session(distance)
        match = run.match
        origin = 9000
        self.assertTrue(match.switch_hero(hog, hog.hero, preserve_pose=True, start_frame=origin))
        hog.commands.reset(last_frame=origin)
        for offset in range(1, 41):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            hog.take_commands([Command(origin + offset)])
            target.take_commands([Command(run.frame)])
        origin += 40
        angles = scenes.torso_aim(run, hog, target)
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ("memory.test", 40000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), [], []))
        shot = None
        before = pool.total
        for step in range(130):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records, notices in peers:
                client.send_frame(run.frame, now)
                packet, _, reliable, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent_records = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent_records)
                notices.extend(reliable)
                self.assertTrue(window.accept(packet.seq))
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                if client.player is not hog or step >= 20:
                    server._datagram(cipher.seal(ack), client.address, now)
            hog.take_commands([Command(origin + step + 1, yaw=angles[0], pitch=angles[1],
                                       buttons=2 if step == 0 else 0)])
            target.take_commands([Command(run.frame)])
            if shot is None:
                shot = next((r.shot for r in match.combat.rockets
                             if r.shooter is hog and r.shot is not None), None)
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000000295)
        captured_clock = (*hog.body_script.roadhog_birth, hog.body_script.frame_ms)
        self.assertEqual(shot.clock_origin, captured_clock)
        self.assertAlmostEqual(before - pool.total, expected_damage, places=4)
        self.assertFalse(match.combat.rockets)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        lease = match.combat.projectiles.lease_id(hog, shot.definition, origin + 1)
        owner_records = [r for r in peers[0][3] if r.entity == lease]
        self.assertEqual([op for i, op in enumerate([r.op for r in owner_records])
                          if i == 0 or op != owner_records[i - 1].op], [OP_CREATE, OP_DESTROY])
        for entity in shot.created:
            operations = [r.op for r in peers[0][3] if r.entity == entity]
            self.assertIn(OP_CREATE, operations)
            self.assertIn(OP_DESTROY, operations)
        hits = {ordinal: value for ordinal, msg_id, value in peers[0][4]
                if msg_id == combat.HIT}
        self.assertAlmostEqual(sum(hit["+0x78"]["+0x4"] for hit in hits.values()),
                               expected_damage, places=4)
        observer_records = [r for r in peers[1][3]
                            if projectiles.SEEN_FIRST <= r.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
        self.assertEqual([r.op for r in observer_records], [OP_CREATE, OP_DESTROY])
        self.assertEqual(len([r for r in peers[1][3] if r.entity == hog.body and r.ch2 is not None]), 1)
        for release in (next(r for r in owner_records if r.op == OP_DESTROY), observer_records[-1]):
            self.assertIsNotNone(release.ch2)
            bits = BitReader(release.ch2.getvalue())
            self.assertEqual((bits.bit(), bits.bit()), (0, 1))
            baseline = bits.var_b()
            self.assertEqual(baseline, projectiles.NO_BASELINE)
            self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, shot.frame)
        self.assertTrue(match.switch_hero(hog, hog.hero, preserve_pose=True, start_frame=40000))
        self.assertNotEqual((*hog.body_script.roadhog_birth, hog.body_script.frame_ms), captured_clock)
        self.assertEqual(shot.clock_origin, captured_clock)
