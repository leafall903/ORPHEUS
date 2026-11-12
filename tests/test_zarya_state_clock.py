"""Original world-clock consumers on the existing Zarya Wait/Chase states."""
import unittest
import test_game_zarya as original
from ow174.game import combat
from ow174.game.commands import Command

class ZaryaStateClockTests(unittest.TestCase):
    def test_idle_energy_paced_sparse_and_body_catchup_use_one_world_interval(self):
        values = []
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                case = original.ZaryaPersonalBarrierTests()
                case.setUp()
                case.activate()
                case.hit(100)
                case.run.step({})
                script = case.a.body_script
                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x0242)
                wait = instance.states[instance.graph.node(7).state]
                self.assertEqual(case.energy(), 20)
                self.assertEqual(wait.ms, 1000)
                start, frame = case.run.frame, script.frame
                end = start + 400
                for tick in ((end,) if sparse else range(start + 1, end + 1)):
                    case.run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(script.frame, frame)
                expected = 20 - ((end - wait.world_deadline) * 16 + 8) / 1000 * 1.8
                # Original Chase adds native EPSILON on every step; preserve its
                # bounded paced/sparse rounding instead of rewriting arithmetic.
                self.assertAlmostEqual(case.energy(), expected, delta=0.01)
                before = case.energy()
                case.a.take_commands([Command(frame + 400)])
                self.assertEqual(case.energy(), before)
                values.append(before)
        self.assertAlmostEqual(*values, delta=0.01)
