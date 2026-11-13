"""Native command batching must not restart Hand Cannon flight clocks."""

import json
import unittest
from pathlib import Path

from ow174.game import messages
from ow174.game.bits import BitWriter
from ow174.game.link import ACK, KIND_FRAME, LinkCipher, Packet
from ow174.game.server import Client, GameServer
from tests.runtime.test_doomfist_combat import DoomfistFixture
from tests.runtime.test_game_packet_budget import MemorySocket


def command_packet(commands):
    record = BitWriter()
    record.bits(35, 16)
    record.bits(0, 8)
    record.bits(len(commands), 8)
    for index, (frame, buttons) in enumerate(commands):
        if not index:
            record.bits(frame, 32)
            record.bit(0)
            record.signed(0, 16)
            record.signed(0, 16)
            record.bits(buttons, 24)
            record.bits(0, 8)
            record.bits(0, 16)
        else:
            for bit in (0, 1, 0, 0, 0, 0, 1):
                record.bit(bit)
            record.bits(buttons, 24)
            record.bit(0)
    payload = BitWriter()
    messages.write_channel(payload)
    payload.bit(1)
    payload.bits(record.count, 32)
    payload.append(record)
    return payload.getvalue()


class HandCannonClockTests(unittest.TestCase):
    def test_encrypted_batches_keep_flight_age_damage_and_retirement(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/doomfist-hand-cannon-clock-v1.json"
        contract = json.loads(path.read_text())
        schedule, expected = contract["input"], contract["expected"]
        reference = None
        for distance in schedule["target_distances"]:
            for batch in schedule["batch_sizes"]:
                with self.subTest(distance=distance, batch=batch):
                    fixture = DoomfistFixture()
                    fixture.prepare(distance)
                    player, match = fixture.a, fixture.match
                    fight = match.combat
                    server = GameServer()
                    server.sock = MemorySocket()
                    server.matches.append(match)
                    client = Client(server, ("memory.test", 40001), 1,
                                    LinkCipher(b"i" * 32, b"o" * 32), player)
                    client.open = True
                    player.client = client
                    server.clients[1] = client
                    client._ack_entity_create(player.entity)
                    client._ack_entity_create(player.body)
                    player.body_script.create_arrived = True
                    peer = LinkCipher(b"o" * 32, b"i" * 32)
                    pending, flights = [], []
                    seq, retired = 0, None
                    for tick in range(schedule["first_frame"], schedule["last_frame"] + 1):
                        match.update(tick * .016, tick)
                        pending.append((tick, int(tick == schedule["fire_frame"])))
                        if len(pending) == batch or tick == schedule["last_frame"]:
                            packet = Packet(client.conn, KIND_FRAME, seq, 0, 0, ACK,
                                            command_packet(pending))
                            server._datagram(peer.seal(packet), client.address, tick * .016)
                            seq += 1
                            pending = []
                            self.assertEqual(player.commands.last_frame, tick)
                        if not flights and fight.rockets:
                            flights = list(fight.rockets)
                            self.assertEqual(len(flights), expected["pellets"])
                            self.assertTrue(all(q.last_tick == expected["launch_world_tick"]
                                                for q in flights))
                        if flights and retired is None and not any(q in fight.rockets for q in flights):
                            retired = tick
                        if tick == schedule["query_world_tick"]:
                            if distance == 600:
                                self.assertAlmostEqual(flights[0].flown,
                                                       expected["query_miss_flight_seconds"])
                                sample = tuple((q.direction, q.position) for q in flights)
                                if reference is None:
                                    reference = sample
                                for actual, wanted in zip(sample, reference, strict=True):
                                    self.assertEqual(actual[0], wanted[0])
                                    for axis, target in zip(actual[1], wanted[1], strict=True):
                                        self.assertAlmostEqual(axis, target)
                            key = "far_target_health" if distance == 600 else "near_target_health"
                            health = expected[key]
                            self.assertEqual(fixture.vitals().total, health)
                    self.assertEqual(retired, expected["all_retired_world_tick"])

    def test_body_epoch_maps_command_frame_to_a_different_world_tick(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/doomfist-hand-cannon-clock-v1.json"
        case = json.loads(path.read_text())["epoch_offset_case"]
        fixture = DoomfistFixture()
        fixture.prepare(600)
        player, match = fixture.a, fixture.match
        command = player.commands.last_frame + 1
        world = match.tick + case["world_gap_ticks"]
        offset = case["fire_after_body_frames"]
        match.update(world * .016, world)
        self.assertTrue(match.switch_hero(player, player.hero))
        self.assertIsNone(player.body_script.world_frame_anchor)
        epoch = match.combat.knockbacks.body_epochs[player.body]
        self.assertIs(epoch[0], player.body_script)
        self.assertEqual(epoch[1], (world, command - 1))
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        client = Client(server, ("memory.test", 40001), 1,
                        LinkCipher(b"i" * 32, b"o" * 32), player)
        client.open = True
        player.client = client
        server.clients[1] = client
        peer = LinkCipher(b"o" * 32, b"i" * 32)
        match.update((world + offset) * .016, world + offset)
        packet = Packet(client.conn, KIND_FRAME, 0, 0, 0, ACK, command_packet([(command, 1)]))
        server._datagram(peer.seal(packet), client.address, (world + offset) * .016)
        flights = list(match.combat.rockets)
        self.assertEqual(len(flights), 11)
        self.assertTrue(all(q.last_tick == world + offset and q.last_tick != command for q in flights))
