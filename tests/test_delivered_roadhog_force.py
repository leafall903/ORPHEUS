"""Original Whole Hog force uses one packet and the actual shared mover."""

import math
import unittest
from unittest.mock import patch

import test_game_roadhog as fixtures
from ow174.game import whole_hog
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest


class WholeHogForceTests(unittest.TestCase):
    def test_actual_volley_caps_once_and_reuses_target_mover(self):
        run, hog, target, _, pool, angles = fixtures.WholeHogTests().session()
        fight = run.match.combat
        mover = target.mover
        before = tuple(mover.position)
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as applied:
            run.step({hog: (*angles, 32)})
            for _ in range(50):
                if fight.knockbacks.whole_hog_seen:
                    break
                run.step({hog: (*angles, 0)})
        self.assertEqual(len(fight.knockbacks.whole_hog_seen), 1)
        self.assertEqual(applied.call_count, 1)
        actual_target, vector = applied.call_args.args
        self.assertIs(actual_target.owner, target)
        self.assertAlmostEqual(math.hypot(vector.x, vector.z), 10)
        self.assertAlmostEqual(vector.y, 1.8)
        self.assertIs(target.mover, mover)
        self.assertIs(fight.knockbacks.active[target.body], mover)
        self.assertLess(pool.total, 20000)
        state = next(state for instance in hog.body_script.component.instances.values()
                     if instance.graph.index == 0x01C6
                     for state in instance.states.values() if state.node.pos == 33)
        spec = whole_hog.evaluate(state, state.node.field('m_6396149F')['m_modifyHealth'])
        self.assertEqual((spec.force, spec.cap, spec.lift_cap), (14, 10, 14))
        self.assertAlmostEqual(spec.lift, 1.8)
        self.assertEqual(spec.scale(5), (1, 1, 1))
        self.assertEqual(spec.scale(20), (1, 1, 1))
        self.assertAlmostEqual(spec.scale(60)[0], .55)
        self.assertEqual(spec.scale(101), (0, 0, 0))
        snapshot_digest(export_body_vm(hog.body_script))
        snapshot_digest(export_body_vm(target.body_script))
        for ability in hog.body_script.ultimates:
            ability.stop()
        hog.body_script.component.run_now()
        run.step({})
        self.assertGreater(target.mover.position[2], before[2])
