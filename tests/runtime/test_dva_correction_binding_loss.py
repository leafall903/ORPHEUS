"""Local ch3 needs the same actual44 prefix as the separately gated owner H1."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dva_movement_correction as movement
import test_game_packet_budget as budget
import test_game_repeated_lifecycles as wire

from ow174.game import body_entities
from ow174.game.commands import Command


class DvaCorrectionBindingLossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        movement.DvaMovementCorrectionTests.setUpClass()

    def check_loss(self, returning):
        contract = json.loads((Path(__file__).resolve().parents[2] /
                               'fixtures/behavior/dva-correction-binding-loss-v1.json').read_text())
        client, pilot, mech = movement.DvaMovementCorrectionTests().scene()
        # Explicit fixture boundary: the prior body/controller creates arrived.
        # The new44 below goes through real encrypted send/loss/retry processing.
        client._own_controller_acked = True
        client._ack_entity_create(pilot.body)
        if returning:
            body_entities.release_control(pilot)
            target, mover = pilot.body, pilot.mover
        else:
            body_entities._publish_possession(pilot)
            target, mover = mech.entity, mech.control_mover
        binding = client._pending_possessions[pilot.entity][1]
        client.entities = [binding]
        peer = budget.LinkCipher(budget.KEY_OUT, budget.KEY_IN)

        def command(frame):
            pilot.match.update(frame * .016, frame)
            pilot.take_commands([Command(frame, forward=127)])

        def send(frame):
            with patch.object(pilot.match, 'sent_updates', wraps=pilot.match.sent_updates) as committed:
                client.send_frame(frame, frame * .016)
            packet = budget.decoded(self, peer, client.server.sock.sent[-1][0])[0]
            selected = committed.call_args.args[1]
            corrections = [u for u in selected if u.correction is not None]
            # Independently consume the genuine world packet, including ch3.
            records = wire.wire_frame(packet)[1]
            plain_target = [u for u, fields in records
                            if u.entity == target and not u.op and u.chunk is None and not fields]
            self.assertEqual(len(plain_target), len(corrections))
            return packet, selected, corrections

        command(1071)
        lost, first, corrections = send(1071)
        self.assertIn(binding, first)
        self.assertEqual([u.entity for u in corrections], [target])
        self.assertLess(first.index(binding), first.index(corrections[0]))
        first_corrected = mover.corrected
        self.assertEqual(first_corrected, 1071)
        self.assertNotIn(binding, client.entities)
        # Drop the entire new44+ch3 packet; never report a positive ACK.
        command(1072)
        _, held, corrections = send(1072)
        self.assertNotIn(binding, held)
        self.assertEqual(corrections, [], contract['expected']['before_binding'])
        self.assertEqual(mover.corrected, first_corrected)
        client.acked((lost.seq + 1) & 0xFFFFFFFF, 0)
        self.assertIn(binding, client.entities)
        command(1073)
        count = len(client.server.sock.sent)
        client.server.sock.fail_next = True
        client.send_frame(1073, 1073 * .016)
        self.assertEqual(len(client.server.sock.sent), count)
        self.assertEqual(mover.corrected, first_corrected)
        retry, selected, corrections = send(1073)
        self.assertEqual([u.entity for u in corrections], [target])
        self.assertLess(selected.index(binding), selected.index(corrections[0]))
        self.assertEqual(mover.corrected, 1073)
        client.acked((retry.seq + 1) & 0xFFFFFFFF, 1)
        self.assertNotIn(pilot.entity, client._pending_possessions)
        command(1074)
        _, selected, corrections = send(1074)
        self.assertNotIn(binding, selected)
        self.assertEqual([u.entity for u in corrections], [target])
        self.assertEqual(mover.corrected, 1074)

    def test_lost_mech_binding_holds_ch3_without_committing_a_send(self):
        self.check_loss(False)

    def test_lost_return_binding_holds_pilot_ch3_without_committing_a_send(self):
        self.check_loss(True)


if __name__ == '__main__':
    unittest.main()
