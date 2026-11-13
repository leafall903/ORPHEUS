"""Sigma owner/observer disposal labels with separate command/world epochs."""
import unittest

from test_game_combat import aim
from test_game_packet_budget import MemorySocket, decoded
from test_sigma_combat import SIGMA, SigmaCombatTests

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import expr
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY


class SigmaOwnerClockTests(unittest.TestCase):
    def test_spheres_retire_owner_and_observer_in_original_owner_domain(self):
        self.exercise(1, 110)

    def test_authoritative_rock_retire_labels_use_captured_owner_domain(self):
        self.exercise(16, 70)

    def exercise(self, button, damage):
        run, owner, enemy, _ = SigmaCombatTests().arena(offset=0, distance=6)
        match = run.match
        owner.quanta = 32000
        self.assertTrue(match.switch_hero(owner, heroes()[SIGMA], start_frame=9000))
        owner.commands.reset(last_frame=9000)
        owner_frame = 9000
        for at in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            through = 9000 + at // 2
            if through > owner_frame:
                owner.take_commands([Command(through)])
                owner_frame = through
            enemy.take_commands([Command(run.frame)])
        fight = match.combat
        health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is enemy)
        before = health.total
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 43000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), []))
        captured = []
        facing = aim(fight._eye(owner), fight._eye(enemy))
        cast_start = owner_frame
        for step in range(150):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records in peers:
                client.send_frame(run.frame, now)
                packet, *_ = decoded(self, cipher, server.sock.sent[-1][0])
                records.extend(client.in_flight.get(packet.seq, (None, []))[1])
                self.assertTrue(window.accept(packet.seq))
                # Owner ACK arrives after a short physical flight can end.
                if client.player is not owner or step >= 20:
                    ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                    server._datagram(cipher.seal(ack), client.address, now)
            through = cast_start + (step + 1) // 2
            if through > owner_frame:
                owner.take_commands([Command(through, buttons=button if through == cast_start + 1 else 0,
                                             yaw=facing[0], pitch=facing[1])])
                owner_frame = through
            enemy.take_commands([Command(run.frame)])
            for rocket in fight.rockets:
                if rocket.shot is not None and all(rocket.shot is not s for s in captured):
                    captured.append(rocket.shot)
        self.assertEqual(before - health.total, damage)
        self.assertEqual(len(captured), 2 if button == 1 else 1)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.shots)
        self.assertFalse(fight.projectiles.observed)
        for shot in captured:
            self.assertEqual(shot.clock_origin, (*owner.body_script.sigma_birth, 32))
            self.assertGreater(shot.frame, 9000)
            if shot.authoritative:
                self.assertEqual({d.record.ids for d in shot.observers}, {(shot.entity,)})
                component = owner.body_script.component
                self.assertEqual(component.entity_var(20360).value(), expr.Entity(shot.entity))
                instance = component.instances[shot.instance]
                self.assertFalse(instance.node_state(instance.graph.node(31)).active)
                self.assertFalse(instance.node_state(instance.graph.node(44)).active)
            for delivery in shot.observers:
                self.assertTrue(delivery.creation.received)
                self.assertTrue(delivery.launch_arrival.received)
                self.assertTrue(delivery.finished)
                records = peers[0 if delivery.viewer is owner else 1][3]
                self.check_release(records, delivery.record.ids[0], shot.frame)
            if not shot.authoritative:
                self.assertTrue(shot.created)
                for entity, arrival in shot.created.items():
                    self.assertTrue(arrival.received)
                    self.check_release(peers[0][3], entity, shot.frame)

    def check_release(self, records, entity, owner_frame):
        selected = [r for r in records if r.entity == entity]
        self.assertEqual(list(dict.fromkeys(r.op for r in selected if r.op)), [OP_CREATE, OP_DESTROY])
        for update in selected:
            if not update.op:
                self.assertIsNotNone(update.chunk)  # The rock now also carries its own VM frames.
        released = next(r for r in selected if r.op == OP_DESTROY)
        self.assertIsNotNone(released.ch2)
        bits = BitReader(released.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline, delta = bits.var_b(), bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        native_frame = (baseline + delta) & 0xFFFFFFFF
        self.assertGreater(native_frame, owner_frame + 4096)
