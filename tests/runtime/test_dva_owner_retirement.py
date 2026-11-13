"""Actual encrypted old-owner retry after an acknowledged DVa control return."""
import json
import unittest
from pathlib import Path

import test_dva_owner_projection as owners
from test_game_packet_budget import KEY_IN, KEY_OUT, LinkCipher, decoded
from test_game_repeated_lifecycles import wire_frame

from ow174.game import body_entities, dva_owner
from ow174.game.script import decode


class OwnerRetirementLoss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owners.DvaOwnerProjectionTests.setUpClass()
        cls.contract = json.loads((Path(__file__).resolve().parents[2] /
                                   'fixtures/behavior/dva-owner-retirement-v1.json').read_text())

    def test_lost_mech_owner_does_not_retry_after_pilot_possession_ack(self):
        client, pilot, mech, delivery, initial, known = owners.DvaOwnerProjectionTests().scene()
        stream = delivery.stream
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        owner = next(u for u in client.entities if u.stream is stream and u.chunk_last == stream.last)
        client.entities = [owner]
        client._own_controller_acked = True
        client._combat_notices[pilot.body] = None
        pilot.body_script.create_arrived = True
        peer = LinkCipher(KEY_OUT, KEY_IN)
        client.send_frame(1001, 10.)
        lost = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        self.assertTrue(any(row.entity == mech.entity and row.chunk is not None
                            for row, _ in wire_frame(lost)[1]))
        body_entities.release_control(pilot)
        dva_owner.poll(client)
        client.send_frame(1002, 10.016)
        returned = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        refs = [fields[44] for _, fields in wire_frame(returned)[1] if 44 in fields]
        self.assertEqual(refs, [{0: pilot.body, 1: pilot.body, 2: pilot.body}])
        # ACK the pilot handoff, explicitly report the preceding owner packet lost.
        client.acked((returned.seq + 1) & 0xFFFFFFFF, 1)
        dva_owner.poll(client)
        client.send_frame(1003, 10.032)
        after = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        stale = []
        for row, _ in wire_frame(after)[1]:
            if row.entity == mech.entity and row.chunk is not None:
                frame = decode.read_chunk(row.chunk.getvalue(), row.chunk.count, known)
                if frame.owner:
                    stale.append((frame.last, frame.cmfd))
        self.assertEqual(stale, [], "H1 retried after native44 has cleared old-mech43 owner")

    def test_reparking_cancels_unacknowledged_pilot_owner_and_retries_plain_reset(self):
        client, pilot, mech, *_ = owners.DvaOwnerProjectionTests().scene()
        script = pilot.body_script
        script.create_arrived = True
        body_entities.release_control(pilot)
        pilot.match._body_frames(pilot, client)
        baseline = script.stream.full_update
        initial = decode.read_chunk(baseline.chunk.getvalue(), baseline.chunk.count)
        known = {number: item.graph for number, item in initial.instances.items()}
        script.stream.arrived(script.stream.last)
        pilot.match._body_frames(pilot, client)
        owner = next(u for u in client.entities
                     if u.stream is script.stream and u.chunk_last == script.stream.last)
        self.assertTrue(decode.read_chunk(owner.chunk.getvalue(), owner.chunk.count,
                                        known).owner)
        client.entities = [owner]
        client._own_controller_acked = True
        client._combat_notices[pilot.body] = None
        peer = LinkCipher(KEY_OUT, KEY_IN)
        client.send_frame(1001, 10.)
        sent = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        body_entities.begin_initial_control(pilot)
        self.assertIs(pilot.controlled_mech, mech)
        pilot.match._body_frames(pilot, client)
        reset = script.stream.full_update
        self.assertFalse(owner.resend)
        self.assertNotIn(owner, client.entities)
        self.assertEqual(script.stream.cmfd, self.contract['expected']['parked_cmfd'])
        self.assertFalse(decode.read_chunk(reset.chunk.getvalue(), reset.chunk.count).owner)
        # An old positive ACK can still arrive before the new H0 reset ACK.
        client.acked((sent.seq + 1) & 0xFFFFFFFF, 1)
        self.assertIn(reset, client.entities)
        client.send_frame(1002, 10.016)
        dropped_reset = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        client.acked((dropped_reset.seq + 1) & 0xFFFFFFFF, 0)
        client.send_frame(1003, 10.032)
        retry = decoded(self, peer, client.server.sock.sent[-1][0])[0]
        chunks = [row for row, _ in wire_frame(retry)[1]
                  if row.entity == pilot.body and row.chunk is not None]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk.getvalue(), reset.chunk.getvalue())
        self.assertFalse(decode.read_chunk(chunks[0].chunk.getvalue(), chunks[0].chunk.count).owner)
        client.acked((retry.seq + 1) & 0xFFFFFFFF, 1)
        self.assertEqual(script.stream.base_last, reset.chunk_last)
        script.stream.arrived(owner.chunk_last)  # late old ACK cannot roll back the full reset
        self.assertEqual(script.stream.base_last, reset.chunk_last)


if __name__ == '__main__':
    unittest.main()
