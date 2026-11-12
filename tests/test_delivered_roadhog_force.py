"""Original Whole Hog force uses one packet and the actual shared mover."""

import math
import unittest

import test_game_roadhog as fixtures
from ow174.game import whole_hog
from ow174.game.commands import Command
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest


class WholeHogForceTests(unittest.TestCase):
    def test_actual_volley_caps_once_and_reuses_target_mover(self):
        run, hog, target, _, pool, angles = fixtures.WholeHogTests().session()
        fight = run.match.combat
        mover = target.mover
        before = tuple(mover.position)
        run.step({hog: (*angles, 32)})
        for _ in range(50):
            if fight.knockbacks.whole_hog_seen:
                break
            run.step({hog: (*angles, 0)})
        self.assertEqual(len(fight.knockbacks.whole_hog_seen), 1)
        motion = fight.knockbacks.whole_hog_motion[target.body]
        self.assertIs(motion.owner, target)
        self.assertAlmostEqual(math.hypot(motion.velocity[0], motion.velocity[2]), 10)
        self.assertAlmostEqual(motion.velocity[1], 1.8)
        self.assertIs(target.mover, mover)
        self.assertNotIn(target.body, fight.knockbacks.active)
        # A fresh body command and another world update in this same tick
        # must not integrate the selected external displacement twice.
        held_pose = tuple(mover.position)
        held_velocity = motion.velocity
        target.take_commands([Command(target.body_script.frame + 1)])
        fight.update(fight.now, fight.tick, frames=False)
        self.assertEqual(tuple(mover.position), held_pose)
        self.assertEqual(motion.velocity, held_velocity)
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
