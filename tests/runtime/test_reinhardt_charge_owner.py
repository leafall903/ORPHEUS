"""Synthetic owner/observer delivery; original-client Charge acceptance is pending."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_delivered_reinhardt as delivered
import test_game_correction as correction_tests
import test_game_repeated_lifecycles as lifecycle_tests
import test_server_journal as journal_tests
from ow174.game import correction
from ow174.game.content import SOLDIER, heroes


class ChargeOwnerTests(unittest.TestCase):
    def test_encrypted_production_commands_emit_owner_charge_pose(self):
        fixture = journal_tests.ServerJournalTests()
        session, sent = fixture.make()
        links, players = fixture.connected(session, sent, two=False,
                                          first_hero=0x02E0000000000007)
        peer, = links
        player, = players
        now = 100.944
        movements = []
        native_read = lifecycle_tests.read_movement

        def record_movement(reader, tick):
            fields = native_read(reader, tick)
            movements.append(fields)
            return fields

        def step(button):
            nonlocal now
            now += .016
            peer.commands(player.body_script.frame + 1, 1, button, now=now)
            cursor = len(sent)
            session.tick(now)
            for data, address in sent[cursor:]:
                if address != peer.address:
                    continue
                packet = peer.cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind == journal_tests.KIND_FRAME:
                    with patch.object(lifecycle_tests, "read_movement", record_movement):
                        lifecycle_tests.wire_frame(packet)
            peer.drain()
            peer.send(now=now)

        for _ in range(30):
            step(0)
        self.assertTrue(player.body_script.create_arrived)
        start = tuple(player.mover.position)
        step(8)
        for _ in range(65):
            step(0)
        self.assertAlmostEqual(player.mover.position[2] - start[2], 7.3828125)
        self.assertFalse(player.body_script.world.velocity_requests)
        self.assertTrue(any(row["position"][2] - start[2] > 7 for row in movements),
                        "authenticated owner datagrams must contain the driven pose")

    def test_charge_pose_reaches_owner_and_observer_and_retires_with_body(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / "fixtures/behavior/reinhardt-charge-owner-v1.json").read_text())
        case = delivered.ChargeMovementTests()
        case.setUp()
        player, match = case.rein, case.match
        expected = fixture["expected"]
        self.assertEqual(case.run.frame, fixture["input"]["initial_frame"])
        self.assertEqual(list(player.mover.position), fixture["input"]["initial_position"])
        player.body_script.create_arrived = True
        self.assertIsNone(correction.own_record(player, match.tick))  # idle is unchanged
        for commands in fixture["input"]["commands"]:
            case.advance(**commands)
        self.assertEqual(case.run.frame, expected["frame"])
        self.assertEqual(list(player.mover.position), expected["position"])

        player.body_script.create_arrived = False
        self.assertEqual(correction.own_record(player, match.tick) is not None,
                         expected["record_before_create_ack"])
        player.body_script.create_arrived = fixture["input"]["body_create_acknowledged"]
        owner = [u for u in match.remote_movements(player)
                 if u.entity == player.body and u.movement is not None]
        observer = [u for u in match.remote_movements(case.enemy)
                    if u.entity == player.body and u.movement is not None]
        self.assertEqual(len(owner), expected["owner_movement_records"])
        self.assertEqual(len(observer), expected["observer_movement_records"])
        decoded = correction_tests.read(owner[0].movement, match.tick)
        self.assertEqual(decoded["frame"], expected["frame"])
        self.assertEqual(list(decoded["position"]), expected["position"])
        self.assertLessEqual(correction.command_ack(player, match.tick, updates=owner),
                             expected["frame"] + correction.ACK_LEAD)
        match.sent_updates(player, owner)
        self.assertIsNone(correction.own_record(player, match.tick))

        self.assertTrue(match.switch_hero(player, heroes()[SOLDIER]))
        self.assertEqual(player.impulse_correction,
                         expected["correction_window_after_body_replacement"])
