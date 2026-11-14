"""Actual primary delivery preserves identity/lifetime without an active-bit oracle."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import pharah, projectiles, world
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.decode import read_chunk
from tests.runtime.test_game_projectiles import ObserverProjectileDeliveryTests, bits_of, read_shot

CASE = json.loads((Path(__file__).parents[2] /
                   'fixtures/behavior/pharah-primary-report-h0-v1.json').read_text())


class PharahReportH0Tests(unittest.TestCase):
    def test_primary_delivery_preserves_identity_and_lifetime_through_real_ack(self):
        peer = ObserverProjectileDeliveryTests()
        peer.setUp()
        self.addCleanup(peer.doCleanups)
        self.assertTrue(peer.match.switch_hero(peer.shooter, heroes()[pharah.HERO]))
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
        known = {index: item[0] for index, item in watch.stream.base.instances.items()}
        report_original, fired_original = nodes.WeaponVolley.report, projectiles.Projectiles.fired
        reports, consumed = [], []

        def report(state, until):
            selected = (state.instance.component is owner.body_script.component
                        and (state.instance.graph.index, state.node.pos) == pharah.PRIMARY)
            active, payload = (state.active, dict(state.payload())) if selected else (None, None)
            component = state.instance.component
            count = len(component.shots)
            result = report_original(state, until)
            if selected:
                for item in component.shots[count:]:
                    reports.append((item, active, payload))
            return result

        def fired(manager, player, state, *args, **kwargs):
            active = state.active
            result = fired_original(manager, player, state, *args, **kwargs)
            if player is owner and result is not None:
                consumed.append((result, state, active, kwargs.get('observer_activation')))
            return result

        first = peer.tick + 1
        with (patch.object(nodes.WeaponVolley, 'report', report),
              patch.object(projectiles.Projectiles, 'fired', fired)):
            peer.advance(CASE['input']['command_count'])
            owner.take_commands([Command(frame, buttons=FIRE if frame == first else 0)
                                 for frame in range(first, first + CASE['input']['command_count'])])
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(consumed), 1)
        item, report_active, payload = reports[0]
        shot, state, consume_active, argument = consumed[0]
        rocket, = peer.fight.rockets
        delivery, = shot.observers
        deadline = delivery.deadline
        record = delivery.record
        initial_activation = delivery.activation
        observer_deadline = shot.observer_deadline
        clock_origin = shot.clock_origin
        first_h0, creates = None, []
        for _ in range(8):
            peer.advance(1)
            records = peer.send()
            for update in records:
                if update.entity == delivery.record.ids[0] and update.op == world.OP_CREATE:
                    bits = bits_of(update.build(0))
                    creates.append([bits.bit(), bits.bit(), bits.bits(12)])
                if update.entity == owner.body and update.chunk is not None:
                    frame = read_chunk(update.chunk.getvalue(), update.chunk.count, known)
                    instance = frame.instances.get(shot.instance)
                    observed = instance.states.get(shot.state) if instance is not None else None
                    if not frame.owner and observed is not None and first_h0 is None:
                        first_h0 = {'tick': peer.tick, 'active': observed[0], 'payload': observed[1],
                                    'bits': update.chunk.count, 'hex': update.chunk.getvalue().hex()}
            if first_h0 is not None:
                break
            peer.ack()
        self.evidence = {'tupleLength': len(item), 'reportActive': report_active,
                         'reportPayload': payload, 'consumeActive': consume_active,
                         'sameStateObject': item[0] is state, 'definition': hex(shot.definition),
                         'sync': projectiles._sync(state), 'shotFrame': shot.frame,
                         'sourceAnchor': owner.body_script.world_frame_anchor,
                         'sourceFrameMs': owner.body_script.frame_ms,
                         'firstH0': first_h0, 'creates': creates,
                         'observerMetadataPresent': argument is not None}
        self.assertIs(item[0], state)
        self.assertEqual((state.instance.graph.index, state.node.pos), pharah.PRIMARY)
        self.assertEqual(shot.definition, int(CASE['expected']['definition'], 16))
        self.assertEqual(shot.frame, first)
        self.assertEqual(shot.activation, (payload['counter'], payload['start']))
        self.assertEqual(projectiles._sync(state), CASE['expected']['sync'])
        self.assertIsNotNone(first_h0)
        # The original H0 codec omits start; neither active bit is an admission oracle.
        self.assertEqual(first_h0['payload'], {key: payload[key]
                                              for key in ('offset', 'volleys', 'counter')})
        self.assertEqual(creates, [[0, 1, 0x33]])
        self.assertEqual(first_h0['payload']['counter'], CASE['expected']['counter'])
        self.assertEqual(first_h0['payload']['volleys'], CASE['expected']['volleys'])

        # Only the existing initial-delivery ACK is late; no new viewer or flight.
        lifetime_before = rocket.left
        peer.advance(CASE['input']['ack_delay_ticks'])
        self.assertIn(rocket, peer.fight.rockets)
        self.assertIs(rocket.shot, shot)
        self.assertIs(delivery.record, record)
        self.assertIs(delivery.activation, initial_activation)
        self.assertEqual(delivery.deadline, deadline)
        self.assertEqual(shot.observer_deadline, observer_deadline)
        self.assertEqual(shot.clock_origin, clock_origin)
        self.assertLess(rocket.left, lifetime_before)
        self.assertEqual(len(shot.observers), 1)
        peer.ack()
        peer.advance(1)
        launched = peer.launches(peer.send())
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0].entity, shot.body)
        bits = bits_of(launched[0].ch2)
        self.assertEqual([bits.bit(), bits.bit(), bits.var_b()], [1, 1, shot.frame])
        wire_shot = read_shot(bits)
        self.assertEqual((wire_shot['instance'], wire_shot['state'], wire_shot['activation'],
                          wire_shot['volleys'], wire_shot['ids']),
                         (shot.instance, shot.state, shot.activation[0],
                          record.volleys, list(record.ids)))
        peer.ack()
        self.assertTrue(delivery.launch_arrival.received)
        self.assertEqual(len(shot.observers), 1)
        self.assertEqual(len(peer.fight.rockets), 1)
        self.assertEqual(delivery.deadline, deadline)
        self.evidence.update(deadline=deadline, deadlineUnchanged=True,
                             physicalLifetimeBefore=lifetime_before, physicalLifetimeAfter=rocket.left,
                             liveFlights=1, launchesAfterLateAck=1, h2=wire_shot,
                             h2AcknowledgedByRealPeer=delivery.launch_arrival.received)
