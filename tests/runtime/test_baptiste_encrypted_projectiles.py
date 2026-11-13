"""Actual Baptiste throws through encrypted GameServer delivery, not native rendering."""

import unittest

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime import test_baptiste_projectile_delivery as scenes
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class BaptisteEncryptedProjectileTests(unittest.TestCase):
    def test_healing_contact_with_delayed_owner_ack_retires_all_copies(self):
        self.exercise(button=2, ack_delay=20)

    def test_late_confirmation_ack_does_not_resurrect_healing_projectile(self):
        self.exercise(button=2, ack_delay=95)

    def test_field_contact_retires_authoritative_owner_and_observer_copies(self):
        self.exercise(button=16, ack_delay=20)

    def test_body_replacement_preserves_old_shot_clock_and_retires_copies(self):
        self.exercise(button=2, ack_delay=20, replace_body=True)

    def exercise(self, *, button, ack_delay, replace_body=False):
        run, caster, ally, enemy, fight, targets = scenes.BaptisteProjectileDeliveryTests().arena(
            friendly_target=button == 2,
        )
        match = run.match
        fight.vitals_of(targets[ally]).damage(100)
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=9000))
        caster.commands.reset(last_frame=9000)
        birth = caster.body_script.world_frame_anchor
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(9000 + offset)])
            for player in (ally, enemy):
                player.take_commands([Command(run.frame)])
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
        shot = None
        rocket = None
        held_acks = []
        replaced = False
        owner_blocked = False
        for step in range(125):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records in peers:
                client.send_frame(run.frame, now)
                packet, _, _, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent_records = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent_records)
                self.assertTrue(window.accept(packet.seq))
                ack = cipher.seal(Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK))
                if (client.player is caster and shot is not None
                        and any(record.op == OP_CREATE and record.entity in shot.created
                                for record in sent_records)):
                    owner_blocked = True
                if client.player is caster and owner_blocked and step < ack_delay:
                    held_acks.append(ack)
                else:
                    server._datagram(ack, client.address, now)
            if step == ack_delay:
                # Replayed authenticated ACKs from before retirement cannot recreate a proxy.
                for ack in held_acks:
                    server._datagram(ack, caster.client.address, now)
                held_acks.clear()
            if replace_body and step == 8:
                self.assertIsNotNone(shot)
                self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True,
                                                  start_frame=12000))
                caster.commands.reset(last_frame=12000)
                replaced = True
            frame = 12000 + step - 8 if replaced else 9021 + step
            caster.take_commands([Command(frame, pitch=0 if button == 2 else 8192,
                                          buttons=button if step == 0 else 0)])
            for player in (ally, enemy):
                player.take_commands([Command(run.frame)])
            if shot is None and fight.rockets:
                rocket, = fight.rockets
                shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertNotIn(rocket, fight.rockets)
        self.assertNotIn(shot, fight.projectiles.shots,
                         f'physical flight ended but Shot remains: frame={shot.frame}, '
                         f'release_at={shot.release_at}, world={match.tick}')
        self.assertFalse(fight.projectiles.observed)
        self.assertEqual(shot.clock_origin, (*birth, 16))
        self.assertEqual(shot.frame, 9021)
        self.assertTrue(all(delivery.record.frame == shot.frame for delivery in shot.observers))
        if replaced:
            self.assertNotEqual(caster.body_script.world_frame_anchor, birth)
        if button == 2:
            self.assertEqual(fight.vitals_of(targets[ally]).total, 170)
            self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)
            self.assertTrue(shot.created)
        else:
            self.assertEqual(len(fight.baptiste.fields), 1)
        for delivery in shot.observers:
            self.assertTrue(delivery.creation.received)
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(delivery.finished)
            self.assert_copies(peers, delivery.viewer, {delivery.record.ids[0]}, shot.frame,
                               release=not replaced)
        if button == 2:
            self.assert_copies(peers, caster, set(shot.created), shot.frame,
                               release=ack_delay < 64 and not replaced)

    def assert_copies(self, peers, player, entities, owner_frame, *, release):
        records = next(peer[3] for peer in peers if peer[0].player is player)
        for entity in entities:
            copies = [record for record in records if record.entity == entity]
            ops = [record.op for record in copies]
            logical = [op for index, op in enumerate(ops) if index == 0 or op != ops[index - 1]]
            self.assertEqual(logical, [OP_CREATE, OP_DESTROY])
            destroy = next(record for record in copies if record.op == OP_DESTROY)
            if release:
                self.assertIsNotNone(destroy.ch2)
                bits = BitReader(destroy.ch2.getvalue())
                self.assertEqual((bits.bit(), bits.bit()), (0, 1))
                baseline = bits.var_b()
                self.assertEqual(baseline, projectiles.NO_BASELINE)
                self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, owner_frame)
            else:
                self.assertIsNone(destroy.ch2)
