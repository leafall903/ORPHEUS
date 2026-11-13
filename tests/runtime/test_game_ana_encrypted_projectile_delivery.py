"""Ana lifecycle through encrypted Client packets and real ACK processing.

These in-memory transport tests establish protocol behavior, with original
client rendering and visual acceptance still pending.
"""

import unittest

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime import test_game_ana as fixture
from tests.runtime.test_game_combat import aim
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class AnaEncryptedProjectileDeliveryTests(unittest.TestCase):
    def test_distinct_owner_epoch_hit_and_delayed_ack_retire_all_proxies(self):
        for button in (1, 8, 16):
            with self.subTest(button=button):
                self.exercise(button, owner_ack_delay=60)

    def test_rifle_natural_expiry_and_late_ack_retire_all_proxies(self):
        self.exercise(1, miss=True, owner_ack_delay=60)

    def test_dropped_owner_confirmation_ack_cleans_up_without_resurrection(self):
        self.exercise(1, drop_confirmation=True)

    def exercise(self, button, *, miss=False, owner_ack_delay=0, drop_confirmation=False):
        run, owner, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True, distance=12)
        match, fight = run.match, run.match.combat
        owner_origin = 9000
        self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=owner_origin))
        owner.commands.reset(last_frame=owner_origin)
        birth = owner.body_script.ana_birth
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(owner_origin + offset)])
            target.take_commands([Command(run.frame)])
        owner_origin += 20
        point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
        direction = fixture.grenade_aim(run, owner, point) if button == 16 else aim(fight._eye(owner), point)
        if miss:
            direction = (0, -16384)
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ("memory.test", 40100 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for actor in run.players:
                client._track_body_create(actor.body)
                client._ack_entity_create(actor.entity)
                client._ack_entity_create(actor.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), []))
        shot = rocket = None
        owner_blocked = False
        for step in range(160):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records in peers:
                client.send_frame(run.frame, now)
                packet, _, _, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent)
                self.assertTrue(window.accept(packet.seq))
                if (drop_confirmation and client.player is owner and shot is not None
                        and any(row.op == OP_CREATE and row.entity in shot.created for row in sent)):
                    owner_blocked = True
                if client.player is not owner or (not owner_blocked and step >= owner_ack_delay):
                    ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                    server._datagram(cipher.seal(ack), client.address, now)
            owner.take_commands([Command(owner_origin + step + 1, yaw=direction[0], pitch=direction[1],
                                         buttons=button if step == 0 else 0)])
            target.take_commands([Command(run.frame)])
            if shot is None and fight.rockets:
                rocket, = fight.rockets
                shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertFalse(fight.rockets)
        self.assertFalse(any(item is shot for item in fight.projectiles.shots),
                         f"Shot survived physical end: release_at={shot.release_at}, world={match.tick}")
        self.assertFalse(fight.projectiles.observed)
        self.assertEqual(shot.clock_origin, (*birth, 16))
        expected = 200 if miss else {1: 130, 8: 195, 16: 140}[button]
        self.assertEqual(fight.vitals_of(entry).total, expected)
        self.assertEqual(rocket.ended, "expired" if miss else "body")
        # Deliver ACKs after the cleanup boundary, including previously dropped
        # owner confirmations. Retired-generation creates must never be resent.
        late_records = {client: [] for client, _, _, _ in peers}
        for step in range(160, 172):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records in peers:
                client.send_frame(run.frame, now)
                packet, _, _, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent)
                late_records[client].extend(sent)
                self.assertTrue(window.accept(packet.seq))
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                server._datagram(cipher.seal(ack), client.address, now)
        self.assertFalse(fight.projectiles.shots)
        self.assertFalse(fight.projectiles.observed)
        for peer_index, (client, _, _, records) in enumerate(peers):
            if button == 1 and peer_index == 0:
                entity = fight.projectiles.lease_id(owner, shot.definition, shot.frame)
                ids = {entity}
            elif button == 8 and peer_index == 0:
                continue  # The authored SHOTS_ONLY dart is locally predicted.
            else:
                ids = {row.entity for row in records
                       if projectiles.SEEN_FIRST <= row.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN}
            self.assertEqual(len(ids), 1)
            entity, = ids
            relevant = [row for row in records if row.entity == entity]
            ops = [row.op for row in relevant]
            self.assertEqual([op for index, op in enumerate(ops) if index == 0 or op != ops[index - 1]],
                             [OP_CREATE, OP_DESTROY])
            destroyed = next(row for row in relevant if row.op == OP_DESTROY)
            outstanding = [*client.entities, *(row for _, rows in client.in_flight.values() for row in rows)]
            self.assertFalse(any(row.entity == entity and row.op == OP_CREATE for row in outstanding))
            self.assertFalse(any(row.entity == entity and row.op == OP_CREATE for row in late_records[client]))
            if drop_confirmation and peer_index == 0:
                self.assertIsNone(destroyed.ch2)
            else:
                self.assertIsNotNone(destroyed.ch2)
                bits = BitReader(destroyed.ch2.getvalue())
                self.assertEqual((bits.bit(), bits.bit()), (0, 1))
                baseline = bits.var_b()
                self.assertEqual(baseline, projectiles.NO_BASELINE)
                self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, shot.frame)
        old_body, captured_clock = shot.body, shot.clock_origin
        self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=15000))
        self.assertNotEqual(owner.body, old_body)
        self.assertNotEqual((*owner.body_script.ana_birth, 16), captured_clock)
        self.assertEqual(shot.clock_origin, captured_clock)
