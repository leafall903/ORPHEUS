"""Actual Hanzo release through encrypted owner and observer peers; no visual claim."""

import json
import unittest
from pathlib import Path

import test_game_hanzo as hero_tests
from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class HanzoProjectileDeliveryTests(unittest.TestCase):
    def test_different_owner_epoch_retires_owner_and_observer_after_real_hit(self):
        self.exercise()

    def test_late_owner_ack_does_not_leave_a_projectile_proxy(self):
        self.exercise(owner_ack_delay=100)

    def test_confirmation_ack_after_grace_and_body_change_cannot_resurrect_a_proxy(self):
        self.exercise(hold_confirmation=True)

    def exercise(self, owner_ack_delay=0, hold_confirmation=False):
        fixture = Path(__file__).resolve().parents[2] / "fixtures/behavior/hanzo-projectile-delivery-v1.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))["expected"]
        run, caster, target = hero_tests.HanzoCombatTests().arena()
        match = run.match
        owner_origin = 9000
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True,
                                         start_frame=owner_origin))
        caster.commands.reset(last_frame=owner_origin)
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(owner_origin + offset)])
            target.take_commands([Command(run.frame)])
        owner_origin += 20
        old_script = caster.body_script
        birth = old_script.world_frame_anchor
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
        held = None
        blocked = False
        for step in range(180):
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
                if (hold_confirmation and client.player is caster and shot is not None
                        and any(r.op == OP_CREATE and r.entity in shot.created for r in sent_records)):
                    blocked = True
                    if held is None:
                        held = (client, cipher.seal(ack))
                if client.player is not caster or (step >= owner_ack_delay and (not blocked or step >= 165)):
                    server._datagram(cipher.seal(ack), client.address, now)
            caster.take_commands([Command(owner_origin + step + 1, buttons=1 if step < 70 else 0)])
            target.take_commands([Command(run.frame)])
            if shot is None and match.combat.rockets:
                shot = match.combat.rockets[0].shot
            if hold_confirmation and step == 150:
                self.assertNotIn(shot, match.combat.projectiles.shots)
                self.assertTrue(match.switch_hero(caster, heroes()[SOLDIER], preserve_pose=True,
                                                 start_frame=owner_origin + step + 1))
                self.assertIsNot(caster.body_script, old_script)
                self.assertEqual(shot.clock_origin, (*birth, old_script.frame_ms))
                self.assertIsNotNone(held)
                server._datagram(held[1], held[0].address, now)
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000000144)
        self.assertEqual(match.combat.vitals[target.body].total, expected["target_health"])
        self.assertFalse(match.combat.rockets)
        self.assertNotIn(shot, match.combat.projectiles.shots,
                         "physical hit must also retire the owner-clock Shot")
        self.assertFalse(match.combat.projectiles.observed)
        self.assertEqual(shot.clock_origin, (*birth, old_script.frame_ms))
        owner_records, notices = peers[0][3:]
        self.assertTrue(shot.created)
        for lease in shot.created:
            owner = [record for record in owner_records if record.entity == lease]
            self.assert_logical_ops(owner)
            owner_release = next(r for r in owner if r.op == OP_DESTROY)
            if hold_confirmation:
                self.assertIsNone(owner_release.ch2)
            else:
                self.assert_release_frame(owner_release, shot.frame)
        hits = {ordinal: value for ordinal, msg, value in notices if msg == combat.HIT}.values()
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], expected["owner_hit_amounts"])
        remote = [record for record in peers[1][3]
                  if projectiles.SEEN_FIRST <= record.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
        self.assert_logical_ops(remote)
        self.assert_release_frame(next(r for r in remote if r.op == OP_DESTROY), shot.frame)
        launches = [record for record in peers[1][3] if record.entity == shot.body and record.ch2 is not None]
        self.assertEqual(len(launches), 1)
        for client, *_ in peers:
            for lease in shot.created:
                self.assertNotIn(lease, client.entities)
            for record in remote:
                self.assertNotIn(record.entity, client.entities)

    def assert_logical_ops(self, records):
        ops = [record.op for record in records]
        self.assertEqual([op for i, op in enumerate(ops) if i == 0 or op != ops[i - 1]],
                         [OP_CREATE, OP_DESTROY])

    def assert_release_frame(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        reader = BitReader(record.ch2.getvalue())
        self.assertEqual((reader.bit(), reader.bit()), (0, 1))
        baseline = reader.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + reader.var_b()) & 0xFFFFFFFF, owner_frame)
