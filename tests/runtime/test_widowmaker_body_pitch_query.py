"""Actual hook cosmetic pitch capture and existing body/observer serialization."""
import unittest
from dataclasses import replace

import test_widowmaker_combat as widow_tests
from test_script_driver import ClientModel

from ow174.game import observers, widowmaker
from ow174.game.script.driver import BodyStream


class WidowmakerBodyPitchTests(unittest.TestCase):
    def test_actual_hook_captures_pitch_and_rejects_stale_pose(self):
        case = widow_tests.WidowmakerGrappleTests()
        case.setUp()
        case.wall(10)
        for offset in range(20):
            case.run.step({case.a: (8192, -2048, 8 if offset == 0 else 0)})
            instance = case.hook()
            if 502 in instance.vars:
                break
        self.assertEqual(instance.vars[501].value(), 45.0)
        self.assertEqual(instance.vars[502].value(), -11.25)
        self.assertEqual(instance.evaluate(instance.graph.node(10).field('m_8A406A39')), -11.25)
        script = case.a.body_script
        remote = observers.snapshot(script)
        for frame, snapshot in (script.tree_frame(), (observers.full_frame(script, remote), remote)):
            model = ClientModel(widow_tests.WIDOWMAKER)
            model.apply(BodyStream(case.a.body).full(frame, snapshot))
            self.assertEqual(model.vars[(instance.id, 502)], -11.25)
        cfg = instance.graph.node(32).field('m_value')['m_configVars'][0]
        self.assertEqual(widowmaker.body_pitch(script.world, cfg, instance), -11.25)
        history = case.a.mover.history
        pose = history[-1]
        history[-1] = replace(pose, frame=pose.frame - 1)
        self.assertIs(widowmaker.body_pitch(script.world, cfg, instance), NotImplemented)
        history[-1] = pose
        script.retired = True
        self.assertIs(widowmaker.body_pitch(script.world, cfg, instance), NotImplemented)
