"""Matrix output on the actual owned mech and existing BOB gun host."""
import unittest

import test_ashe_bob as fixtures
from ow174.game import content


class BobOwnedMechMatrixTests(unittest.TestCase):
    def test_native_absorption_output_and_release(self):
        run, owner, dva, bob = fixtures.BobTests().cast()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(dva, content.heroes()[0x02E000000000007A]))
        mech = dva.controlled_mech
        movement = mech.control_mover
        movement.position[:] = (0, 1, 200)
        mech.position = tuple(movement.position)
        dva.mover.position[:] = (0, 1, 200)
        for _ in range(80):
            run.step({})
        for _ in range(3):
            run.step({dva: (-32768, 0, 2)})
        movement.position[:] = (0, 1, 8)
        movement.yaw = -32768
        movement.body = None  # isolate Matrix from scene floor contacts
        mech.position, mech.yaw = tuple(movement.position), -32768
        before, shots = fight.vitals[mech.entity].total, bob.shots
        for _ in range(25):
            run.step({dva: (-32768, 0, 2)})
        matrix = next(i for i in mech.body_script.component.instances.values()
                      if i.graph.index == 0x02CC)
        self.assertTrue(matrix.states[3].active)
        self.assertGreater(bob.shots, shots)
        self.assertEqual(fight.vitals[mech.entity].total, before)
        self.assertEqual(matrix.vars[6584].value(), 14)
        self.assertEqual(matrix.vars[41].value().id, bob.bot.entity)
        for _ in range(20):
            run.step({dva: (-32768, 0, 0)})
        self.assertLess(fight.vitals[mech.entity].total, before)
