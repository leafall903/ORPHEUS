"""One actual Frag report retained through first H0 and its delayed initial ACK."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_junkrat_projectile_combat as scenes
from test_game_combat import FakeClient

from ow174.game import projectiles
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE
from ow174.game.script import decode, nodes

CASE = json.loads((Path(__file__).resolve().parents[2] /
                   'fixtures/behavior/junkrat-frag-report-h0-v1.json').read_text(encoding='utf-8'))


class FragReportH0Tests(unittest.TestCase):
    def test_actual_frag_report_first_h0_and_delayed_ack_keep_capture_and_deadline(self):
        run, owner, viewer = scenes.JunkratProjectileTests().setup_fight()
        fight = run.match.combat
        model = decode.ClientBody.for_hero(owner.hero.guid)
        for update in viewer.client.entities:
            if update.entity == owner.body and update.chunk is not None:
                model.apply(update)
        reports, consumed, first_h0, digests = [], [], [], []
        held = []
        old_report, old_fired = nodes.WeaponVolley.report, projectiles.Projectiles.fired
        old_queue = FakeClient.queue_entities

        def report(state, until):
            before = len(state.instance.component.shots)
            old_report(state, until)
            if state.instance.graph.index != 0x022B:
                return
            for item in state.instance.component.shots[before:]:
                reports.append((item, state.active, dict(state.payload())))
                if len(item) == 6:
                    component = state.instance.component
                    position = component.shots.index(item)
                    original = snapshot_digest(export_component_vm(component))
                    component.shots[position] = (*item[:5], None)
                    try:
                        missing = snapshot_digest(export_component_vm(component))
                    finally:
                        component.shots[position] = item
                    digests.append((original, missing, snapshot_digest(export_component_vm(component))))

        def fired(manager, player, state, *args, **kwargs):
            result = old_fired(manager, player, state, *args, **kwargs)
            if player is owner and state.instance.graph.index == 0x022B:
                consumed.append((result, state, state.active, kwargs.get('observer_activation'),
                                 manager.match.tick + result.bound + projectiles.DELIVERY_GRACE_TICKS))
            return result

        def queue(client, updates):
            passed = []
            if client is viewer.client:
                for update in updates:
                    if update.entity != owner.body or update.chunk is None:
                        passed.append(update)
                        continue
                    frame = model.apply(update)
                    if reports and not first_h0:
                        state = reports[0][0][0]
                        item = frame.instances.get(state.instance.id)
                        first_h0.append((frame.owner,
                                         item.states.get(state.index) if item is not None else None))
                    if reports:
                        held.append(update)  # Delay this existing H0 ACK, not another viewer.
                    else:
                        passed.append(update)
                return old_queue(client, passed)
            return old_queue(client, updates)

        with (patch.object(nodes.WeaponVolley, 'report', report),
              patch.object(projectiles.Projectiles, 'fired', fired),
              patch.object(FakeClient, 'queue_entities', queue)):
            run.step({owner: (0, -8192, FIRE)})
            run.step({})
        self.assertEqual((len(reports), len(consumed), len(first_h0)), (1, 1, 1))
        item, active, payload = reports[0]
        shot, state, consumed_active, argument, deadline = consumed[0]
        rocket, = [r for r in fight.rockets if r.shot is shot]
        self.evidence = {'reportShape': len(item), 'reportActive': active, 'consumeActive': consumed_active,
                         'firstH0': first_h0, 'payload': payload, 'definition': hex(shot.definition)}
        self.assertEqual(len(item), CASE['expected']['report_fields'])
        capture = item[5]
        self.assertIsNone(item[4])
        self.assertIs(item[0], state)
        self.assertTrue(active)
        self.assertFalse(consumed_active)
        self.assertEqual(capture, (state.node.cls, active, tuple(sorted(payload.items()))))
        self.assertEqual(sorted(payload), CASE['expected']['capture_fields'])
        self.assertEqual(projectiles._sync(state), CASE['expected']['sync'])
        self.assertIs(capture, argument)
        self.assertIs(capture, shot.observer_activation)
        initial, = [d for d in shot.observers if d.viewer is viewer]
        self.assertIs(capture, initial.activation)
        self.assertFalse(first_h0[0][0])
        self.assertTrue(first_h0[0][1][0])
        # The unchanged H0 volley codec transmits offset/volleys/counter;
        # the four-int report capture also retains its original start.
        self.assertEqual(first_h0[0][1][1], {key: value for key, value in payload.items() if key != 'start'})
        self.assertEqual(shot.definition, int(CASE['expected']['definition'], 16))
        self.assertIs(rocket.source_state, state)
        self.assertEqual((rocket.source_graph, rocket.source_entity), (0x022B, shot.body))
        self.assertEqual((shot.observer_deadline, initial.deadline), (deadline, deadline))
        original_record = shot.observer_record
        physical_left, physical_flown = rocket.left, rocket.flown
        self.assertEqual(len(digests), 1)
        self.assertNotEqual(digests[0][0], digests[0][1])
        self.assertEqual(digests[0][0], digests[0][2])

        self.assertTrue(held)
        self.assertFalse(initial.launch_arrival.received)
        with patch.object(FakeClient, 'queue_entities', queue):
            for _ in range(3):
                run.step({})
        self.assertFalse(initial.launch_arrival.received)
        old_queue(viewer.client, held)
        for _ in range(3):
            run.step({})
        self.assertIs(capture, shot.observer_activation)
        self.assertIs(shot.observer_record, original_record)
        self.assertEqual(initial.deadline, deadline)
        self.assertTrue(initial.launch_arrival.received)
        self.assertIn(rocket, fight.rockets)
        self.assertIs(rocket.shot, shot)
        self.assertAlmostEqual(rocket.left + rocket.flown, physical_left + physical_flown)
        self.evidence.update({'sameCaptureThroughFirstAndDelayedAck': True, 'deadline': deadline,
                              'samePhysicalShot': True, 'pendingDigests': digests})
