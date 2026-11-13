"""Mei's actual encrypted command/ACK path must retire owner and remote copies."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_delivered_mei_combat as scenes
from test_game_packet_budget import MemorySocket, decoded
from test_server_journal import Peer

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, KIND_FRAME, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/mei-projectile-delivery-v1.json').read_text())


class MeiProjectileDeliveryTests(unittest.TestCase):
    def exercise(self, *, button=2, miss=False, replace=False):
        run, owner, enemy, target = scenes.MeiCombatTests().duel()
        match = run.match
        self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000))
        owner.commands.reset(last_frame=9000)
        for at in range(1, 31):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(9000 + at)])
            enemy.take_commands([Command(run.frame)])
        birth = owner.body_script.cryo_birth
        self.assertNotEqual(birth[0], birth[1])
        self.assertEqual((*birth, 16), tuple(CONTRACT['input']['clock_origin']))
        owner.body_script.set_ult_charge(1610.)
        facing = ((0, -6000) if miss else
                  scenes.aim(match.combat._eye(owner), (target.feet[0], target.feet[1] + .9, target.feet[2])))
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 42000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append(SimpleNamespace(client=client, cipher=LinkCipher(outgoing, incoming),
                                         window=ReceiveWindow(), seq=0, records=[], notices=[], late=[]))

        def send(peer, *, payload=b'', now, acknowledge=True):
            packet = Packet(peer.client.conn, KIND_FRAME if payload else KIND_CONTROL,
                            peer.seq, peer.window.expected if acknowledge else 0,
                            peer.window.bits if acknowledge else 0, ACK, payload)
            peer.seq += 1
            raw = peer.cipher.seal(packet)
            server._datagram(raw, peer.client.address, now)

        for peer in peers:
            peer.send = lambda payload=b'', now=0., p=peer: send(p, payload=payload, now=now,
                                                               acknowledge=p is not peers[0] or step >= 65)
        shot = None
        retired_at = None
        physical_end_at = None
        for step in range(160):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for peer in peers:
                client = peer.client
                client.send_frame(run.frame, now)
                packet, _, reliable, _, _, _ = decoded(self, peer.cipher, server.sock.sent[-1][0])
                peer.notices.extend(reliable)
                records = client.in_flight.get(packet.seq, (None, []))[1]
                peer.records.extend(records)
                self.assertTrue(peer.window.accept(packet.seq))
                # Replay a genuinely delayed encrypted ACK after retirement too.
                ack = Packet(client.conn, KIND_CONTROL, peer.seq, peer.window.expected, peer.window.bits, ACK)
                peer.seq += 1
                raw = peer.cipher.seal(ack)
                if peer is peers[0] and step < 65:
                    peer.late.append(raw)
                else:
                    server._datagram(raw, client.address, now)
            Peer.commands(peers[0], 9031 + step, 1, button if step == 0 else 0,
                          yaw=facing[0], pitch=facing[1], now=now)
            Peer.commands(peers[1], run.frame, 1, 0, now=now)
            if shot is None:
                flights = match.combat.blizzard.flights if button == 32 else match.combat.rockets
                if flights:
                    shot = flights[0].shot
            if replace and step == 66:
                self.assertIsNotNone(shot)
                self.assertIn(shot, match.combat.projectiles.shots)
                self.assertTrue(shot.created)
                self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=12000))
                owner.commands.reset(last_frame=12000)
                self.assertNotEqual(owner.body_script.cryo_birth, birth)
            if shot is not None and shot not in match.combat.projectiles.shots and retired_at is None:
                retired_at = run.frame
            if (shot is not None and not match.combat.rockets and not match.combat.blizzard.flights
                    and physical_end_at is None):
                physical_end_at = run.frame
        self.assertIsNotNone(shot)
        self.assertEqual(run.frame, CONTRACT['expected']['final_world'])
        self.assertFalse(match.combat.rockets)
        self.assertFalse(match.combat.blizzard.flights)
        self.assertNotIn(shot, match.combat.projectiles.shots,
                         f'physical flight ended but Shot retained at world {run.frame}; '
                         f'release_at={shot.release_at}')
        self.assertFalse(match.combat.projectiles.observed)
        self.assertIsNotNone(retired_at)
        self.assertLessEqual(retired_at, shot.release_at + projectiles.DELIVERY_GRACE_TICKS + 1)
        self.assertEqual(shot.clock_origin, (*birth, 16))
        owner_records = peers[0].records
        lease = next(iter(shot.created))
        if not replace:
            self.assertEqual(lease, match.combat.projectiles.lease_id(owner, shot.definition, shot.frame))
        ops = [r.op for r in owner_records if r.entity == lease]
        logical = [op for i, op in enumerate(ops) if i == 0 or op != ops[i - 1]]
        self.assertEqual(logical, [OP_CREATE, OP_DESTROY])
        copies = [r for r in peers[1].records
                  if projectiles.SEEN_FIRST <= r.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
        self.assertEqual([r.op for r in copies], [OP_CREATE, OP_DESTROY])
        self.assertTrue(any(r.entity == shot.body and r.ch2 is not None for r in peers[1].records))
        owner_release = next(r for r in owner_records if r.entity == lease and r.op == OP_DESTROY)
        release_frames = []
        for record in (owner_release, copies[-1]):
            if not replace:
                self.assertIsNotNone(record.ch2)
            if record.ch2 is not None:
                bits = BitReader(record.ch2.getvalue())
                self.assertEqual((bits.bit(), bits.bit()), (0, 1))
                baseline = bits.var_b()
                self.assertEqual(baseline, projectiles.NO_BASELINE)
                release_frame = (baseline + bits.var_b()) & 0xFFFFFFFF
                self.assertGreater(release_frame, shot.frame)
                release_frames.append(release_frame)
        for raw in peers[0].late:
            server._datagram(raw, peers[0].client.address, now + .01)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        for entity in shot.created:
            self.assertFalse(peers[0].client._entity_lifecycles[entity][1])
            self.assertTrue(any(r.entity == entity and r.op == OP_DESTROY for r in owner_records))
        self.assertFalse(peers[1].client._entity_lifecycles[copies[-1].entity][1])
        if button == 2 and not miss:
            self.assertEqual(match.combat.vitals_of(match.combat.player_target(enemy)).total, 125.)
            hits = {seq: value for seq, msg, value in peers[0].notices if msg == combat.HIT}.values()
            self.assertEqual([hit['+0x78']['+0x4'] for hit in hits], [75])
        self.evidence = {'button': button, 'miss': miss, 'replace': replace,
                         'captured_clock': shot.clock_origin, 'owner_event': shot.frame,
                         'world': run.frame, 'physical_end_at': physical_end_at,
                         'shot_release_at': shot.release_at, 'shot_retired_at': retired_at,
                         'owner_aliases': len(shot.created), 'native_release_owner_frames': release_frames,
                         'pending_shots': len(match.combat.projectiles.shots),
                         'pending_proxies': len(match.combat.projectiles.observed),
                         'late_acks_replayed': len(peers[0].late)}
        return shot, run

    def test_icicle_actual_hit_and_late_ack_retire_both_encrypted_copies(self):
        self.exercise()

    def test_icicle_range_expiry_and_late_ack_retire_both_encrypted_copies(self):
        self.exercise(miss=True)

    def test_blizzard_actual_landing_and_late_ack_retire_both_encrypted_copies(self):
        self.exercise(button=32)

    def test_replacement_keeps_old_shot_clock_and_late_ack_cannot_resurrect_it(self):
        self.exercise(replace=True)
