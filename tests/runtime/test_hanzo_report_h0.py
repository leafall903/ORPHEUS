"""Actual Hanzo reports qualify original H0 for first viewers and old Storm joins."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_combat as scenes
import test_game_hanzo_sonic as sonic_fixture
import test_hanzo_late_arrow as late_fixture

from ow174.game import hanzo_projectiles, projectiles
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes

CASE = json.loads((Path(__file__).resolve().parents[2] /
                   'fixtures/behavior/hanzo-report-h0-v1.json').read_text(encoding='utf-8'))


class HanzoReportH0Tests(unittest.TestCase):
    @staticmethod
    def join(case, name):
        viewer = case.match.add_player(90 + len(case.match.players), name, SOLDIER, 1, False)
        scenes.in_world(case.match, viewer)
        case.match.switch_hero(viewer, heroes()[SOLDIER])
        viewer.mover.position[:] = (1100., 0., 1000.)
        viewer.mover.body = None
        return viewer

    def test_actual_emissions_keep_report_tuple_for_first_and_old_storm_viewers(self):
        self.evidence = []
        for kind, expected_definition in CASE['expected']['definitions'].items():
            with self.subTest(kind=kind):
                case = late_fixture.LateHanzoArrowTests()
                case.setUp()
                try:
                    self.exercise(case, kind, int(expected_definition, 16))
                finally:
                    case.doCleanups()

    def exercise(self, case, kind, expected_definition):
        first = self.join(case, 'Initial observer')
        for _ in range(4):
            case.step()
        owner, fight = case.owner, case.match.combat
        report_original, fired_original = nodes.WeaponVolley.report, projectiles.Projectiles.fired
        reports, consumed, pending_storage = [], [], []

        def report(state, until):
            component = state.instance.component
            before = len(component.shots)
            report_original(state, until)
            if component is not owner.body_script.component:
                return
            for item in component.shots[before:]:
                if item[0] is not state:
                    continue
                reports.append((item, state.active, dict(state.payload())))
                if kind == 'primary' and len(item) == 6 and not pending_storage:
                    index = component.shots.index(item)
                    original_digest = snapshot_digest(export_component_vm(component))
                    component.shots[index] = (*item[:5], None)
                    try:
                        missing_digest = snapshot_digest(export_component_vm(component))
                    finally:
                        component.shots[index] = item
                    pending_storage.append((original_digest, missing_digest,
                                            snapshot_digest(export_component_vm(component))))

        def fired(manager, player, state, *args, **kwargs):
            active_at_consumption = state.active
            result = fired_original(manager, player, state, *args, **kwargs)
            if player is owner and result is not None:
                consumed.append((result, state, active_at_consumption, kwargs.get('observer_activation'),
                                 [d.activation for d in result.observers if d.viewer is first]))
            return result

        with (patch.object(nodes.WeaponVolley, 'report', report),
              patch.object(projectiles.Projectiles, 'fired', fired)):
            if kind == 'sonic':
                fight.configure_sonic(sonic_fixture.profile())
                case.step(8)
                case.step()
            if kind == 'storm':
                case.step(16)
                case.step()
            for _ in range(45 if kind == 'storm' else 70):
                case.step(1)
                if kind == 'storm' and len(consumed) >= 2:
                    break
            case.step()
        self.assertEqual(len(consumed), 2 if kind == 'storm' else 1)
        self.assertEqual(len(reports), len(consumed))
        for (item, report_active, payload), (shot, state, consume_active, argument, first_h0) in zip(
                reports, consumed, strict=True):
            self.assertEqual(len(item), 6)
            self.assertIsNone(item[4])
            self.assertIs(item[0], state)
            capture = item[5]
            self.assertTrue(report_active)
            self.assertFalse(consume_active)
            self.assertIs(capture, argument)
            self.assertIs(capture, shot.observer_activation)
            self.assertEqual(capture, (state.node.cls, report_active, tuple(sorted(payload.items()))))
            self.assertEqual(shot.definition, expected_definition)
            self.assertEqual((payload['counter'], payload['start']), shot.activation)
            self.assertEqual(shot.observer_record.volleys, payload['volleys'] & 0xFF)
            initial, = [delivery for delivery in shot.observers if delivery.viewer is first]
            self.assertEqual(len(first_h0), 1)
            self.assertIs(first_h0[0], capture)
            self.assertEqual(initial.record.frame, shot.frame)
        if kind == 'primary':
            self.assertEqual(len(pending_storage), 1)
            before, missing, restored = pending_storage[0]
            self.assertNotEqual(before, missing)
            self.assertEqual(before, restored)

        shots = [entry[0] for entry in consumed]
        if kind == 'storm':
            self.assertNotEqual(shots[0].activation, shots[1].activation)
            self.assertTrue(all(any(rocket.shot is shot for rocket in fight.rockets) for shot in shots))
            self.assertNotEqual(consumed[0][1].counter, shots[0].activation[0])
        late = self.join(case, 'Late observer')
        order = []
        queue_original = late.client.queue_entities

        def queue(updates):
            for update in updates:
                for shot in shots:
                    for delivery in shot.observers:
                        if delivery.viewer is late and delivery.launch is update:
                            order.append(shot.frame)
                            self.assertIs(delivery.activation, shot.observer_activation)
                            watch = fight.observers.watches[(late.slot, shot.body)]
                            self.assertIs(watch.volleys[0], delivery)
            return queue_original(updates)

        with patch.object(late.client, 'queue_entities', side_effect=queue):
            for _ in range(8):
                case.step()
        self.assertEqual(order, [shot.frame for shot in shots])
        for shot in shots:
            delivery, = [item for item in shot.observers if item.viewer is late]
            self.assertTrue(delivery.launch_arrival.received)
            self.assertEqual(delivery.deadline, shot.observer_deadline)
            self.assertEqual(delivery.launch.entity, shot.body)
            self.assertEqual(shot.observer_activation, reports[shots.index(shot)][0][5])
        self.assertIsNone(hanzo_projectiles.volley_activation(consumed[-1][1], (-1, -1)))

        self.evidence.append({
            'kind': kind, 'definition': hex(expected_definition), 'sourceBody': owner.body,
            'reportShapes': [len(row[0]) for row in reports],
            'reportH0': [row[0][5] for row in reports],
            'consumeActive': [row[2] for row in consumed],
            'captureIsReportArgumentShotAndFirstDelivery': [
                row[0][5] is entry[3] is entry[0].observer_activation is entry[4][0]
                for row, entry in zip(reports, consumed, strict=True)],
            'frames': [shot.frame for shot in shots], 'lateLaunchOrder': order,
            'deadlineUnchanged': [next(d for d in shot.observers if d.viewer is late).deadline
                                  == shot.observer_deadline for shot in shots],
            'pendingStorageDigests': pending_storage,
        })
