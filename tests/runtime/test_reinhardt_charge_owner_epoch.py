"""Charge owner delivery across different constructor clocks; no game client."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_repeated_lifecycles as lifecycle
import test_server_journal as journal

from ow174.game import world

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/reinhardt-charge-owner-epoch-v1.json"
EVIDENCE = {}


class ChargeOwnerEpochTests(unittest.TestCase):
    def test_actual_encrypted_charge_delivers_own_pose_in_recipient_epoch(self):
        contract = json.loads(FIXTURE.read_text())
        inputs, expected = contract["input"], contract["expected"]
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, two=False, first_hero=0x02E0000000000007)
        (peer,) = peers
        (owner,) = players
        with patch.object(owner.match, "skin_of", lambda account, hero: owner.skin):
            self.assertTrue(
                owner.match.switch_hero(
                    owner, owner.hero, preserve_pose=True, start_frame=inputs["owner_birth"]
                )
            )
        owner.commands.reset(last_frame=inputs["owner_birth"])
        epoch = owner.match.combat.knockbacks.body_epochs[owner.body][1]
        self.assertNotEqual(epoch[0], epoch[1])
        now = 100.944
        entity = [None]
        records = []
        native_update, native_read = lifecycle.EntityUpdate, lifecycle.read_movement

        def update(body):
            entity[0] = body
            return native_update(body)

        def movement(reader, tick):
            fields = native_read(reader, tick)
            if tick != world.CREATE_BASE and entity[0] == owner.body:
                records.append({"packet_world_tick": tick, **fields})
            return fields

        def step(button):
            nonlocal now
            now += 0.016
            peer.commands(owner.body_script.frame + 1, 1, button, now=now)
            cursor = len(sent)
            session.tick(now)
            for data, address in sent[cursor:]:
                if address != peer.address:
                    continue
                packet = peer.cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind == journal.KIND_FRAME:
                    with (
                        patch.object(lifecycle, "EntityUpdate", update),
                        patch.object(lifecycle, "read_movement", movement),
                    ):
                        lifecycle.wire_frame(packet)
            peer.drain()
            peer.send(now=now)

        for _ in range(inputs["warm_commands"]):
            step(0)
        self.assertTrue(owner.body_script.create_arrived)
        records.clear()
        start = tuple(owner.mover.position)
        step(inputs["charge_button"])
        for _ in range(inputs["neutral_commands_after_charge"]):
            step(0)
        displacement = owner.mover.position[2] - start[2]
        EVIDENCE.update(
            {
                "constructor_epoch": epoch,
                "server_displacement": displacement,
                "owner_records": len(records),
                "create_acked": owner.body_script.create_arrived,
                "owner_frames": [row["frame"] for row in records],
                "packet_world_ticks": [row["packet_world_tick"] for row in records],
                "finite_binding": getattr(owner, "motion_correction_target", None) is not None,
                "adopted_native": False,
            }
        )
        self.assertGreater(displacement, expected["minimum_server_displacement"])
        self.assertGreaterEqual(len(records), expected["minimum_owner_records"])
        self.assertTrue(all(row["frame"] > row["packet_world_tick"] for row in records))
        self.assertTrue(any(row["position"][2] - start[2] > 7 for row in records))
