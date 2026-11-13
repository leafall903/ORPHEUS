"""Original ModifyHealth minimum-Y preserves a faster airborne Bash victim."""

import unittest
from unittest.mock import patch

import test_game_brigitte as fixtures
import test_game_brigitte_shield as shield_fixtures

from ow174.game.commands import FIRE, SECONDARY
from ow174.game.mover import Mover


class BashMinimumYTests(unittest.TestCase):
    def test_bash_clamps_minimum_y_without_adding_lift_twice(self):
        for vertical in (-5.0, 0.0, 5.0):
            with self.subTest(vertical=vertical):
                case = fixtures.BrigitteTests()
                run, brig = case.fixture()
                enemy = run.players[1]
                x, y, z = brig.mover.position
                enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
                shield_fixtures.BarrierTests().raise_shield(run, brig, 25)
                samples = []
                original = Mover.add_external_impulse

                def collect(mover, impulse, original=original, enemy=enemy, samples=samples):
                    before = mover.velocity
                    original(mover, impulse)
                    if mover is enemy.mover:
                        samples.append((before, mover.velocity, impulse))

                enemy.mover.velocity = (0.0, vertical, 0.0)
                with patch.object(Mover, "add_external_impulse", collect):
                    run.step({brig: (0, 0, SECONDARY | FIRE)})
                    for _ in range(12):
                        if samples:
                            break
                        run.step({brig: (0, 0, SECONDARY)})
                self.assertEqual(len(samples), 1)
                before, after, impulse = samples[0]
                self.assertAlmostEqual(after[1], max(before[1], 1.0), places=5)
                self.assertAlmostEqual(after[2] - before[2], 3.0, places=5)
                self.assertEqual(impulse[1], max(0.0, 1.0 - before[1]))
                hp = run.match.combat.vitals_of(case.target(run, enemy))
                self.assertEqual(hp.total, 195)
