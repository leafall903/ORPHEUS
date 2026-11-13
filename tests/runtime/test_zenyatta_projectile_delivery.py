"""Zenyatta primary confirmation and observer delivery through encrypted Client ACKs.

Protocol evidence only. Native model, animation and explosion rendering still
require the original client; these checks deliberately make no visual claim.
"""
import unittest

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime import test_zenyatta_combat as scenes
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class ZenyattaProjectileDeliveryTests(unittest.TestCase):
    def test_replacement_keeps_the_old_shot_epoch_and_retires_it(self):
        run, caster, _, _ = scenes.ZenyattaCombatTests().arena()
        self.assertTrue(run.match.switch_hero(caster, caster.hero, start_frame=9000))
        caster.commands.reset(last_frame=9000)
        caster.take_commands([Command(9001, pitch=-6000, buttons=1)])
        orb, = run.match.combat.rockets
        shot = orb.shot
        captured = shot.clock_origin
        self.assertEqual(captured, (*caster.body_script.world_frame_anchor, 16))
        self.assertTrue(run.match.switch_hero(caster, caster.hero, start_frame=12000))
        self.assertNotEqual(captured[:2], caster.body_script.world_frame_anchor)
        for _ in range(8):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(shot.clock_origin, captured)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        self.assertFalse(run.match.combat.projectiles.observed)

    def test_hit_with_separate_epoch_and_late_owner_ack_retires_all_copies(self):
        self.exercise(owner_ack_delay=20, owner_origin=9000)

    def test_expiry_with_separate_epoch_and_late_owner_ack_retires_all_copies(self):
        self.exercise(owner_ack_delay=20, owner_origin=9000, expire=True)

    def test_dropped_confirmation_ack_retires_without_owner_explosion(self):
        self.exercise(owner_origin=9000, owner_no_ack=True)

    def exercise(
        self, *, owner_ack_delay=0, owner_origin=None, owner_frame_ms=16, owner_no_ack=False, expire=False,
    ):
        helper = scenes.ZenyattaCombatTests()
        run, caster, target, health = helper.arena()
        if expire:
            target.mover.position[0] += 100
            target.mover.body = None
        match = run.match
        before = health.total
        if owner_origin is not None:
            caster.quanta = owner_frame_ms * 1000
            self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True,
                                              start_frame=owner_origin))
            caster.commands.reset(last_frame=owner_origin)
            # Complete the replacement's normal observer introduction before
            # substituting encrypted peers for the setup model.
            sent = 0
            for at in range(1, 21):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                through = at * 16 // owner_frame_ms
                if through > sent:
                    caster.take_commands([Command(owner_origin + frame)
                                          for frame in range(sent + 1, through + 1)])
                    sent = through
                target.take_commands([Command(run.frame)])
            owner_origin += sent
        else:
            owner_origin = run.frame
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 40000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), [], []))
        cast_frame = owner_origin + 1
        shot = None
        owner_sent = 0
        owner_blocked = False
        for step in range(240):
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
            owner_through = (step + 1) * 16 // owner_frame_ms
            if owner_through > owner_sent:
                caster.take_commands([
                    Command(owner_origin + at, pitch=-6000 if expire else 600, buttons=1 if at == 1 else 0)
                    for at in range(owner_sent + 1, owner_through + 1)
                ])
                owner_sent = owner_through
            target.take_commands([Command(run.frame)])
            if shot is None and match.combat.rockets:
                shot = match.combat.rockets[0].shot
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000000236)
        birth = caster.body_script.world_frame_anchor
        self.assertEqual(before - health.total, 0 if expire else 48)
        self.assertFalse(match.combat.rockets)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        self.assertEqual(shot.clock_origin, (*birth, owner_frame_ms))
        owner_records, owner_notices = peers[0][3:]
        self.assertTrue(shot.created)
        for entity in shot.created:
            operations = [record.op for record in owner_records if record.entity == entity]
            self.assertIn(OP_CREATE, operations)
            self.assertIn(OP_DESTROY, operations)
            self.assertLess(operations.index(OP_CREATE), operations.index(OP_DESTROY))
            self.assertEqual(operations[-1], OP_DESTROY)
        lease = match.combat.projectiles.lease_id(caster, shot.definition, cast_frame)
        ops = [r.op for r in owner_records if r.entity == lease]
        logical_ops = [op for index, op in enumerate(ops) if index == 0 or op != ops[index - 1]]
        self.assertEqual(logical_ops, [OP_CREATE, OP_DESTROY])
        owner_release = next(r for r in owner_records if r.entity == lease and r.op == OP_DESTROY)
        if owner_no_ack:
            self.assertIsNone(owner_release.ch2)
        else:
            self.assert_release_frame(owner_release, shot.frame)
        hits = {ordinal: value for ordinal, msg_id, value in owner_notices if msg_id == combat.HIT}.values()
        self.assertEqual([hit['+0x78']['+0x4'] for hit in hits], [] if expire else [48])
        observer_records = peers[1][3]
        projectile_records = [
            record for record in observer_records
            if projectiles.SEEN_FIRST <= record.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN
        ]
        self.assertEqual([r.op for r in projectile_records], [OP_CREATE, OP_DESTROY])
        launches = [r for r in observer_records if r.entity == caster.body and r.ch2 is not None]
        self.assertEqual(len(launches), 1)
        if not owner_no_ack:
            self.assert_release_frame(projectile_records[-1], shot.frame)

    def assert_release_frame(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        bits = BitReader(record.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline = bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, owner_frame,
                           'native release must be ahead of this owner epoch, not the world epoch')
