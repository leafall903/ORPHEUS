"""Reported Sombra device replication through the production Client queues."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import statescript, translocators, world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import decode, graph
from ow174.game.server import Client, GameServer
from tests.runtime import test_game_combat as arena
from tests.runtime import test_game_sombra_combat as scenes
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class SombraReportedRepairTests(unittest.TestCase):
    def test_separate_owner_epoch_expires_encrypted_beacon_and_ignores_late_create_ack(self):
        for late_ack in (False, True):
            with self.subTest(late_ack=late_ack):
                scene = scenes.SombraCombatTests()
                scene.setUp()
                run, owner = scene.run, scene.a
                match = run.match
                self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000))
                owner.commands.reset(last_frame=9000)
                for step in range(1, 21):
                    run.frame += 1
                    match.update(run.frame * .016, run.frame)
                    owner.take_commands([Command(9000 + step)])
                server = GameServer()
                server.sock = MemorySocket()
                server.matches.append(match)
                peers = []
                for number, player in enumerate(run.players, 1):
                    incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
                    client = Client(server, ("memory.test", 46000 + number), number,
                                    LinkCipher(incoming, outgoing), player)
                    server.clients[number] = client
                    player.client = client
                    client.open = True
                    for other in run.players:
                        client._track_body_create(other.body)
                        client._ack_entity_create(other.entity)
                        client._ack_entity_create(other.body)
                    peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), []))

                sequence = 0
                delayed = []

                def exchange(acknowledge, peers=peers, match=match, server=server, delayed=delayed):
                    nonlocal sequence
                    for client, cipher, window, records in peers:
                        client.send_frame(match.tick, match.tick * .016)
                        packet = decoded(self, cipher, server.sock.sent[-1][0])[0]
                        records.extend(client.in_flight[packet.seq][1])
                        self.assertTrue(window.accept(packet.seq))
                        ack = Packet(client.conn, KIND_CONTROL, sequence,
                                     window.expected, window.bits, ACK)
                        sequence += 1
                        if acknowledge:
                            server._datagram(cipher.seal(ack), client.address, match.tick * .016)
                        else:
                            delayed.append((client, cipher, ack))

                for step in range(1, 21):
                    run.frame += 1
                    match.update(run.frame * .016, run.frame)
                    owner.take_commands([Command(9020 + step, buttons=16 if step == 1 else 0)])
                    exchange(not late_ack)
                fight = match.combat
                beacon, = fight.translocators.entries.values()
                self.assertNotEqual(beacon.cast.component.frame, beacon.last_tick)
                self.assertEqual(beacon.source_body, owner.body)
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.projectiles.shots)
                self.assertFalse(fight.projectiles.observed)
                for client, _, _, _ in peers:
                    self.assertIn(beacon.entity, client._bot_scripts)
                    self.assertEqual(client.bot_create_acked(beacon.entity), not late_ack)
                deadline = beacon.until_tick
                match.update((deadline + 1) * .016, deadline + 1)
                self.assertNotIn(beacon.entity, fight.translocators.entries)
                self.assertNotIn(beacon.entity, fight.deployables)
                self.assertFalse(beacon.body_script.component.instances)
                # Deliver old encrypted create ACKs only after the entity retired.
                for client, cipher, ack in delayed:
                    server._datagram(cipher.seal(ack), client.address, match.tick * .016)
                for step in range(80):
                    match.tick = deadline + 1 + step
                    exchange(True)
                    if all(beacon.entity not in client._bot_scripts
                           and not any(item.entity == beacon.entity for item in client.entities)
                           and not any(item.entity == beacon.entity
                                       for _, items in client.in_flight.values() for item in items)
                           for client, _, _, _ in peers):
                        break
                for client, _, _, records in peers:
                    ops = [item.op for item in records if item.entity == beacon.entity and item.op]
                    self.assertIn(world.OP_CREATE, ops)
                    self.assertIn(world.OP_DESTROY, ops)
                    self.assertLess(ops.index(world.OP_CREATE), ops.index(world.OP_DESTROY))
                    self.assertNotIn(beacon.entity, client._bot_scripts)
                    self.assertNotIn(beacon.entity, client._body_destroy_wait)
                    self.assertFalse(any(item.entity == beacon.entity for item in client.entities))
                    self.assertFalse(any(item.entity == beacon.entity
                                         for _, items in client.in_flight.values() for item in items))

    def test_acknowledged_owner_keeps_accepted_emp_deadlines_without_input(self):
        scene = scenes.SombraCombatTests()
        scene.setUp()
        scene.a.body_script.create_arrived = True
        scene.test_paid_emp_matches_pulse_and_expiry_with_no_player_commands()

    def test_translocator_release_clock_matches_single_eight_and_full_packets(self):
        trajectories = []
        for batch in (1, 8, 32):
            with self.subTest(batch=batch):
                scene = scenes.SombraCombatTests()
                scene.setUp()
                player, match = scene.a, scene.run.match
                player.body_script.create_arrived = True
                player.mover.teleport_grounded((0, 30, 0))
                player.body_script.ult_seconds = 0
                match.combat.sombra_charge.sync(player.body_script)
                first = scene.run.frame + 1
                for offset in range(0, 64, batch):
                    end = min(64, offset + batch)
                    tick = first + end - 1
                    match.update(tick * .016, tick)
                    player.take_commands([
                        Command(first + index, buttons=16 if index == 0 else 0)
                        for index in range(offset, end)
                    ])
                match.update((first + 64) * .016, first + 64)
                beacon, = match.combat.translocators.entries.values()
                self.assertEqual(beacon.created_tick, first + 6)
                trajectories.append(
                    (beacon.created_tick, beacon.until_tick, beacon.position, beacon.velocity),
                )
        self.assertEqual(len(trajectories), 3)
        self.assertTrue(all(row == trajectories[0] for row in trajectories))

    def test_full_packet_preserves_actual_camo_and_hack_selection(self):
        for buttons in (8, 2):
            with self.subTest(buttons=buttons):
                scene = scenes.SombraCombatTests()
                scene.setUp()
                player, match = scene.a, scene.run.match
                script = player.body_script
                script.create_arrived = True
                script.ult_seconds = 0
                match.combat.sombra_charge.sync(script)
                first = scene.run.frame + 1
                yaw, pitch = arena.aim(match.combat._eye(player), (0, 1, 1.5))
                for offset in (0, 32, 64):
                    tick = first + offset + 31
                    match.update(tick * .016, tick)
                    self.assertLess(script.frame, first + offset)
                    player.take_commands([
                        Command(first + index, yaw=yaw, pitch=pitch,
                                buttons=buttons if buttons == 2 or index == 0 else 0)
                        for index in range(offset, offset + 32)
                    ])
                if buttons == 8:
                    ability = scene.camo().state(13)
                    self.assertTrue(ability.flags & ability.RUNNING)
                else:
                    self.assertTrue(scene.hack_applications())

    def test_practice_bot_accepts_actual_hack_with_original_body_metadata(self):
        run = arena.Run()
        player = run.players[0]
        match = run.match
        match.switch_hero(player, heroes()[scenes.SOMBRA])
        victim = next(bot for bot in match.bots if bot.team != player.team)
        x, y, z = victim.position
        player.mover.teleport_grounded((x, y, z - 3))
        for _ in range(50):
            run.step({})
        self.assertIsNotNone(victim.body_script, "requires original Practice body metadata in the profile")
        yaw, pitch = arena.aim(match.combat._eye(player), (x, y + .4, z))
        for _ in range(100):
            run.step({player: (yaw, pitch, 2)})
            if victim.entity in match.combat.sombra_controls.locks:
                break
        effect, = match.combat.sombra_controls.locks[victim.entity]
        self.assertEqual(effect.application.source_entity(), player.body)
        self.assertEqual(effect.application.graph.index, 0x0B1F)

    def test_original_recycle_and_timeout_retire_beacon_and_start_cooldown(self):
        for reason in ("recycle", "timeout"):
            with self.subTest(reason=reason):
                scene = scenes.SombraCombatTests()
                scene.setUp()
                scene.press(16, 20)
                fight = scene.run.match.combat
                beacon, = fight.translocators.entries.values()
                scene.press(0, 80)
                if reason == "recycle":
                    scene.press(4, 20)
                else:
                    deadline = beacon.until_tick
                    scene.run.match.update((deadline + 1) * .016, deadline + 1)
                self.assertNotIn(beacon.entity, fight.translocators.entries)
                self.assertNotIn(beacon.entity, fight.deployables)
                self.assertNotIn(beacon.entity, fight.vitals)
                self.assertFalse(beacon.body_script.component.instances)
                self.assertGreater(beacon.cast.state(translocators.ABILITY).remaining(), 0)
                for player in scene.run.players:
                    self.assertTrue(any(update.entity == beacon.entity and update.op == world.OP_DESTROY
                                        for update in player.client.entities))

    def test_beacon_registers_original_vm_and_moves_for_owner_and_observer(self):
        fixture = (Path(__file__).resolve().parents[2]
                   / "fixtures/behavior/sombra-translocator-delivery-v1.json")
        expected = json.loads(fixture.read_text(encoding="utf-8"))["expected"]
        scene = scenes.SombraCombatTests()
        scene.setUp()
        server = GameServer()
        clients = []
        for number, player in enumerate(scene.run.players, 1):
            client = Client(server, ("memory.test", 45000 + number), number,
                            LinkCipher(b"i" * 32, b"o" * 32), player)
            player.client = client
            clients.append(client)
        scene.press(16, 20)
        fight = scene.run.match.combat
        beacon, = fight.translocators.entries.values()
        self.assertEqual(beacon.definition, translocators.DEFINITION)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.shots)
        for client in clients:
            delivery = client._bot_scripts[beacon.entity]
            self.assertEqual(delivery.source is beacon.body_script, expected["registered_body_vm_per_viewer"])
            initial, _ = delivery.source.initial
            chunk = statescript.chunk(initial, 0, 1)
            definitions = {item.graph.index: item.graph
                           for item in beacon.body_script.component.instances.values()}
            with patch.object(decode, "graph_of", side_effect=lambda index, definitions=definitions:
                              definitions.get(index) or graph.graph(index)):
                parsed = decode.read_chunk(chunk.getvalue(), chunk.count)
            received = {item.graph for item in parsed.instances.values()}
            self.assertIn(int(expected["device_graph"], 16), received)
        before = beacon.position
        for client in clients:
            client._ack_entity_create(beacon.entity)
        scene.press(0, 5)
        self.assertNotEqual(beacon.position, before)
        for client in clients:
            poses = [update for update in client.entities
                     if update.entity == beacon.entity and update.movement is not None]
            self.assertTrue(poses)
            self.assertEqual(poses[-1].movement.position, beacon.position)
            self.assertEqual(poses[-1].movement.velocity, beacon.velocity)
            self.assertEqual(poses[-1].resend, expected["movement_retransmission"])
        scene.press(0, 80)
        scene.press(16)
        self.assertAlmostEqual(scene.a.mover.position[2], beacon.position[2], places=2)
        scene.press(0, 40)
        self.assertFalse(fight.translocators.entries)
        for client in clients:
            pending = list(client.entities)
            deferred = client._body_destroy_wait.get(beacon.entity)
            if deferred is not None:
                pending.append(deferred[0])
            self.assertTrue(any(update.entity == beacon.entity and update.op == world.OP_DESTROY
                                for update in pending))
