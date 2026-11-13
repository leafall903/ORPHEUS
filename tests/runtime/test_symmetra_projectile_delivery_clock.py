"""Actual orb owner/observer ACK and native release labels across body epochs."""

import unittest

import test_symmetra_combat as scenes
from test_game_packet_budget import MemorySocket, decoded

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY


class OrbDeliveryClockTests(unittest.TestCase):
    def test_separate_owner_epoch_delivers_and_retires_both_orb_copies(self):
        self.exercise()

    def test_late_owner_ack_after_impact_still_retires_both_copies(self):
        self.exercise(owner_ack_delay=130)

    def test_longer_owner_frames_keep_native_release_in_owner_domain(self):
        self.exercise(frame_ms=32, owner_ack_delay=130)

    def test_ack_after_cleanup_grace_cannot_leave_or_recreate_an_owner_proxy(self):
        self.exercise(owner_ack_delay=190, owner_impact=False)

    def test_body_replacement_keeps_the_old_shot_clock_and_retires_old_proxies(self):
        self.exercise(owner_ack_delay=130, owner_impact=False, replace_body=True)

    def exercise(self, *, frame_ms=16, owner_ack_delay=0, owner_impact=True, replace_body=False):
        run, owner, enemy = scenes.arena()
        match = run.match
        owner.quanta = frame_ms * 1000
        self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000))
        owner.commands.reset(last_frame=9000)
        birth = owner.body_script.symmetra_birth
        sent = 0
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            through = offset * 16 // frame_ms
            if through > sent:
                owner.take_commands([Command(9000 + frame) for frame in range(sent + 1, through + 1)])
                sent = through
            enemy.take_commands([Command(run.frame)])
        origin = 9000 + sent
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ("memory.test", 42000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            client.open, player.client = True, client
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), []))
        owner_sent, shot = 0, None
        for offset in range(250):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            if replace_body and offset == 120:
                self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=12000))
                owner.commands.reset(last_frame=12000)
            for client, cipher, window, records in peers:
                client.send_frame(run.frame, now)
                packet, *_ = decoded(self, cipher, server.sock.sent[-1][0])
                records.extend(client.in_flight.get(packet.seq, (None, []))[1])
                self.assertTrue(window.accept(packet.seq))
                if client.player is not owner or offset >= owner_ack_delay:
                    ack = Packet(client.conn, KIND_CONTROL, offset, window.expected, window.bits, ACK)
                    server._datagram(cipher.seal(ack), client.address, now)
            through = (offset + 1) * 16 // frame_ms
            if through > owner_sent:
                owner.take_commands([
                    Command(origin + frame, buttons=2 if frame * frame_ms <= 90 * 16 else 0)
                    for frame in range(owner_sent + 1, through + 1)
                ])
                owner_sent = through
            enemy.take_commands([Command(run.frame)])
            if shot is None and match.combat.rockets:
                [rocket] = match.combat.rockets
                shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertTrue(shot.authoritative)
        self.assertEqual(shot.clock_origin, (*birth, frame_ms))
        if replace_body:
            self.assertNotEqual(owner.body_script.symmetra_birth, birth)
        self.assertEqual(scenes.health(run, enemy).total, 80)
        self.assertFalse(match.combat.rockets)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        for client, _cipher, _window, records in peers:
            copies = [record for record in records
                      if projectiles.SEEN_FIRST <= record.entity
                      < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
            logical = []
            for record in copies:
                if not logical or record.op != logical[-1]:
                    logical.append(record.op)
            self.assertEqual(logical, [OP_CREATE, OP_DESTROY])
            release = next(record for record in copies if record.op == OP_DESTROY)
            if client.player is owner and not owner_impact:
                self.assertIsNone(release.ch2)
                continue
            self.assertIsNotNone(release.ch2)
            bits = BitReader(release.ch2.getvalue())
            self.assertEqual((bits.bit(), bits.bit()), (0, 1))
            baseline = bits.var_b()
            self.assertEqual(baseline, projectiles.NO_BASELINE)
            release_frame = (baseline + bits.var_b()) & 0xFFFFFFFF
            self.assertGreater(release_frame, shot.frame,
                               "native release must be ahead of its owner epoch")
