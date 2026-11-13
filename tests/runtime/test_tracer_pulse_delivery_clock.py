"""Pulse owner/observer lifecycle under encrypted ACKs and a distinct owner epoch."""
import unittest

import test_game_tracer as scenes
import test_game_tracer_pulse as pulse_scenes

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class PulseDeliveryClockTests(unittest.TestCase):
    def test_contact_and_expiry_retire_owner_and_observer_after_delayed_ack(self):
        for expires in (False, True):
            with self.subTest(expires=expires):
                self.exercise(expires=expires, owner_ack_delay=350 if expires else 95)

    def test_missing_owner_confirmation_ack_cleans_up_without_explosion(self):
        self.exercise(expires=False, owner_no_ack=True)

    def test_emitted_shot_keeps_its_original_body_clock_after_replacement(self):
        case = pulse_scenes.PulseBombTests()
        case.setUp()
        bomb = case.launch()
        shot = bomb.shot
        clock = shot.clock_origin
        self.assertIsNotNone(clock)
        self.assertTrue(case.run.match.switch_hero(case.player, case.player.hero, start_frame=9000))
        self.assertNotEqual(case.player.body_script.world_frame_anchor, clock[:2])
        self.assertEqual(shot.clock_origin, clock)
        self.assertTrue(all(delivery.clock_origin == clock for delivery in shot.observers))
        case.run.frame += 1
        case.run.match.update(case.run.frame * combat.TICK, case.run.frame)
        self.assertNotIn(shot, case.fight.projectiles.shots)
        self.assertEqual(shot.clock_origin, clock)

    def exercise(self, *, expires, owner_ack_delay=0, owner_no_ack=False):
        run, caster, target, _ = scenes.TracerPistolTests().fight()
        match, fight = run.match, run.match.combat
        for _ in range(30):
            run.step({})
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=9000))
        caster.commands.reset(last_frame=9000)
        birth = caster.body_script.world_frame_anchor
        if expires:
            caster.mover.position[:] = (0, 1000, 0)
            caster.mover.velocity = (0, 0, 0)
            caster.mover.body = None
            target.mover.position[:] = (100, 1, 0)
            target.mover.body = None
        for index in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(9000 + index)])
            target.take_commands([Command(run.frame)])
        owner_origin = caster.body_script.frame
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())

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
        shot, bomb = None, None
        owner_blocked = False
        physical_end = None
        for step in range(430 if expires else 220):
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
                if (owner_no_ack and client.player is caster and shot is not None
                        and any(record.op == OP_CREATE and record.entity in shot.created
                                for record in sent_records)):
                    owner_blocked = True
                if client.player is not caster or (not owner_blocked and step >= owner_ack_delay):
                    server._datagram(cipher.seal(ack), client.address, now)
            caster.take_commands([Command(owner_origin + step + 1, buttons=32 if step == 0 else 0)])
            target.take_commands([Command(run.frame)])
            if shot is None and fight.pulse_bombs.bombs:
                bomb, = fight.pulse_bombs.bombs
                shot = bomb.shot
            if bomb is not None and physical_end is None and bomb not in fight.pulse_bombs.bombs:
                physical_end = step
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000000045)
        if owner_ack_delay:
            self.assertLess(physical_end, owner_ack_delay)
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertFalse(any(current is shot for current in fight.projectiles.shots),
                         "Physical expiry must also retire its source-owner prediction")
        self.assertFalse(fight.projectiles.observed)
        self.assertEqual(shot.clock_origin, (*birth, caster.body_script.frame_ms))

        owner_records, owner_notices = peers[0][3:]
        lease = fight.projectiles.lease_id(caster, shot.definition, shot.frame)
        ops = [record.op for record in owner_records if record.entity == lease]
        unique = [op for index, op in enumerate(ops) if index == 0 or op != ops[index - 1]]
        self.assertEqual(unique, [OP_CREATE, OP_DESTROY])
        release = next(record for record in owner_records
                       if record.entity == lease and record.op == OP_DESTROY)
        if owner_no_ack:
            self.assertIsNone(release.ch2)
        else:
            self.assert_native_release(release, shot.frame)
        hits = {ordinal: value for ordinal, message, value in owner_notices if message == combat.HIT}
        self.assertEqual([value["+0x78"]["+0x4"] for value in hits.values()], [] if expires else [5, 195])

        observer_records = peers[1][3]
        copies = [record for record in observer_records
                  if projectiles.SEEN_FIRST <= record.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
        self.assertEqual([record.op for record in copies], [OP_CREATE, OP_DESTROY])
        launches = [record for record in observer_records
                    if record.entity == caster.body and record.ch2 is not None]
        self.assertEqual(len(launches), 1)
        self.assert_native_release(copies[-1], shot.frame)

    def assert_native_release(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        bits = BitReader(record.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline = bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, owner_frame)
