"""Source-team protocol evidence only; this does not establish native colors."""

import json
import unittest
from pathlib import Path

from test_game_combat import Run

from ow174.game import combat, world
from ow174.game.bits import BitReader
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script.driver import BASTION, ULT_COST
from ow174.game.server import Client, GameServer
from tests.runtime.test_game_packet_budget import MemorySocket, decoded
from tests.runtime.test_game_projectiles import read_shot
from tests.runtime.test_game_repeated_lifecycles import wire_frame


class BastionTeamSourceTests(unittest.TestCase):
    def test_actual_tank_source_team_and_launch_survive_observer_introduction(self):
        self._tank_sources('bastion-team-source-v1.json')

    def test_actual_tank_retains_both_volleys_across_delayed_observer_ack(self):
        self._tank_sources('bastion-delayed-volley-v1.json')

    def _tank_sources(self, fixture):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                               / fixture).read_text(encoding='utf-8'))
        run = Run(names=('Bastion', 'Ally', 'Enemy', 'DelayedAlly'))
        caster, ally, enemy, delayed = run.players
        match = run.match
        caster.team = ally.team = delayed.team = 0
        enemy.team = 1
        match.collision_world = flat_world(1)
        match.switch_hero(caster, heroes()[BASTION])
        for index, actor in enumerate(run.players):
            actor.mover.position[:] = (index * 20, 1, 0)
            actor.mover.body = None
        for _ in range(40):
            run.step({})
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({caster: (0, 0, 32)})
        for _ in range(110):
            run.step({})
        source_body, source_team = caster.body, caster.team_bit
        self.assertEqual(source_team, contract['expected']['source_team_filter'])
        self.assertEqual(enemy.team_bit, contract['expected']['enemy_team_filter'])
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, actor in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 43000 + number), number,
                            LinkCipher(incoming, outgoing), actor)
            server.clients[number] = client
            actor.client = client
            client.open = True
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), [], []))
        fire_steps = contract['input'].get('fire_steps', [15])
        delayed_ack = contract['input'].get('delayed_ack_step', 0)
        shots = []
        for step in range(220 if len(fire_steps) > 1 else 150):
            for client, _, _, _, _ in peers:
                actor = client.player
                if step == (5 if actor is delayed else 0):
                    controller = world.EntityUpdate(caster.entity, world.OP_CREATE,
                                                    lambda origin, own=actor is caster:
                                                    match._player_create(origin, caster, own=own))
                    body = (world.EntityUpdate(source_body, world.OP_CREATE,
                                               match._body_builder(caster, None, own=True))
                            if actor is caster else match.remote_body_create(actor, caster))
                    client._track_body_create(source_body)
                    client.queue_entities([controller, body])
            run.step({caster: (0, 0, 1 if step in fire_steps else 0)})
            if step in fire_steps:
                rocket = max((rocket for rocket in match.combat.rockets if rocket.shooter is caster),
                             key=lambda rocket: rocket.shot.frame)
                shots.append(rocket.shot)
            for client, cipher, window, wire, records in peers:
                now = run.frame * combat.TICK
                client.send_frame(run.frame, now)
                packet, _, _, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                wire.extend(wire_frame(packet)[1])
                sent = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent)
                for record in sent:
                    if record.entity != source_body or record.ch2 is None or client.player is caster:
                        continue
                    reader = BitReader(record.ch2.getvalue())
                    self.assertEqual((reader.bit(), reader.bit()), (1, 1))
                    reader.var_b()
                    launch = read_shot(reader)
                    stream = match.combat.observers.projectile_stream(client.player, source_body, npc=False)
                    installed = stream.base.states[(launch['instance'], launch['state'])]
                    self.assertEqual(dict(installed[2])['counter'], launch['activation'])
                self.assertTrue(window.accept(packet.seq))
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                if client.player is not delayed or step >= delayed_ack:
                    server._datagram(cipher.seal(ack), client.address, now)
        self.assertEqual(len(shots), len(fire_steps))
        for shot in shots:
            self.assertEqual((shot.body, shot.filter_bits), (source_body, source_team))
        for client, _, _, wire, records in peers:
            body_creates = [fields for update, fields in wire
                            if update.entity == source_body and update.op == world.OP_CREATE]
            self.assertTrue(body_creates)
            self.assertTrue(all(fields[26][0] == source_team for fields in body_creates))
            controllers = [fields for update, fields in wire
                           if update.entity == caster.entity and update.op == world.OP_CREATE]
            self.assertTrue(controllers)
            self.assertTrue(all(fields[26][0] == caster.controller_filter_bits for fields in controllers))
            if client.player is caster:
                continue  # The owner launches under its native prediction lease.
            launches = [record for record in records
                        if record.entity == source_body and record.ch2 is not None]
            self.assertEqual(len(launches), len(shots), client.player.name)
            for record, shot in zip(launches, shots, strict=True):
                reader = BitReader(record.ch2.getvalue())
                self.assertEqual((reader.bit(), reader.bit(), reader.var_b()), (1, 1, shot.frame))
                launch = read_shot(reader)
                self.assertEqual((launch['instance'], launch['state'], launch['activation']),
                                 (shot.instance, shot.state, shot.activation[0]))
                self.assertTrue(any(update.entity in launch['ids'] and update.op == world.OP_CREATE
                                    for update, _ in wire))
        self.assertNotEqual(enemy.team_bit, source_team)
        self.assertEqual(ally.team_bit, source_team)
        for shot in shots:
            self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertTrue(all(not watch.volleys for watch in match.combat.observers.watches.values()))
        self.assertFalse(match.combat.projectiles.observed)
