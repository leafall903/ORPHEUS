"""One actual primary report stays authentic through first H0 and its initial ACK."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import hero_torbjorn, projectiles
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.decode import read_chunk
from tests.runtime.test_game_packet_budget import decoded
from tests.runtime.test_game_projectiles import ObserverProjectileDeliveryTests
from tests.runtime.test_game_repeated_lifecycles import wire_frame

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/torbjorn-primary-report-h0-v1.json').read_text())


class TorbjornPrimaryReportH0Tests(unittest.TestCase):
    def test_actual_primary_report_first_wire_h0_and_initial_ack_keep_same_capture(self):
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
        reports, consumed, digests = [], [], []

        def report(state, until):
            selected = (state.instance.component is owner.body_script.component
                        and (state.instance.graph.index, state.node.pos) == (0x1683, 29))
            active, payload = (state.active, dict(state.payload())) if selected else (None, None)
            component = state.instance.component
            count = len(component.shots)
            result = original_report(state, until)
            if selected:
                for item in component.shots[count:]:
                    reports.append((item, active, payload))
                    if len(item) == 6:
                        index = component.shots.index(item)
                        before = snapshot_digest(export_component_vm(component))
                        component.shots[index] = (*item[:5], None)
                        try:
                            missing = snapshot_digest(export_component_vm(component))
                        finally:
                            component.shots[index] = item
                        digests.append([before, missing, snapshot_digest(export_component_vm(component))])
            return result

        def fired(manager, player, state, *args, **kwargs):
            active = state.active
            result = original_fired(manager, player, state, *args, **kwargs)
            if player is owner and (state.instance.graph.index, state.node.pos) == (0x1683, 29):
                consumed.append((result, state, active, kwargs.get('observer_activation')))
            return result

        first = peer.tick + 1
        with (patch.object(nodes.WeaponVolley, 'report', report),
              patch.object(projectiles.Projectiles, 'fired', fired)):
            peer.advance(CASE['input']['command_count'])
            owner.take_commands([Command(frame, buttons=FIRE if frame == first else 0)
                                 for frame in range(first, first + CASE['input']['command_count'])])
        self.assertEqual((len(reports), len(consumed)), (1, 1))
        item, report_active, payload = reports[0]
        shot, state, consume_active, argument = consumed[0]
        self.assertIsNotNone(shot)
        rocket, = (rocket for rocket in peer.fight.rockets if rocket.shot is shot)
        delivery, = shot.observers
        record, launch_record = shot.observer_record, delivery.record
        deadline, bound, clock = delivery.deadline, shot.bound, shot.clock_origin
        first_h0 = None
        for _ in range(8):
            peer.advance(1)
            peer.send()
            packet, _, _, _, _, _ = decoded(peer, peer.cipher, peer.server.sock.sent[-1][0])
            _, records = wire_frame(packet)
            for update, _fields in records:
                if update.entity != owner.body or update.chunk is None:
                    continue
                frame = read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                instance = frame.instances.get(shot.instance)
                observed = instance.states.get(shot.state) if instance is not None else None
                if not frame.owner and first_h0 is None:
                    first_h0 = {'active': observed[0] if observed is not None else None,
                                'payload': observed[1] if observed is not None else None,
                                'bits': update.chunk.count, 'hex': update.chunk.getvalue().hex()}
            if first_h0 is not None:
                break
            peer.ack()
        self.evidence = {'tupleLength': len(item), 'reportActive': report_active,
                         'consumeActive': consume_active, 'payload': payload,
                         'activation': shot.activation, 'definition': hex(shot.definition),
                         'sync': projectiles._sync(state), 'firstH0': first_h0,
                         'deadline': deadline, 'pendingCaptureDigests': digests}
        self.assertEqual(len(item), CASE['expected']['report_fields'])
        self.assertIsNone(item[4])
        capture = item[5]
        self.assertIs(item[0], state)
        self.assertTrue(report_active)
        self.assertFalse(consume_active)
        self.assertEqual(capture, (state.node.cls, report_active, tuple(sorted(payload.items()))))
        self.assertEqual(shot.activation, (payload['counter'], payload['start']))
        self.assertIs(capture, argument)
        self.assertIs(capture, shot.observer_activation)
        self.assertIs(capture, delivery.activation)
        self.assertEqual(shot.definition, int(CASE['expected']['definition'], 16))
        self.assertEqual(projectiles._sync(state), CASE['expected']['sync'])
        self.assertIsNotNone(first_h0)
        self.assertTrue(first_h0['active'])
        self.assertEqual(first_h0['payload'], {key: payload[key]
                                             for key in ('offset', 'volleys', 'counter')})
        self.assertEqual(len(digests), 1)
        self.assertNotEqual(digests[0][0], digests[0][1])
        self.assertEqual(digests[0][0], digests[0][2])
        self.assertEqual(shot.observer_deadline, deadline)
        self.assertFalse(delivery.launch_arrival.received)
        flight_time = rocket.left + rocket.flown
        peer.advance(CASE['input']['initial_ack_delay_ticks'])
        self.assertIn(rocket, peer.fight.rockets)
        self.assertIs(rocket.shot, shot)
        self.assertIs(shot.observer_record, record)
        self.assertIs(delivery.record, launch_record)
        self.assertIs(delivery.activation, capture)
        self.assertEqual((delivery.deadline, shot.observer_deadline, shot.bound, shot.clock_origin),
                         (deadline, deadline, bound, clock))
        self.assertAlmostEqual(rocket.left + rocket.flown, flight_time)
        peer.ack()
        peer.advance(1)
        self.assertEqual(len(peer.launches(peer.send())), 1)
        self.assertTrue(delivery.creation.received)
        self.assertFalse(delivery.launch_arrival.received)
        peer.ack()  # Acknowledge the actual datagram that just carried this launch.
        self.assertTrue(delivery.launch_arrival.received)
        self.assertIs(shot.observer_record, record)
        self.assertIs(delivery.record, launch_record)
        self.assertIs(delivery.activation, capture)
        self.assertEqual((len(shot.observers), len(peer.fight.rockets)), (1, 1))
        self.assertEqual((delivery.deadline, shot.observer_deadline, shot.bound, shot.clock_origin),
                         (deadline, deadline, bound, clock))
        self.assertAlmostEqual(rocket.left + rocket.flown, flight_time)
        self.evidence.update(sameCaptureReportArgumentShotDelivery=True,
                             samePhysicalShotAndRecordsThroughInitialAck=True, deadlineUnchanged=True)
