"""Actual Echo input, owner ACK and observer retirement through encrypted peers."""
import unittest
from types import SimpleNamespace

import test_echo_combat as echo_cases
from test_game_packet_budget import decoded
from test_game_projectiles import read_shot
from test_server_journal import Peer

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import LinkCipher
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket


class EchoOwnerClockTests(unittest.TestCase):
    def test_separate_owner_epoch_completes_projectile_and_proxy_lifetimes(self):
        for button, distance, expected_damage in ((1, 95, 0), (2, 5, 180)):
            with self.subTest(button=button):
                self.exercise(button, distance, expected_damage)

    def test_late_confirmation_ack_after_physical_expiry_cleans_all_three_pellets(self):
        self.exercise(1, 95, 0, confirmation_delay=100)

    def test_missing_confirmation_ack_retires_at_the_grace_deadline(self):
        self.exercise(1, 95, 0, confirmation_delay=350)

    def test_ack_after_retirement_does_not_recreate_owner_or_observer_proxies(self):
        self.exercise(1, 95, 0, confirmation_delay=200)

    def test_body_replacement_keeps_the_old_shot_epoch(self):
        run, shooter, _, _, angles = echo_cases.EchoPrimaryTests().session()
        run.step({shooter: (*angles, 1)})
        shots = [rocket.shot for rocket in run.match.combat.rockets]
        old_birth = (*shooter.body_script.echo_birth, shooter.body_script.frame_ms)
        self.assertTrue(run.match.switch_hero(
            shooter, shooter.hero, preserve_pose=True, start_frame=12000,
        ))
        self.assertNotEqual(shooter.body_script.echo_birth, old_birth[:2])
        for shot in shots:
            self.assertEqual(shot.clock_origin, old_birth)
            for delivery in shot.observers:
                self.assertEqual(delivery.clock_origin, old_birth)
        run.match.combat.projectiles.update(run.frame + 1)
        self.assertFalse(run.match.combat.projectiles.shots)

    def exercise(self, button, distance, expected_damage, confirmation_delay=0):
        run, shooter, victim, target, _ = echo_cases.EchoPrimaryTests().session(distance=distance)
        match = run.match
        self.assertTrue(match.switch_hero(shooter, shooter.hero, preserve_pose=True, start_frame=9000))
        shooter.commands.reset(last_frame=9000)
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            shooter.take_commands([Command(9000 + offset)])
            victim.take_commands([Command(run.frame)])
        before = match.combat.vitals_of(target).total
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        adapter = SimpleNamespace(datagram=server._datagram)
        peers, clients, records = [], [], [[], []]
        for index, player in enumerate(run.players):
            incoming, outgoing = bytes([index + 1]) * 32, bytes([index + 11]) * 32
            client = Client(server, ("memory.test", 41000 + index), index + 1,
                            LinkCipher(incoming, outgoing), player)
            server.clients[index + 1] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            handoff = SimpleNamespace(conn=index + 1, key_out=outgoing, key_in=incoming)
            peers.append(Peer(adapter, handoff, server.sock.sent, client.address))
            clients.append(client)
        shots = {}
        confirmation_at = None
        for step in range(230):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for index, (client, peer) in enumerate(zip(clients, peers, strict=True)):
                client.send_frame(run.frame, now)
                packet, *_ = decoded(self, peer.cipher, server.sock.sent[-1][0])
                current = client.in_flight.get(packet.seq, (None, []))[1]
                records[index].extend(current)
                if index == 0 and confirmation_at is None and any(
                        row.op == OP_CREATE and any(row.entity in shot.created for shot in shots.values())
                        for row in current):
                    confirmation_at = step
                blocked = (index == 0 and confirmation_at is not None
                           and step < confirmation_at + confirmation_delay)
                peer.drain(lose=index == 0 and (step < 20 or blocked))
            peers[0].commands(9021 + step, 1, button if step == 0 else 0, now=now)
            peers[1].commands(run.frame, 1, 0, now=now)
            for rocket in match.combat.rockets:
                if rocket.shot is not None:
                    shots[id(rocket.shot)] = rocket.shot
        self.assertEqual(len(shots), 3 if button == 1 else 6)
        self.assertFalse(match.combat.rockets)
        self.assertFalse(match.combat.projectiles.shots,
                         "physical expiry left owner confirmations alive in the wrong clock")
        self.assertFalse(match.combat.projectiles.observed)
        self.assertEqual(before - match.combat.vitals_of(target).total, expected_damage)
        for shot in shots.values():
            self.assertTrue(shot.created)
            for entity in shot.created:
                ops = [row.op for row in records[0] if row.entity == entity]
                self.assertIn(OP_CREATE, ops)
                self.assertIn(OP_DESTROY, ops)
                destroy = next(row for row in records[0]
                               if row.entity == entity and row.op == OP_DESTROY)
                if confirmation_delay >= 200:
                    self.assertIsNone(destroy.ch2)
                else:
                    self.assert_release(destroy, shot.frame)
        notices = peers[0].hit_notices(0)
        self.assertEqual(len(notices), 0 if button == 1 else 12)
        self.assertEqual(sum(notice["+0x78"]["+0x4"] for notice in notices), expected_damage)
        ids = {entity for shot in shots.values() for delivery in shot.observers
               for entity in delivery.record.ids}
        launched = {}
        for row in records[1]:
            if row.entity != shooter.body or row.ch2 is None:
                continue
            bits = BitReader(row.ch2.getvalue())
            self.assertEqual((bits.bit(), bits.bit()), (1, 1))
            frame = bits.var_b()
            event = read_shot(bits)
            launched[(frame, event["shot"], tuple(event["ids"]))] = event
        self.assertEqual(len(launched), 1 if button == 1 else 6)
        self.assertEqual({entity for event in launched.values() for entity in event["ids"]}, ids)
        for shot in shots.values():
            self.assertEqual(shot.clock_origin, (1030, 9000, 16))
        for entity in ids:
            ops = [row.op for row in records[1] if row.entity == entity]
            self.assertIn(OP_CREATE, ops)
            self.assertIn(OP_DESTROY, ops)
            destroy = next(row for row in records[1] if row.entity == entity and row.op == OP_DESTROY)
            if confirmation_delay < 200 or destroy.ch2 is not None:
                self.assert_release(destroy, min(shot.frame for shot in shots.values()))

    def assert_release(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        bits = BitReader(record.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline = bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + bits.var_b()) & 0xffffffff, owner_frame)
