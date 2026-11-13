"""A new0407 H1 must not bypass its still-in-flight component44 binding."""

import unittest

import test_dva_owner_projection as owners
from test_game_packet_budget import KEY_IN, KEY_OUT, LinkCipher, decoded
from test_game_repeated_lifecycles import wire_frame

from ow174.game import body_entities, dva_owner
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import decode


class DvaOwnerPossessionLossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owners.DvaOwnerProjectionTests.setUpClass()

    def test_lost_binding_blocks_new_mech_owner_until_binding_retry(self):
        client, pilot, mech, delivery, initial, known = owners.DvaOwnerProjectionTests().scene()
        stream = delivery.stream
        client._own_controller_acked = True
        client._ack_entity_create(pilot.body)
        pilot.body_script.create_arrived = True  # prior pilot create/full already received
        peer = LinkCipher(KEY_OUT, KEY_IN)
        # Send H0 first but delay its ACK until after the binding packet is sent.
        client.entities = [initial]
        client.send_frame(1001, 10.)
        baseline = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        self.assertIsNone(stream.base)
        body_entities._publish_possession(pilot)
        binding = client._pending_possessions[pilot.entity][1]
        client.send_frame(1002, 10.016)
        lost = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        refs = [fields[44] for _, fields in wire_frame(lost)[1] if 44 in fields]
        self.assertEqual(refs, [{0: mech.entity, 1: mech.entity, 2: pilot.body}])
        self.assertNotIn(binding, client.entities)
        # Receive only the older H0 ACK; the client has never received new44.
        client.acked((baseline.seq+1) & 0xFFFFFFFF, 1)
        self.assertIsNotNone(stream.base)
        dva_owner.poll(client)
        client.send_frame(1003, 10.032)
        before_binding = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        premature = []
        for update, _ in wire_frame(before_binding)[1]:
            if update.entity == mech.entity and update.chunk is not None:
                frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                if frame.owner:
                    premature.append(frame.cmfd)
        self.assertEqual(premature, [], 'Mech H1 arrived before the lost44 ever established native43 owner')
        # Report binding loss. Its retry must precede the held owner record.
        client.acked((lost.seq+1) & 0xFFFFFFFF, 0)
        sent_count = len(client.server.sock.sent)
        client.server.sock.fail_next = True
        client.send_frame(1004, 10.048)
        self.assertEqual(len(client.server.sock.sent), sent_count)
        self.assertIn(binding, client.entities)
        self.assertIs(client._pending_possessions[pilot.entity][1], binding)
        client.send_frame(1004, 10.048)
        retry = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        order = []
        for update, fields in wire_frame(retry)[1]:
            if 44 in fields:
                order.append('binding')
            if update.entity == mech.entity and update.chunk is not None:
                frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                if frame.owner:
                    order.append('owner')
        self.assertEqual(order, ['binding', 'owner'])
        self.assertIs(client._pending_possessions[pilot.entity][1], binding)
        # Losing this same binding+owner retry must retain both identities/order.
        client.acked((retry.seq+1) & 0xFFFFFFFF, 0)
        client.send_frame(1005, 10.064)
        repeated = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        repeated_order = []
        for update, fields in wire_frame(repeated)[1]:
            if 44 in fields:
                self.assertEqual(fields[44], refs[0])
                repeated_order.append('binding')
            if update.entity == mech.entity and update.chunk is not None:
                frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                if frame.owner:
                    self.assertEqual(frame.cmfd, 1000)
                    repeated_order.append('owner')
        self.assertEqual(repeated_order, ['binding', 'owner'])
        client.acked((repeated.seq+1) & 0xFFFFFFFF, 1)
        self.assertNotIn(pilot.entity, client._pending_possessions)
        acknowledged = stream.base_last
        client.acked((repeated.seq+1) & 0xFFFFFFFF, 1)
        self.assertEqual(stream.base_last, acknowledged)

    def test_old_mech_binding_ack_cannot_release_new_pilot_owner(self):
        client, pilot, _mech, _delivery, initial, _ = owners.DvaOwnerProjectionTests().scene()
        client._own_controller_acked = True
        client._ack_entity_create(pilot.body)
        pilot.body_script.create_arrived = True
        peer = LinkCipher(KEY_OUT, KEY_IN)
        client.entities = [initial]
        client.send_frame(1001, 10.)
        first = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        client.acked((first.seq+1) & 0xFFFFFFFF, 1)
        # Pilot H0 is still pending when both later binding packets are sent.
        pilot.match._body_frames(pilot, client)
        pilot_full = pilot.body_script.stream.full_update
        full = decode.read_chunk(pilot_full.chunk.getvalue(), pilot_full.chunk.count)
        known = {number: item.graph for number, item in full.instances.items()}
        client.send_frame(1002, 10.016)
        pilot_baseline = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        body_entities._publish_possession(pilot)
        old_binding = client._pending_possessions[pilot.entity][1]
        client.send_frame(1003, 10.032)
        old_packet = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        self.assertEqual(old_packet.seq, (pilot_baseline.seq+1) & 0xFFFFFFFF)
        body_entities.release_control(pilot)
        new_binding = client._pending_possessions[pilot.entity][1]
        self.assertIsNot(new_binding, old_binding)
        self.assertFalse(old_binding.resend)
        client.send_frame(1004, 10.048)
        new_packet = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        self.assertTrue(any(fields.get(44) == {0: pilot.body, 1: pilot.body, 2: pilot.body}
                            for _, fields in wire_frame(new_packet)[1]))
        # Receive the old bind ACK together with the older pilot H0 ACK.
        # The new pilot binding packet remains unreceived and in flight.
        client.acked((old_packet.seq+1) & 0xFFFFFFFF, 3)
        self.assertIs(client._pending_possessions[pilot.entity][1], new_binding)
        self.assertIsNotNone(pilot.body_script.stream.base)
        pilot.match._body_frames(pilot, client)
        client.send_frame(1005, 10.064)
        held = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        for update, _ in wire_frame(held)[1]:
            if update.entity == pilot.body and update.chunk is not None:
                frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                self.assertFalse(frame.owner)
        self.assertIs(client._pending_possessions[pilot.entity][1], new_binding)

    def test_hero_switch_retires_old_binding_without_blocking_soldier_owner(self):
        client, pilot, mech, _, initial, _ = owners.DvaOwnerProjectionTests().scene()
        client._own_controller_acked = True
        client._ack_entity_create(pilot.body)
        pilot.body_script.create_arrived = True
        peer = LinkCipher(KEY_OUT, KEY_IN)
        client.entities = [initial]
        client.send_frame(1001, 10.)
        first = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        client.acked((first.seq+1) & 0xFFFFFFFF, 1)
        body_entities._publish_possession(pilot)
        old_binding = client._pending_possessions[pilot.entity][1]
        old_body = pilot.body
        client.send_frame(1002, 10.016)
        old_packet = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        self.assertTrue(pilot.match.switch_hero(pilot, find_hero('soldier')))
        self.assertNotEqual(pilot.body, old_body)
        self.assertIsNone(pilot.controlled_mech)
        self.assertNotIn(mech, pilot.match.bots)
        new_binding = client._pending_possessions[pilot.entity][1]
        self.assertFalse(old_binding.resend)
        client.acked((old_packet.seq+1) & 0xFFFFFFFF, 1)
        self.assertIs(client._pending_possessions[pilot.entity][1], new_binding)
        model, target, seen_binding, seen_owner = decode.ClientBody(), mech.entity, False, False
        for frame in range(1003, 1011):
            pilot.match.update(frame*.016, frame)
            pilot.take_commands([Command(frame)])
            pilot.match._body_frames(pilot, client)
            client.send_frame(frame, frame*.016)
            packet = decoded(self, peer, client.server.sock.sent[-1][0])[0]
            for update, fields in wire_frame(packet)[1]:
                if 44 in fields:
                    self.assertEqual(fields[44], {0: pilot.body, 1: pilot.body, 2: pilot.body})
                    target, seen_binding = pilot.body, True
                if update.entity == pilot.body and update.chunk is not None:
                    state = model.apply(update)
                    if state.owner:
                        self.assertEqual(target, pilot.body)
                        seen_owner = True
            client.acked((packet.seq+1) & 0xFFFFFFFF, 1)
            if seen_binding and seen_owner and pilot.entity not in client._pending_possessions:
                break
        self.assertTrue(seen_binding)
        self.assertTrue(seen_owner)
        self.assertNotIn(pilot.entity, client._pending_possessions)


if __name__ == '__main__':
    unittest.main()
