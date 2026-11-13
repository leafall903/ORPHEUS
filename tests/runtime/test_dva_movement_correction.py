"""Owned0407 ch3 must correct the command it describes, not an NPC world tick."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dva_owner_projection as owner_tests
import test_game_packet_budget as budget
import test_game_repeated_lifecycles as wire
from test_game_correction import read

from ow174.game import body_entities, correction
from ow174.game.commands import JUMP, Command
from ow174.game.content import find_hero
from ow174.game.server import Client


class DvaMovementCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owner_tests.DvaOwnerProjectionTests.setUpClass()
        cls.contract = json.loads((Path(__file__).resolve().parents[2]
                                  / "fixtures/behavior/dva-controlled-movement-v1.json").read_text())

    def scene(self):
        client, pilot, mech, *_ = owner_tests.DvaOwnerProjectionTests().scene()
        pilot.body_script.create_arrived = True
        match = pilot.match
        for frame in range(1001, 1071):
            match.update(frame * .016, frame)
            pilot.take_commands([Command(frame)])
        return client, pilot, mech

    def commands(self, pilot):
        pilot.take_commands([Command(frame, forward=127, buttons=JUMP if frame == 1071 else 0)
                             for frame in range(1071, 1079)])
        pilot.match.tick = 1075  # client commands can lead the outgoing world packet

    def test_owned_mech_gets_one_exact_command_snapshot_and_bounds_ack(self):
        _, pilot, mech = self.scene()
        self.commands(pilot)
        mover = mech.control_mover
        snapshot = next(s for s in mover.history if s.frame == 1075)
        records = [u for u in pilot.match.remote_movements(pilot) if u.entity == mech.entity]
        self.assertEqual(len(records), 1)
        update = records[0]
        self.assertIsNotNone(update.correction)
        wire = read(update.movement, 1075)
        self.assertEqual(wire["frame"], self.contract["expected"]["state_frame"])
        self.assertEqual(wire["position"], snapshot.position)
        self.assertEqual(wire["flags"], snapshot.flags)
        self.assertEqual(wire["throttles"], snapshot.throttles)
        self.assertEqual(wire["throttles"], tuple(self.contract["expected"]["throttles"]))
        self.assertEqual(wire["spring"], snapshot.spring)
        self.assertEqual(correction.command_ack(pilot, 1075, [update]),
                         self.contract["expected"]["command_ack"])
        self.assertIsNone(mover.corrected)  # selecting/budgeting does not commit a send
        pilot.match.sent_updates(pilot, [update])
        self.assertEqual(mover.corrected, 1075)
        self.assertIsNone(pilot.mover.corrected)
        self.assertEqual(correction.command_ack(pilot, 1075), 1077)
        self.assertFalse(any(u.entity == mech.entity for u in pilot.match.remote_movements(pilot)))

    def test_mech_create_and_pilot_create_ack_gate_the_owner_record(self):
        client, pilot, _mech = self.scene()
        self.commands(pilot)
        pilot.body_script.create_arrived = False
        self.assertIsNone(correction.own_record(pilot, 1075))
        pilot.body_script.create_arrived = True
        with patch.object(client, "bot_create_acked", return_value=False):
            self.assertIsNone(correction.own_record(pilot, 1075))
        with patch.object(correction, "CORRECTIONS", False):
            self.assertIsNone(correction.own_record(pilot, 1075))
        self.assertIsNotNone(correction.own_record(pilot, 1075))

    def test_release_rebind_retires_old_mech_tokens_and_restores_pilot_target(self):
        _, pilot, mech = self.scene()
        self.commands(pilot)
        update = correction.own_record(pilot, 1075)
        self.assertIsNotNone(update)
        old = mech.control_mover
        body_entities.release_control(pilot)
        correction.sent(pilot, update)
        self.assertIsNone(old.corrected)
        pilot.take_commands([Command(1079, forward=127)])
        current = correction.own_record(pilot, 1079)
        self.assertIsNotNone(current)
        self.assertEqual(current.entity, pilot.body)
        self.assertIs(current.correction.mover, pilot.mover)
        body_entities.begin_initial_control(pilot)
        self.assertIs(pilot.controlled_mech, mech)
        self.assertIsNot(mech.control_mover, old)
        correction.sent(pilot, update)
        self.assertIsNone(mech.control_mover.corrected)

    def test_dead_or_detached_current_mech_does_not_correct_the_parked_pilot(self):
        _, pilot, mech = self.scene()
        self.commands(pilot)
        for field in ("dead", "detached_bomb"):
            with self.subTest(field=field), patch.object(mech, field, True):
                self.assertIsNone(correction.own_record(pilot, 1075))

    def test_failed_send_does_not_commit_and_lost_packet_keeps_next_command_record(self):
        client, pilot, mech = self.scene()
        self.commands(pilot)
        client.entities = []
        client._own_controller_acked = True
        peer = budget.LinkCipher(budget.KEY_OUT, budget.KEY_IN)
        client.server.sock.fail_next = True
        client.send_frame(1075, 10.)
        self.assertIsNone(mech.control_mover.corrected)
        client.send_frame(1075, 10.)
        packet = budget.decoded(self, peer, client.server.sock.sent[-1][0])[0]
        # Inspect this genuine encrypted datagram, but deliberately send no ACK.
        from ow174.game import messages
        from ow174.game.bits import BitReader

        reader = BitReader(packet.payload)
        self.assertEqual(reader.var_a(), 1075)
        messages.read_channel(reader)
        self.assertEqual(reader.var_a(), 1077)
        self.assertEqual(mech.control_mover.corrected, 1075)
        records = wire.wire_frame(packet)[1]
        self.assertEqual(sum(u.entity == mech.entity for u, _ in records), 1)
        pilot.match.tick = 1076
        client.send_frame(1076, 10.)
        packet = budget.decoded(self, peer, client.server.sock.sent[-1][0])[0]
        self.assertEqual(sum(u.entity == mech.entity for u, _ in wire.wire_frame(packet)[1]), 1)
        self.assertEqual(mech.control_mover.corrected, 1076)
        self.assertIsNone(pilot.mover.corrected)

    def test_other_viewers_retain_the_existing_npc_record(self):
        client, pilot, mech = self.scene()
        self.commands(pilot)
        other = pilot.match.add_player(7, "Observer", find_hero("soldier"), 0, False)
        peer = Client(client.server, ("memory.test", 40007), 7,
                      budget.LinkCipher(budget.KEY_IN, budget.KEY_OUT), other)
        other.client = peer
        peer.queue_entities([pilot.match._bot_create_update(mech, other)])
        peer._ack_entity_create(mech.entity)
        pilot.match.bots_shown[other.slot] = 1000
        records = [u for u in pilot.match.remote_movements(other) if u.entity == mech.entity]
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].correction)
        self.assertIsNone(records[0].movement.frame)
        self.assertEqual(records[0].movement.throttles, mech.held)
