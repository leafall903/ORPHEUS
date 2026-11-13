"""Actual Tank input and encrypted peers; protocol evidence, not native visuals."""

import json
import unittest
from pathlib import Path

import test_bastion_combat as scenes

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class BastionProjectileDeliveryTests(unittest.TestCase):
    def test_different_owner_epoch_hit_and_expiry_retire_after_late_ack(self):
        for hit in (True, False):
            with self.subTest(hit=hit):
                self.exercise(hit=hit)

    def test_replaced_body_keeps_old_clock_and_late_ack_leaves_no_proxy(self):
        self.exercise(hit=True, replace_body=True)

    def exercise(self, *, hit, replace_body=False):
        contract = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                               / "bastion-tank-command-clock-v1.json").read_text())
        inputs = contract["input"]["encrypted_delivery"]
        expected = contract["expected"]["encrypted_delivery"]
        helper = scenes.BastionCombatTests()
        helper.fixture(enemy_position=(0, 1, 8) if hit else (30, 1, 90),
                       bystander_position=(30, 1, 10), enemy_hero=0x02E0000000000040)
        run, caster, target = helper.run, helper.player, helper.enemy
        match = run.match
        world_origin, owner_origin = run.frame, inputs["owner_origin"]
        self.assertEqual(world_origin, inputs["world_origin"])
        self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True,
                                          start_frame=owner_origin))
        caster.commands.reset(last_frame=owner_origin)
        birth = caster.body_script.world_frame_anchor

        def input_frame(buttons=0):
            owner_frame = owner_origin + run.frame - world_origin
            yaw, pitch = helper.command_aim if hit else (0, 0)
            caster.take_commands([Command(owner_frame, yaw=yaw, pitch=pitch, buttons=buttons)])
            for other in run.players[1:]:
                other.take_commands([Command(run.frame)])

        def advance(buttons=0):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            input_frame(buttons)

        for _ in range(20):
            advance()
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(scenes.ULT_COST).value())
        advance(32)
        for _ in range(110):
            advance()
        before = helper.health(target).total
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ("memory.test", 41000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), []))
        advance(1)
        rocket, = match.combat.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertEqual(run.frame, expected["fire_world_tick"])
        self.assertEqual(shot.frame, expected["shot_owner_frame"])
        lease = match.combat.projectiles.lease_id(caster, shot.definition, shot.frame)
        owner_delay = inputs["late_owner_ack_steps"]["hit" if hit else "miss"]
        ended = None
        for step in range(200):
            if replace_body and step == 10:
                self.assertTrue(match.switch_hero(caster, scenes.heroes()[scenes.SOLDIER],
                                                  preserve_pose=True, start_frame=18000))
                caster.commands.reset(last_frame=18000)
                owner_origin, world_origin = 18000, run.frame
                self.assertIsNot(caster.body_script, script)
                self.assertEqual(shot.clock_origin, (*birth, script.frame_ms))
            advance()
            if ended is None and rocket not in match.combat.rockets:
                ended = run.frame
            for client, cipher, window, records in peers:
                now = run.frame * combat.TICK
                client.send_frame(run.frame, now)
                packet, _, _, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                records.extend(client.in_flight.get(packet.seq, (None, []))[1])
                self.assertTrue(window.accept(packet.seq))
                if client.player is not caster or step >= owner_delay:
                    ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                    server._datagram(cipher.seal(ack), client.address, now)
        self.assertIsNotNone(ended)
        self.assertLessEqual(shot.release_at, ended + projectiles.RELEASE_MARGIN)
        self.assertEqual(shot.release_at, expected["release_world_tick"]["hit" if hit else "miss"])
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        self.assertAlmostEqual(before - helper.health(target).total, 205 if hit else 0)
        self.assertEqual(shot.clock_origin, (*birth, script.frame_ms))
        for client, _, _, records in peers:
            ids = {record.entity for record in records if projectiles.SEEN_FIRST <= record.entity
                   < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN}
            if client.player is caster:
                ids.add(lease)
            self.assertFalse(any(update.entity in ids for update in client.entities))
            self.assertFalse(any(update.entity in ids for _, updates in client.in_flight.values()
                                 for update in updates))
        owner_records = [record for record in peers[0][3] if record.entity == lease]
        if replace_body:
            self.assertFalse(any(record.op == OP_CREATE for record in owner_records))
            for _, _, _, records in peers[1:]:
                proxy = [record for record in records if projectiles.SEEN_FIRST <= record.entity
                         < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
                self.assertTrue(proxy)
                self.assertEqual(proxy[-1].op, OP_DESTROY)
            return
        ops = [record.op for record in owner_records]
        self.assertEqual([op for i, op in enumerate(ops) if not i or op != ops[i - 1]],
                         [OP_CREATE, OP_DESTROY])
        owner_destroy = next(record for record in owner_records if record.op == OP_DESTROY)
        self.assert_release(owner_destroy, shot.frame)
        for _, _, _, records in peers[1:]:
            proxy = [record for record in records if projectiles.SEEN_FIRST <= record.entity
                     < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN]
            self.assertEqual([record.op for record in proxy], [OP_CREATE, OP_DESTROY])
            self.assert_release(proxy[-1], shot.frame)

    def assert_release(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        bits = BitReader(record.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline = bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, owner_frame)
