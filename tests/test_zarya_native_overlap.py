"""Original independent Zarya casts use the current native actors and pools."""
import unittest
import test_game_zarya as fixtures
from ow174.game.content import heroes

class ZaryaNativeOverlapTests(unittest.TestCase):
    def scene(self):
        case = fixtures.ZaryaProjectedBarrierTests()
        case.setUp()
        self.assertTrue(case.run.match.switch_hero(case.b, heroes()[fixtures.ZARYA]))
        case.b.mover.position[:] = [0, 1, 5]
        case.b.mover.body = None
        return case

    def test_projected_first_then_self_keeps_both_actual_native_pools(self):
        case = self.scene()
        case.run.step({case.a: (0, 0, 16)})
        projected = case.fight.barriers.authored_for(case.a.body)
        self.assertIsNotNone(projected)
        self.assertIs(projected.recipient, case.b)
        case.run.step({case.b: (0, 0, 8)})
        personal = case.fight.barriers.authored_for(case.b.body)
        self.assertIsNotNone(personal)
        self.assertIsNone(personal.recipient)
        self.assertIs(case.fight.barriers.scripted[projected.entity], projected)
        self.assertEqual((personal.vitals.total, projected.vitals.total), (200, 200))
        self.assertIsNot(personal.vitals, projected.vitals)
