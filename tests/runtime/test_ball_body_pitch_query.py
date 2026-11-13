"""Original body-pitch query and actual RMB graph capture, not native anchor acceptance."""
import unittest

import test_delivered_wrecking_ball_grapple as grapple
from test_script_driver import ClientModel

from ow174.game import ball, observers
from ow174.game.script.driver import BodyStream


class BodyPitchQueryTests(unittest.TestCase):
    def test_actual_rmb_captures_own_body_pitch_for_hook_cosmetic(self):
        for pitch, degrees in ((8192, 45.0), (-8192, -45.0)):
            with self.subTest(pitch=pitch):
                run, player, _, _, _ = grapple.scene()
                run.step({player: (0, pitch, 2)})
                instance = ball.instance(player, ball.GRAPPLE)
                self.assertEqual(instance.vars[502].value(), degrees)
                self.assertTrue(ball.rolling(player))
                script = player.body_script
                remote = observers.snapshot(script)
                for frame, snapshot in (script.tree_frame(), (observers.full_frame(script, remote), remote)):
                    model = ClientModel(ball.HERO)
                    model.apply(BodyStream(player.body).full(frame, snapshot))
                    self.assertEqual(model.vars[(instance.id, 502)], degrees)
                cfg = dict(instance.graph.node(103).field('m_value')['m_configVars'][0])
                cfg['m_entity'] = {'$': 'STUConfigVarDynamic', 'm_identifier': 38, 'm_60DB8F99': 0}
                self.assertIs(ball.body_pitch(script.world, cfg, instance), NotImplemented)
