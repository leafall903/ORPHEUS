"""Actual003 first-person heights with real inputs, projectiles and triangle walls.

The target fields are data-backed; using them as combat eye offsets remains a
host reconstruction until native/client comparison. No head-region claim.
"""

import unittest

import test_lucio_combat as fixtures

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE
from ow174.game.script import graph


class LucioGeometryTests(unittest.TestCase):
    def scenario(self):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        ally.mover.position[:] = (10, 1, 4)
        return helper, run, source, enemy, ally

    def wall(self, run, low, high, z=2):
        run.match.collision_world = World([
            (-50, 1, -50, -50, 1, 50, 50, 1, 50),
            (-50, 1, -50, 50, 1, 50, 50, 1, -50),
            (-5, low, z, 5, low, z, 5, high, z),
            (-5, low, z, 5, high, z, -5, high, z),
        ], name="explicit Lucio eye-height occlusion fixture")

    def test_stance_inputs_use_authored_eye_for_shots_targets_and_history(self):
        for crouched, height in ((False, 1.175), (True, 0.8225)):
            with self.subTest(crouched=crouched):
                _, run, source, _, _ = self.scenario()
                run.step({source: (0, 0, CROUCH if crouched else 0)})
                run.step({source: (0, 0, FIRE | (CROUCH if crouched else 0))})
                self.assertEqual(source.mover.crouched, crouched)
                [rocket] = run.match.combat.rockets
                self.assertAlmostEqual(rocket.position[1] - source.mover.position[1], height)
                target = next(t for t in run.match.combat._targets() if t.owner is source)
                self.assertEqual(target.eye, height)
                self.assertEqual(run.match.combat.history[source.body][-1][3], height)

    def test_primary_uses_lucio_height_at_low_walls_and_overhead_openings(self):
        for crouched, cutoff in ((False, 2.35), (True, 2.0)):
            for overhead in (False, True):
                with self.subTest(crouched=crouched, overhead=overhead):
                    helper, run, source, enemy, _ = self.scenario()
                    self.wall(run, cutoff if overhead else 1, 5 if overhead else cutoff)
                    stance = CROUCH if crouched else 0
                    run.step({source: (0, 0, stance)})
                    run.step({source: (0, 0, FIRE | stance)})
                    for _ in range(35):
                        run.step({source: (0, 0, stance)})
                    self.assertEqual(helper.health(run, enemy), 120 if overhead else 200)

    @unittest.skipUnless(graph.graph(0x0267), "requires actual0267 status supplement")
    def test_healing_sight_uses_the_same_lower_eye(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                helper, run, source, enemy, ally = self.scenario()
                enemy.mover.position[:] = (50, 1, 50)
                ally.mover.position[:] = (0, 1, 4)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is ally)
                fight.vitals_of(target).damage(100)
                if blocked:
                    self.wall(run, 1, 2.3, z=0.8)
                run.step({source: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                before = helper.health(run, ally)
                for _ in range(64):
                    run.step({})
                expected = 0 if blocked else 16 * 64 * combat.TICK
                self.assertAlmostEqual(helper.health(run, ally) - before, expected, places=5)
