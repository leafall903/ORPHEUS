"""Observe actual staged health receipts without replaying VM or backend effects."""

import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import test_doomfist_helper_health_receipt as health_fixture

from ow174.game import combat, doomfist_helper_health, replay_values
from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm, snapshot_digest
from ow174.game.script import expr, runtime


class HealthReceiptSnapshotTests(unittest.TestCase):
    def test_actual_receipt_export_is_idempotent_causal_and_keeps_unknown_fields_closed(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                               / 'doomfist-helper-health-snapshot-v1.json').read_text(encoding='utf-8'))
        expected = contract['expected']
        fixture = health_fixture.HelperHealthReceiptTests()
        fixture.prepare()
        inputs, helper, queue, _before, serial = fixture.stage()
        position, = helper.vars[431].value()
        self.assertIs(type(position), expr.Vec3)  # Latest formal body_positions getter, not old6e scalar0.
        receipt = doomfist_helper_health.consume_body_damage(inputs)
        component = helper.component
        health, hits = fixture.vitals().total, list(fixture.a.client.messages(combat.HIT))
        sequence, rng = component.seq.next_value, component.rng.getstate()
        states = [(state, state.active, state.counter) for state in helper.states.values()]
        forbidden = AssertionError('observation replayed live behavior')
        with ExitStack() as stack:
            for target, names in (
                (fixture.fight, ('_damage', '_doomfist_shields')),
                (runtime.Component, ('run_now', 'run_frame', 'ensure_valid')),
                (runtime.Instance, ('evaluate', 'run_node', 'dispatch', 'follow')),
                (runtime.Var, ('value',)),
                (expr, ('evaluate', 'run')),
                (doomfist_helper_health, ('resolve', 'consume_body_damage')),
            ):
                for name in names:
                    stack.enter_context(patch.object(target, name, side_effect=forbidden))
            first = export_component_vm(component)
            def walk(value):
                if isinstance(value, dict):
                    yield value
                    for item in value.values():
                        yield from walk(item)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        yield from walk(item)
            request, = [row for row in walk(replay_values.decode(first['value']))
                        if row.get('class') == 'ow174.game.doomfist_helper_health.Request']
            fields = dict(request['fields'])
            self.assertEqual(len(fields), expected['request_field_count'])
            self.assertEqual(fields['source']['ref'], [expected['actor_ref'], fixture.a.entity])
            self.assertEqual(fields['recipient']['ref'], [expected['actor_ref'], fixture.b.entity])
            self.assertEqual(fields['source_script']['ref'], [expected['source_script_ref'], fixture.a.body])
            self.assertEqual(fields['component'], {'ref': ['component']})
            self.assertEqual(fields['state'], {'ref': ['state', helper.id, inputs.state.index]})
            self.assertNotEqual(fields['source']['identity'], fields['recipient']['identity'])
            self.assertEqual(first, export_component_vm(component))
            original = inputs.source_frame
            object.__setattr__(inputs, 'source_frame', original + contract['input']['frame_delta'])
            self.assertNotEqual(snapshot_digest(first), snapshot_digest(export_component_vm(component)))
            object.__setattr__(inputs, 'source_frame', original)
            self.assertEqual(first, export_component_vm(component))
            object.__setattr__(receipt, 'accepted', contract['input']['mutated_accepted'])
            self.assertNotEqual(snapshot_digest(first), snapshot_digest(export_component_vm(component)))
            object.__setattr__(receipt, 'accepted', True)
            self.assertEqual(first, export_component_vm(component))
            object.__setattr__(receipt, 'unexpected_receipt_field', 1)
            with self.assertRaises(BodyVMSnapshotError):
                export_component_vm(component)
            object.__delattr__(receipt, 'unexpected_receipt_field')
            object.__setattr__(inputs, 'unexpected_request_field', 1)
            with self.assertRaises(BodyVMSnapshotError):
                export_component_vm(component)
            object.__delattr__(inputs, 'unexpected_request_field')
            inputs.state.unexpected_health_field = 1
            with self.assertRaises(BodyVMSnapshotError):
                export_component_vm(component)
            del inputs.state.unexpected_health_field
            self.assertEqual(first, export_component_vm(component))
        self.assertEqual(fixture.vitals().total, health)
        self.assertEqual(fixture.a.client.messages(combat.HIT), hits)
        self.assertEqual(fixture.fight.knockbacks.serial, serial)
        self.assertEqual(component.seq.next_value, sequence)
        self.assertEqual(component.rng.getstate(), rng)
        self.assertEqual([(state, state.active, state.counter) for state in helper.states.values()], states)
        self.assertFalse(receipt.original_completion_qualified)
        component.destroy(helper)
        queue.retire()
