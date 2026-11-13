"""Torbjorn owner/observer projectile retirement over encrypted Client ACKs.

These are protocol checks. Native models and impact effects require client QA.
"""

import unittest

import test_game_torbjorn as fixtures

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class TorbjornProjectileDeliveryTests(unittest.TestCase):
    def test_separate_owner_epoch_and_late_ack_retire_all_copies(self):
        self.exercise()

    def test_dropped_owner_confirmation_ack_still_retires_by_grace(self):
        self.exercise(drop_owner_ack=True)

    def test_body_replacement_retains_the_old_shot_clock(self):
        self.exercise(replace_at=40, ack_delay=0, drop_owner_ack=True)

    def exercise(self, *, ack_delay=20, drop_owner_ack=False, replace_at=None):
        run, owner, victim = fixtures.TorbjornProjectileTests().arena(5)
        match = run.match
        self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000))
        owner.commands.reset(last_frame=9000)
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(9000 + offset)])
            victim.take_commands([Command(run.frame)])
        old_clock = owner.body_script.torbjorn_birth
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ("memory.test", 41000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), [], []))
        target = next(target for target in match.combat._targets() if target.owner is victim)
        health = match.combat.vitals_of(target)
        before = health.total
        shot = None
        blocked = False
        replaced = False
        old_ack = None
        for step in range(110):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records, notices in peers:
                client.send_frame(run.frame, now)
                packet, _, reliable, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent)
                notices.extend(reliable)
                self.assertTrue(window.accept(packet.seq))
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                if client.player is owner:
                    if old_ack is None:
                        old_ack = cipher.seal(ack)
                    if (drop_owner_ack and shot is not None
                            and any(record.op == OP_CREATE and record.entity in shot.created
                                    for record in sent)):
                        blocked = True
                if client.player is not owner or (not blocked and step >= ack_delay):
                    server._datagram(cipher.seal(ack), client.address, now)
            if replace_at is not None and step == replace_at:
                self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=12000))
                self.assertNotEqual(owner.body_script.torbjorn_birth, old_clock)
                replaced = True
            if not replaced:
                owner.take_commands([Command(9021 + step, buttons=1 if step == 0 else 0)])
            victim.take_commands([Command(run.frame)])
            if shot is None and match.combat.rockets:
                shot = match.combat.rockets[0].shot
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000002A66)
        self.assertEqual(shot.frame, 9021)
        self.assertEqual(shot.clock_origin, old_clock)
        self.assertEqual(before - health.total, 70)
        self.assertFalse(match.combat.rockets)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        owner_records, notices = peers[0][3:]
        self.assertTrue(shot.created)
        for entity in shot.created:
            self.assertTrue(any(record.entity == entity and record.op == OP_CREATE
                                for record in owner_records))
            retired = [record for record in owner_records
                       if record.entity == entity and record.op == OP_DESTROY]
            self.assertTrue(retired)
            if drop_owner_ack or replaced:
                self.assertIsNone(retired[-1].ch2)
            else:
                self.assert_native_release(retired[-1], shot.frame)
        hits = {ordinal: value for ordinal, message, value in notices if message == combat.HIT}
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits.values()], [70])
        observer_records = peers[1][3]
        for delivery in shot.observers:
            self.assertEqual(delivery.record.frame, shot.frame)
            self.assertEqual(delivery.clock_origin, old_clock)
            records = [record for record in observer_records if record.entity == delivery.record.ids[0]]
            self.assertEqual([record.op for record in records], [OP_CREATE, OP_DESTROY])
            if not replaced:
                self.assert_native_release(records[-1], shot.frame)
        self.assertTrue(any(record.entity == shot.body and record.ch2 is not None
                            for record in observer_records))
        # A late genuine encrypted ACK cannot re-admit a retired generation.
        server._datagram(old_ack, peers[0][0].address, now + combat.TICK)
        match.combat.projectiles.update(run.frame + 1)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        self.assertEqual(shot.clock_origin, old_clock)

    def assert_native_release(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        reader = BitReader(record.ch2.getvalue())
        self.assertEqual((reader.bit(), reader.bit()), (0, 1))
        baseline = reader.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + reader.var_b()) & 0xFFFFFFFF, owner_frame)
