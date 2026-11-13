"""Actual Sentry numeric animation answers; no pose/model Handle/controller injection."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_symmetra_turrets as turrets
from test_symmetra_combat import fire

from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm, snapshot_digest
from ow174.game.script import data_flow, expr


class SentryDataFlowTests(unittest.TestCase):
    def test_actual_launch_build_numeric_answers_scopes_and_retirement(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior/'
                               'symmetra-sentry-numeric-dataflow-v1.json').read_text())
        fixture = turrets.SentryTests()
        run, owner, _enemy = fixture.setup_arena()
        fight = run.match.combat
        with patch.object(fight, '_collision', return_value=turrets.wall()):
            fixture.launch(run, owner)
            sentry, = fight.sentries.sentries
            script = sentry.bot.body_script
            component = script.component
            context = getattr(component.world, 'sentry_data_flow', None)
            self.assertIsInstance(context, data_flow.Context)
            self.assertIs(context.component, component)
            self.assertEqual(context.owner, sentry.bot.entity)
            self.assertIs(context.position, NotImplemented)
            # Placement starts the original half-second Wait before Chase7.
            self.assertEqual(context.providers(0x048000000000010D), ())
            fire(run, owner, contract['input']['additional_build_frames'], buttons=0)
            build, = context.providers(0x048000000000010D)
            self.assertEqual((build.instance.graph.index, build.node.pos), (0x157F, 15))
            self.assertFalse(build.mapping_self)
            self.assertIsNone(build.mapping_target)
            before = build.answer('float')
            self.assertEqual(before[0], 1)
            self.assertAlmostEqual(before[1], expr.to_float(component.entity_var(639).value()), places=6)
            self.assertGreaterEqual(before[1], 0.)
            self.assertLess(before[1], 1.)
            firing, = context.providers(0x04800000000000F7)
            self.assertTrue(firing.mapping_self)
            self.assertIsNone(firing.mapping_target)
            self.assertEqual(firing.answer('float'), tuple(contract['expected']['f7_float_answer']))
            # The two unresolved F4 BOOL providers retain their existing path.
            self.assertEqual(context.providers(0x04800000000000F4), ())
            first = snapshot_digest(export_component_vm(component))
            context.unknown_fixture_field = True
            try:
                with self.assertRaises(BodyVMSnapshotError):
                    export_component_vm(component)
            finally:
                del context.unknown_fixture_field
            fire(run, owner, contract['input']['finish_frames'], buttons=0)
            self.assertEqual(expr.to_float(component.entity_var(639).value()), 1.)
            self.assertEqual(context.providers(0x048000000000010D), ())
            self.assertNotEqual(first, snapshot_digest(export_component_vm(component)))
            self.assertEqual(context.providers(0x04800000000000F4), ())
            self.assertIsNone(script.controller)
            self.assertIsNone(sentry.bot.pet_owner)
            self.assertIsNone(fight.model_pose_for(sentry.bot.entity))
            fight.sentries._retire(sentry)
            self.assertTrue(context.retired)
            self.assertEqual(context.providers(0x048000000000010D), ())
            self.assertEqual(context.providers(0x04800000000000F7), ())
            self.assertIsNone(component.fault)
