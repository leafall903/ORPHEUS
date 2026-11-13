"""Actual encrypted Client delivery; this is protocol evidence, not native rendering."""

import json
import unittest
from pathlib import Path

import test_game_moira_orbs as scenes

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class MoiraEncryptedOrbLifecycleTests(unittest.TestCase):
    contract = json.loads((Path(__file__).resolve().parents[2]
                           / "fixtures/behavior/moira-encrypted-orb-lifecycle-v1.json").read_text())

    def test_both_original_orbs_expire_and_release_in_captured_owner_epoch(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                self.exercise(healing=healing)

    def test_create_ack_after_physical_expiry_leaves_no_owner_or_observer_proxy(self):
        for delay in self.contract['input']['owner_ack_delays']:
            with self.subTest(ack_delay=delay):
                self.exercise(owner_ack_delay=delay)

    def test_body_replacement_retains_old_clock_and_retires_delivered_proxies(self):
        self.exercise(replace_body=True)

    def exercise(self, *, healing=False, owner_ack_delay=0, replace_body=False):
        run, caster, target, _ = scenes.MoiraOrbTests().setup_fight(ally=healing, distance=1000)
        match = run.match
        origin = self.contract['input']['owner_origin']
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=origin))
        caster.commands.reset(last_frame=origin)
        for at in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(origin + at)])
            target.owner.take_commands([Command(run.frame)])
        origin += 20
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 41000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), []))
        old_clock = (*caster.body_script.moira_birth, caster.body_script.frame_ms)
        self.assertEqual(old_clock, tuple(self.contract["expected"]["captured_clock"]))
        ended_at = None
        orb = shot = None
        for step in range(self.contract['input']['frames']):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            if orb is not None and orb not in match.combat.moira_orbs and ended_at is None:
                ended_at = step
            for client, cipher, window, records in peers:
                client.send_frame(run.frame, now)
                packet, *_ = decoded(self, cipher, server.sock.sent[-1][0])
                records.extend(client.in_flight.get(packet.seq, (None, []))[1])
                self.assertTrue(window.accept(packet.seq))
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                if client.player is not caster or step >= owner_ack_delay:
                    server._datagram(cipher.seal(ack), client.address, now)
            caster.take_commands([Command(origin + step + 1,
                                          buttons=16 if step == 0 else
                                          (1 if healing else 2) if step < 8 else 0)])
            target.owner.take_commands([Command(run.frame)])
            if orb is None and match.combat.moira_orbs:
                orb = match.combat.moira_orbs[0]
                shot = orb.shot
            if replace_body and step == 20:
                self.assertIsNotNone(shot)
                self.assertTrue(match.switch_hero(
                    caster, caster.hero, preserve_pose=True, start_frame=20000))
                caster.commands.reset(last_frame=20000)
                origin = 20000 - step - 1
                self.assertNotEqual((*caster.body_script.moira_birth, 16), old_clock)
        self.assertIsNotNone(shot)
        self.assertEqual(shot.clock_origin, old_clock)
        self.assertNotIn(orb, match.combat.moira_orbs)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        self.assertEqual({d.viewer for d in shot.observers}, set(run.players))
        for client, _, _, records in peers:
            delivery = next(d for d in shot.observers if d.viewer is client.player)
            expired_before_ack = (owner_ack_delay > ended_at + projectiles.DELIVERY_GRACE_TICKS
                                  and client.player is caster)
            self.assertEqual(delivery.creation.received, not expired_before_ack)
            self.assertEqual(delivery.launch_arrival.received, not expired_before_ack)
            if expired_before_ack:
                self.assertFalse(delivery.launch_queued)
            self.assertTrue(delivery.finished or replace_body)
            self.assertEqual(delivery.clock_origin, old_clock)
            proxy = delivery.record.ids[0]
            self.assertFalse(client._entity_lifecycles[proxy][1],
                             "late ACK must not resurrect a retired proxy generation")
            selected = [record for record in records if record.entity == proxy]
            ops = [record.op for record in selected]
            logical = [op for at, op in enumerate(ops) if at == 0 or op != ops[at - 1]]
            self.assertEqual(logical, [OP_CREATE, OP_DESTROY])
            release = next(record for record in selected if record.op == OP_DESTROY)
            if replace_body or expired_before_ack:
                self.assertIsNone(release.ch2)
                continue
            self.assertIsNotNone(release.ch2)
            bits = BitReader(release.ch2.getvalue())
            self.assertEqual((bits.bit(), bits.bit()), (0, 1))
            baseline = bits.var_b()
            self.assertEqual(baseline, projectiles.NO_BASELINE)
            native_frame = (baseline + bits.var_b()) & 0xFFFFFFFF
            self.assertGreater(native_frame, shot.frame,
                               'native release must name the captured owner epoch')
