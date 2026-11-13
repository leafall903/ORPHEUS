"""Actual Zarya projectile retirement through encrypted owner and observer ACKs."""

import unittest

import test_zarya_reported_presentation as presentation

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class ZaryaOwnerClockTests(unittest.TestCase):
    def test_distinct_owner_epoch_retires_secondary_and_graviton_after_late_ack(self):
        for button in (2, 32):
            with self.subTest(button=button):
                self.exercise(button)

    def test_missing_owner_confirmation_ack_cannot_leave_projectile_proxies(self):
        self.exercise(2, owner_no_ack=True)

    def test_emitted_clock_remains_the_old_body_clock_after_replacement(self):
        run, caster, _, _ = presentation.ZaryaPresentationTests().scene()
        match, fight = run.match, run.match.combat
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=9000))
        caster.commands.reset(last_frame=9000)
        birth = caster.body_script.world_frame_anchor
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        caster.take_commands([Command(9001, buttons=2)])
        rocket, = fight.rockets
        shot = rocket.shot
        self.assertEqual(shot.clock_origin, (*birth, 16))
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=18000))
        self.assertNotEqual(caster.body, shot.body)
        self.assertNotEqual(caster.body_script.world_frame_anchor, birth)
        self.assertEqual(shot.clock_origin, (*birth, 16))
        match.update((run.frame + 100) * combat.TICK, run.frame + 100, frames=False)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertEqual(shot.clock_origin, (*birth, 16))

    def exercise(self, button, *, owner_no_ack=False):
        run, caster, target, _ = presentation.ZaryaPresentationTests().scene()
        match, fight = run.match, run.match.combat
        origin = 9000
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=origin))
        caster.commands.reset(last_frame=origin)
        birth = caster.body_script.world_frame_anchor
        for step in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(origin + step)])
            for player in run.players[1:]:
                player.take_commands([Command(run.frame)])
        origin += 20
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(2100)
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
        shot = None
        owner_blocked = False
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
                if (owner_no_ack and client.player is caster and shot is not None
                        and any(record.op == OP_CREATE and record.entity in shot.created for record in sent)):
                    owner_blocked = True
                if client.player is not caster or (not owner_blocked and step >= 20):
                    server._datagram(cipher.seal(ack), client.address, now)
            caster.take_commands([Command(origin + step + 1, buttons=button if step == 0 else 0)])
            for player in run.players[1:]:
                player.take_commands([Command(run.frame)])
            if shot is None and fight.rockets:
                shot = fight.rockets[0].shot
        self.assertIsNotNone(shot)
        self.assertFalse(fight.rockets)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertFalse(fight.projectiles.observed)
        self.assertEqual(shot.clock_origin, (*birth, caster.body_script.frame_ms))
        if owner_no_ack:
            client, cipher, window, _, _ = peers[0]
            late = Packet(client.conn, KIND_CONTROL, 110, window.expected, window.bits, ACK)
            server._datagram(cipher.seal(late), client.address, run.frame * combat.TICK)
            for _ in range(5):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                client.send_frame(run.frame, run.frame * combat.TICK)
                packet, _, _, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent = client.in_flight.get(packet.seq, (None, []))[1]
                self.assertFalse(any(record.op == OP_CREATE and record.entity in shot.created
                                     for record in sent))
            self.assertNotIn(shot, fight.projectiles.shots)
            self.assertFalse(fight.projectiles.observed)
        self.assertLess(fight.vitals[target.body].total, 200)
        if button == 2:
            self.assertAlmostEqual(fight.vitals[target.body].total, 153)
        owner_records, owner_notices = peers[0][3:]
        lease = fight.projectiles.lease_id(caster, shot.definition, origin + 1)
        records = [record for record in owner_records if record.entity == lease]
        operations = [record.op for record in records]
        logical = [op for index, op in enumerate(operations)
                   if index == 0 or op != operations[index - 1]]
        self.assertEqual(logical, [OP_CREATE, OP_DESTROY])
        owner_destroy = next(record for record in records if record.op == OP_DESTROY)
        if owner_no_ack:
            self.assertIsNone(owner_destroy.ch2)
        else:
            self.assert_owner_release(owner_destroy, origin + 1)
        self.assertTrue(any(message == combat.HIT for _, message, _ in owner_notices))
        observer_records = peers[1][3]
        proxies = [
            record for record in observer_records
            if (record.entity == lease if button == 32 else
                projectiles.SEEN_FIRST <= record.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN)
        ]
        self.assertEqual([record.op for record in proxies], [OP_CREATE, OP_DESTROY])
        self.assertTrue(any(record.entity == caster.body and record.ch2 is not None
                            for record in observer_records))
        if not owner_no_ack:
            self.assert_owner_release(proxies[-1], origin + 1)

    def assert_owner_release(self, record, frame):
        self.assertIsNotNone(record.ch2)
        bits = BitReader(record.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline = bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, frame)
