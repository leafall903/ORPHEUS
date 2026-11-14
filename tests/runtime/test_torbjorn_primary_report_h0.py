"""One ordinary rivet preserves delivery identity without an active-bit policy."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.runtime.test_game_repeated_lifecycles as wire_tools
from ow174.game import hero_torbjorn, projectiles, world
from ow174.game.bits import BitReader
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.decode import read_chunk
from tests.runtime.test_game_packet_budget import decoded
from tests.runtime.test_game_projectiles import ObserverProjectileDeliveryTests

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/torbjorn-primary-report-h0-v1.json').read_text())


class TorbjornPrimaryReportH0Tests(unittest.TestCase):
    def test_actual_primary_first_delivery_and_real_acks_preserve_shot_identity(self):
        peer = ObserverProjectileDeliveryTests()
        peer.setUp()
        self.addCleanup(peer.doCleanups)
        self.assertTrue(peer.match.switch_hero(peer.shooter, heroes()[hero_torbjorn.HERO]))
        for _ in range(12):
            peer.advance(3)
            peer.send()
            peer.ack()
        owner = peer.shooter
        y = owner.mover.position[1]
        owner.mover.position = [0, y, 0]
        peer.viewer.mover.position = [0, y, CASE['input']['viewer_distance']]
        owner.mover.body = peer.viewer.mover.body = None
        watch = peer.fight.observers.watches[(peer.viewer.slot, owner.body)]
        known = {index: row[0] for index, row in watch.stream.base.instances.items()}
        original_report, original_fired = nodes.WeaponVolley.report, projectiles.Projectiles.fired
        reports, consumed = [], []

        def report(state, until):
            selected = (state.instance.component is owner.body_script.component
                        and (state.instance.graph.index, state.node.pos) == (0x1683, 29))
            payload = dict(state.payload()) if selected else None
            count = len(state.instance.component.shots)
            result = original_report(state, until)
            if selected:
                reports.extend((item, payload) for item in state.instance.component.shots[count:])
            return result

        def fired(manager, player, state, *args, **kwargs):
            result = original_fired(manager, player, state, *args, **kwargs)
            if player is owner and (state.instance.graph.index, state.node.pos) == (0x1683, 29):
                consumed.append((result, state))
            return result

        first = peer.tick + 1
        with (patch.object(nodes.WeaponVolley, 'report', report),
              patch.object(projectiles.Projectiles, 'fired', fired)):
            peer.advance(CASE['input']['command_count'])
            owner.take_commands([Command(frame, buttons=FIRE if frame == first else 0)
                                 for frame in range(first, first + CASE['input']['command_count'])])
        self.assertEqual((len(reports), len(consumed)), (1, 1))
        item, payload = reports[0]
        shot, state = consumed[0]
        self.assertIsNotNone(shot)
        self.assertIs(item[0], state)
        self.assertEqual(shot.activation, item[3])
        self.assertEqual(shot.activation, (payload['counter'], payload['start']))
        self.assertEqual(shot.definition, int(CASE['expected']['definition'], 16))
        self.assertEqual(projectiles._sync(state), CASE['expected']['sync'])
        rocket, = (rocket for rocket in peer.fight.rockets if rocket.shot is shot)
        delivery, = shot.observers
        record = delivery.record
        remembered = shot.observer_record
        deadline, bound, clock = delivery.deadline, shot.bound, shot.clock_origin
        self.assertEqual(shot.body, owner.body)
        self.assertEqual((record.instance, record.state, record.activation, record.volleys, record.frame),
                         (shot.instance, shot.state, shot.activation[0], payload['volleys'], shot.frame))
        packets, creates, launches = [], [], []
        read_shot, entity_record = wire_tools.read_shot, wire_tools.entity_record

        def send():
            peer.send()
            packet, _, _, _, _, _ = decoded(peer, peer.cipher, peer.server.sock.sent[-1][0])
            definitions, shots = [], []

            def observe_shot(reader):
                value = read_shot(reader)
                shots.append(value)
                return value

            def observe_entity(reader, operation):
                if operation == world.OP_CREATE:
                    copy = BitReader(reader.data, reader.pos)
                    copy.bits(14)
                    self.assertFalse(copy.bit())  # This actual projectile is not a map placeable.
                    definitions.append(copy.bits(12 if copy.bit() else 20 if copy.bit() else 32))
                return entity_record(reader, operation)

            with (patch.object(wire_tools, 'read_shot', observe_shot),
                  patch.object(wire_tools, 'entity_record', observe_entity)):
                _, records = wire_tools.wire_frame(packet)
            create_ids = [update.entity for update, _ in records if update.op == world.OP_CREATE]
            creates.extend(zip(create_ids, definitions, strict=True))
            launches.extend(shots)
            packets.append({'seq': packet.seq, 'create_ids': create_ids, 'launches': shots})
            return records

        first_h0 = None
        for _ in range(8):
            peer.advance(1)
            for update, _ in send():
                if update.entity != owner.body or update.chunk is None:
                    continue
                frame = read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                instance = frame.instances.get(shot.instance)
                observed = instance.states.get(shot.state) if instance is not None else None
                if not frame.owner and first_h0 is None:
                    self.assertIsNotNone(observed)
                    first_h0 = {'entity': update.entity, 'active': observed[0], 'payload': observed[1],
                                'bits': update.chunk.count, 'hex': update.chunk.getvalue().hex()}
            if first_h0 is not None:
                break
            peer.ack()
        self.assertIsNotNone(first_h0)
        self.assertIs(type(first_h0['active']), bool)  # No required True or False policy.
        self.assertEqual(first_h0['payload'], {key: payload[key]
                                             for key in ('offset', 'volleys', 'counter')})
        self.assertEqual(creates, [(record.ids[0], shot.definition & 0xFFFFFFFF)])
        self.assertFalse(launches)
        self.assertFalse(delivery.creation.received)
        self.assertFalse(delivery.launch_arrival.received)
        lifetime = rocket.left + rocket.flown
        peer.advance(CASE['input']['initial_ack_delay_ticks'])
        self.assertIs(delivery.record, record)
        self.assertIs(shot.observer_record, remembered)
        self.assertIn(rocket, peer.fight.rockets)
        self.assertIs(rocket.shot, shot)
        self.assertEqual((delivery.deadline, shot.bound, shot.clock_origin), (deadline, bound, clock))
        self.assertAlmostEqual(rocket.left + rocket.flown, lifetime)
        peer.ack()
        self.assertTrue(delivery.creation.received)
        peer.advance(1)
        send()
        self.assertEqual(len(launches), 1)
        keys = ('instance', 'state', 'activation', 'volleys', 'ids')
        self.assertEqual({key: launches[0][key] for key in keys},
                         {'instance': shot.instance, 'state': shot.state, 'activation': shot.activation[0],
                          'volleys': payload['volleys'], 'ids': list(record.ids)})
        self.assertFalse(delivery.launch_arrival.received)
        peer.ack()
        self.assertTrue(delivery.launch_arrival.received)
        self.assertIs(delivery.record, record)
        self.assertIs(shot.observer_record, remembered)
        self.assertIs(rocket.shot, shot)
        self.assertEqual((len(shot.observers), len(peer.fight.rockets), shot.frame), (1, 1, first))
        self.assertEqual((delivery.deadline, shot.bound, shot.clock_origin), (deadline, bound, clock))
        self.assertAlmostEqual(rocket.left + rocket.flown, lifetime)
        self.evidence = {'reportTupleLengthObserved': len(item), 'activation': shot.activation,
                         'payload': payload, 'firstH0': first_h0, 'packets': packets,
                         'creates': creates, 'launchCount': len(launches), 'deadline': deadline,
                         'clock': clock, 'samePhysicalShotRecordAndLifetime': True,
                         'createAndLaunchActuallyAcked': True, 'requiredActiveBit': None}
