"""Actual throw commands with packet batching and encrypted delivery ACKs."""

import json
import unittest
from pathlib import Path

import test_ashe_combat as fixtures
from test_game_packet_budget import MemorySocket, decoded

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import graph
from ow174.game.server import Client, GameServer

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-dynamite-clock-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


@unittest.skipUnless(graph.GRAPHS_PATH.is_file(), 'requires external 104319 graphs')
class DynamiteClockTests(unittest.TestCase):
    def throw(self, batch, *, encrypted=False, epoch_offset=0):
        run, player, _ = fixtures.AsheCombatTests().fight(distance=100)
        if epoch_offset:
            self.assertTrue(run.match.switch_hero(player, player.hero, preserve_pose=True,
                                                 start_frame=run.frame + epoch_offset))
        if encrypted:
            server = GameServer()
            server.sock = MemorySocket()
            client = Client(server, ('memory.test', 40001), 1,
                            LinkCipher(b'i' * 32, b'o' * 32), player)
            server.clients[1] = client
            client.open = True
            player.client = client
            client._ack_entity_create(player.entity)
            self.server, self.client = server, client
            self.cipher = LinkCipher(b'o' * 32, b'i' * 32)
            self.window, self.seq = ReceiveWindow(), 0
        start = run.frame
        command_start = player.body_script.frame
        for first in range(1, 49, batch):
            last = min(first + batch - 1, 48)
            run.frame = start + last
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(command_start+i, buttons=16 if i <= 5 else 0)
                                  for i in range(first, last+1)])
        grenade, = run.match.combat.dynamite.grenades
        return run, player, grenade

    def test_batched_input_preserves_launch_age_flight_and_fuse(self):
        results = []
        expected = CONTRACT['expected']
        for batch in CONTRACT['input']['batch_sizes']:
            run, player, grenade = self.throw(batch)
            self.assertEqual(grenade.born, grenade.shot.frame)
            self.assertEqual(grenade.born, expected['authoritative_born'])
            self.assertEqual(grenade.fuse, expected['authoritative_fuse'])
            self.assertEqual(player.body_script.frame, run.frame)
            results.append((grenade.position, grenade.velocity, grenade.born, grenade.fuse))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_distinct_command_epoch_uses_the_existing_world_frame_mapping(self):
        baseline, _, expected = self.throw(1)
        offset = CONTRACT['input']['additional_command_epoch_offset']
        run, player, grenade = self.throw(32, epoch_offset=offset)
        self.assertEqual(run.frame, baseline.frame)
        self.assertEqual(player.body_script.frame, run.frame + offset)
        self.assertEqual(grenade.shot.frame, expected.shot.frame + offset)
        self.assertEqual((grenade.born, grenade.fuse, grenade.position, grenade.velocity),
                         (expected.born, expected.fuse, expected.position, expected.velocity))

    def test_encrypted_ack_confirms_without_restarting_dynamic_flight(self):
        run, player, grenade = self.throw(8, encrypted=True)
        shot = grenade.shot
        launch = grenade.born
        self.assertEqual(launch, shot.frame)
        samples = []
        for step in range(12):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame)])
            self.client.send_frame(run.frame, run.frame * combat.TICK)
            packet, _, _, _, _, _ = decoded(self, self.cipher, self.server.sock.sent[-1][0])
            if step >= 5:  # withhold the first five owner-report/confirmation ACKs
                self.assertTrue(self.window.accept(packet.seq))
                ack = Packet(self.client.conn, KIND_CONTROL, self.seq,
                             self.window.expected, self.window.bits, ACK)
                self.seq += 1
                self.server._datagram(self.cipher.seal(ack), self.client.address, run.frame * combat.TICK)
            samples.append((grenade.born, grenade.fuse, grenade.position))
        self.assertTrue(shot.created)
        self.assertTrue(any(arrival.received for arrival in shot.created.values()))
        self.assertEqual({(born, fuse) for born, fuse, _ in samples}, {(launch, launch + 125)})
        self.assertGreater(len({position for _, _, position in samples}), 1)
        self.assertIn(grenade, run.match.combat.dynamite.grenades)

    def test_viper_hits_the_unbatched_flight_after_a_delayed_throw_packet(self):
        baseline, _, expected = self.throw(1)
        position = expected.position
        run, player, grenade = self.throw(32)
        self.assertEqual(run.frame, baseline.frame)
        yaw, pitch = fixtures.aim(run.match.combat._eye(player), position)
        run.step({player: (yaw, pitch, 1)})
        self.assertLess(run.frame, grenade.fuse)
        self.assertNotIn(grenade, run.match.combat.dynamite.grenades)

    def test_mid_packet_click_hits_its_command_time_dynamic_projectile(self):
        aim = None
        for batch in (1, 32):
            run, player, _ = fixtures.AsheCombatTests().fight(distance=100)
            engine = run.match.combat.dynamite
            start = run.frame
            for first in range(1, 49, batch):
                last = min(first+batch-1, 48)
                run.frame = start+last
                run.match.update(run.frame * combat.TICK, run.frame)
                commands = []
                for index in range(first, last+1):
                    if index == 41:
                        yaw, pitch = aim
                        commands.append(Command(start+index, yaw=yaw, pitch=pitch, buttons=1))
                    else:
                        commands.append(Command(start+index, pitch=-6000, buttons=16 if index <= 5 else 0))
                player.take_commands(commands)
                if batch == 1 and last == 40:
                    grenade, = engine.grenades
                    aim = fixtures.aim(run.match.combat._eye(player), grenade.position)
            self.assertFalse(engine.grenades, f'batch size {batch}')
