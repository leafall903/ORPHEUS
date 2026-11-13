"""Original Orisa barrier inputs decoded from actual encrypted server frames."""

import json
import os
import struct
import unittest
from pathlib import Path

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as combat_fixture
import test_server_journal as journal_fixture

from ow174.game import combat, world
from ow174.game.bits import BitReader
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, expr


class OrisaBarrierWireContextTests(unittest.TestCase):
    def test_create_health_team_source_and_damage_update_reach_both_peers(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        contract = json.loads((root / "orisa-barrier-health-v1.json").read_text())
        initial_pool = (contract["pool_id"], contract["maximum"], contract["initial"],
                        contract["pool_type"])
        hit_pool = (contract["pool_id"], contract["maximum"], contract["after_one_hit"],
                    contract["pool_type"])
        fixture = journal_fixture.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=combat_fixture.ORISA)
        owner, enemy = players
        manager = owner.match.combat.orisa_barriers
        first_packet = len(sent)
        now = 100.95

        def step(owner_button=0, enemy_button=0):
            nonlocal now
            peers[0].commands(owner.body_script.frame + 1, 1, owner_button, now=now)
            peers[1].commands(enemy.body_script.frame + 1, 1, enemy_button, yaw=-32768, now=now)
            now += combat.TICK
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        for index in range(120):
            step(16 if index == 0 else 0)
            if manager.active:
                break
        shield, = manager.active.values()
        self.assertEqual(shield.health, contract["initial"])
        x, y, z = shield.position
        barrier_fixture.BarrierTests.place(owner, (x, y, z - 6))
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
        for _ in range(12):
            step()
        step(enemy_button=1)
        for _ in range(12):
            step()
        self.assertEqual(shield.health, contract["after_one_hit"])

        for peer in peers:
            model = decode.ClientBody((0x252, 0x25B4))  # original14A1 initial graphs
            creates = []
            updates = []
            look_health = set()
            for data, _ in sent[first_packet:]:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                self.assertIsNotNone(packet)
                if packet.kind != KIND_FRAME:
                    continue
                _, records = journal_fixture.wire_frame(packet)
                for record, fields in records:
                    if record.entity != shield.entity:
                        continue
                    if record.op == world.OP_CREATE:
                        creates.append(fields)
                    if 51 in fields:
                        updates.append(struct.unpack("<IffI4B", fields[51][0]))
                    if record.chunk is not None:
                        first = decode.w_u32(BitReader(record.chunk.getvalue()))
                        frame = decode.read_chunk(record.chunk.getvalue(), record.chunk.count,
                                                  model.graphs, records=model.records)
                        if frame.last > model.applied and first <= model.applied + 1:
                            model.apply(record)
                            if (0, contract["look_health_variable"]) in model.vars:
                                look_health.add(expr.to_float(model.vars[
                                    (0, contract["look_health_variable"])]))
            self.assertTrue(creates)
            self.assertEqual(model.instances[1], 0x252)
            self.assertEqual(model.instances[2], 0x25B4)
            self.assertGreater(shield.script.id, 2)
            for fields in creates:
                self.assertEqual(fields[26][0], owner.team_bit)
                pool = struct.unpack("<IffI4B", fields[51][0])
                self.assertEqual(pool[:4], initial_pool)
            self.assertTrue(any(pool[:4] == hit_pool for pool in updates))
            self.assertEqual(model.instances[shield.script.id], 0xBD8)
            self.assertEqual(model.vars[(shield.script.id, contract["source_variable"])],
                             expr.Entity(shield.source_body))
            self.assertEqual(model.vars[(shield.script.id, contract["caller_variable"])],
                             expr.Entity(shield.source_body))
            self.assertTrue({contract["initial"], contract["after_one_hit"]} <= look_health)
            self.assertEqual(expr.to_float(model.vars[(0, contract["look_health_variable"])]),
                             contract["after_one_hit"])
