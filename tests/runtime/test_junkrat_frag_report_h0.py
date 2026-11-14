"""Actual Frag H0, H2 and encrypted ACK without prescribing the StEC active bit."""
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import test_junkrat_projectile_combat as scenes
from test_game_packet_budget import MemorySocket, decoded
from test_game_projectiles import read_shot
from test_game_repeated_lifecycles import wire_frame

from ow174.game import combat, projectiles
from ow174.game.bits import BitReader
from ow174.game.commands import FIRE, Command
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script import decode, nodes
from ow174.game.server import Client, GameServer

CASE = json.loads((Path(__file__).resolve().parents[2] /
                   'fixtures/behavior/junkrat-frag-report-h0-v1.json').read_text(encoding='utf-8'))


class FragReportH0Tests(unittest.TestCase):
    def test_actual_frag_h0_h2_ack_keep_identity_counter_deadline_and_lifetime(self):
        run, owner, viewer = scenes.JunkratProjectileTests().setup_fight()
        match, fight = run.match, run.match.combat
        model = decode.ClientBody.for_hero(owner.hero.guid)
        for update in viewer.client.entities:
            if update.entity == owner.body and update.chunk is not None:
                model.apply(update)
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 46000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            # Existing entered-body fixture prerequisites; projectile/H0/H2
            # arrivals below change only through actual encrypted packet ACKs.
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow()))
        reports, consumed, h0, h2, acknowledgments = [], [], [], [], []
        old_report, old_fired = nodes.WeaponVolley.report, projectiles.Projectiles.fired

        def report(state, until):
            before = len(state.instance.component.shots)
            old_report(state, until)
            if state.instance.graph.index == 0x022B:
                for item in state.instance.component.shots[before:]:
                    reports.append((item, state.active, dict(state.payload())))

        def fired(manager, player, state, *args, **kwargs):
            result = old_fired(manager, player, state, *args, **kwargs)
            if player is owner and state.instance.graph.index == 0x022B:
                delivery, = [d for d in result.observers if d.viewer is viewer]
                consumed.append((result, state, delivery, delivery.record, delivery.deadline,
                                 result.observer_record, result.observer_activation,
                                 result.observer_deadline))
            return result

        with (patch.object(nodes.WeaponVolley, 'report', report),
              patch.object(projectiles.Projectiles, 'fired', fired)):
            for step in range(12):
                run.frame += 1
                now = run.frame * combat.TICK
                match.update(now, run.frame)
                for client, cipher, window in peers:
                    client.send_frame(run.frame, now)
                    packet, *_ = decoded(self, cipher, server.sock.sent[-1][0])
                    _, wire_records = wire_frame(packet)
                    sent = client.in_flight.get(packet.seq, (None, []))[1]
                    if client.player is viewer and reports:
                        state = reports[0][0][0]
                        for record, _ in wire_records:
                            if record.entity == owner.body and record.chunk is not None:
                                frame = model.apply(record)
                                item = frame.instances.get(state.instance.id)
                                row = item.states.get(state.index) if item is not None else None
                                if row is not None and not h0:
                                    h0.append((step, packet.seq, frame.owner, row))
                        for record in sent:
                            if record.entity == owner.body and record.ch2 is not None:
                                bits = BitReader(record.ch2.getvalue())
                                self.assertTrue(bits.bit())
                                self.assertTrue(bits.bit())
                                source_frame = bits.var_b()
                                launch = read_shot(bits)
                                h2.append((step, packet.seq, source_frame, launch))
                                self.assertFalse(consumed[0][2].launch_arrival.received)
                    self.assertTrue(window.accept(packet.seq))
                    ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                    server._datagram(cipher.seal(ack), client.address, now)
                    if client.player is viewer and h2 and packet.seq == h2[-1][1]:
                        self.assertTrue(consumed[0][2].launch_arrival.received)
                        acknowledgments.append((step, packet.seq))
                owner.take_commands([Command(run.frame, pitch=-8192, buttons=FIRE if step == 0 else 0)])
                viewer.take_commands([Command(run.frame)])
                if acknowledgments:
                    break
        self.assertEqual((len(reports), len(consumed), len(h0), len(h2), len(acknowledgments)),
                         (1, 1, 1, 1, 1))
        item, report_active, payload = reports[0]
        shot, state, delivery, record, deadline, saved_record, saved_activation, saved_deadline = consumed[0]
        rocket, = [r for r in fight.rockets if r.shot is shot]
        self.assertIs(item[0], state)
        self.assertEqual(item[3], shot.activation)
        self.assertEqual(shot.definition, int(CASE['expected']['definition'], 16))
        self.assertEqual(projectiles._sync(state), CASE['expected']['sync'])
        self.assertEqual(state.node.cls, CASE['expected']['class'])
        self.assertFalse(h0[0][2])  # This is an observer H0, not an owner frame.
        self.assertEqual(h0[0][3][1], {key: payload[key] for key in CASE['expected']['wire_payload_keys']})
        launch = h2[0][3]
        self.assertEqual((launch['instance'], launch['state'], launch['activation'], launch['volleys']),
                         (shot.instance, shot.state, shot.activation[0], payload['volleys'] & 0xFF))
        self.assertEqual(h2[0][2], shot.frame)
        self.assertLess(h0[0][0], h2[0][0])
        self.assertIs(delivery.record, record)
        self.assertEqual(delivery.deadline, deadline)
        self.assertIs(shot.observer_record, saved_record)
        self.assertIs(shot.observer_activation, saved_activation)
        self.assertEqual(shot.observer_deadline, saved_deadline)
        self.assertIs(rocket.source_state, state)
        self.assertEqual((rocket.source_graph, rocket.source_entity), (0x022B, shot.body))
        lifetime = float(state.evaluate(state.node.field('m_projectileLifetime'), watch=False))
        self.assertAlmostEqual(rocket.left + rocket.flown, lifetime)
        self.assertEqual(shot.bound, math.ceil(lifetime / combat.TICK))
        self.evidence = {'reportFields': len(item), 'reportActiveObserved': report_active,
                         'h0ActiveObserved': h0[0][3][0], 'payload': payload,
                         'h0': h0, 'h2': h2, 'actualACKs': acknowledgments,
                         'sameShotRecordDeadline': True, 'physicalLifetime': lifetime,
                         'remainingPlusFlown': rocket.left + rocket.flown,
                         'baselineLimit': 'Entered bodies use existing fixture create ACK setup.'}
