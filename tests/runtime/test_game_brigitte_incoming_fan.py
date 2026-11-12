"""Enemy actual Fan rays hit Brigitte or her held shield; no center-ray assumption."""

import math
import unittest
from unittest.mock import patch

import test_game_brigitte as brigitte_tests

from ow174.game import mccree
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes


class IncomingFanTests(unittest.TestCase):
    def scene(self, *, shield):
        run, brigitte = brigitte_tests.BrigitteTests().fixture()
        enemy = run.players[1]
        run.match.switch_hero(enemy, heroes()[mccree.HERO])
        x, y, z = brigitte.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z - 5], None
        for _ in range(20):
            run.step({brigitte: (-32768, 0, SECONDARY if shield else 0)})
        return run, brigitte, enemy

    def test_actual_enemy_fan_uses_six_distinct_authored_cone_rays(self):
        run, brigitte, enemy = self.scene(shield=False)
        rays = []
        original = mccree.fan_direction

        def observe(shooter, state, forward, number, time, activation):
            result = original(shooter, state, forward, number, time, activation)
            if shooter is enemy and state.instance.graph.index == 0x1D2 and state.node.pos == 37:
                rays.append(result)
            return result

        with patch.object(mccree, 'fan_direction', side_effect=observe):
            run.step({enemy: (0, 0, SECONDARY)})
            for _ in range(60):
                run.step({})
        self.assertEqual(len(rays), 6)
        self.assertEqual(len(set(rays)), 6)
        self.assertTrue(all(ray[2] >= math.cos(math.radians(6)) - 1e-7 for ray in rays))
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is brigitte)
        self.assertLess(fight.vitals_of(target).total, 200)

    def test_actual_enemy_fan_breaks_front_shield_without_same_shot_spill(self):
        run, brigitte, enemy = self.scene(shield=True)
        fight = run.match.combat
        barrier = fight.brigitte.barriers[brigitte.body]
        target = next(t for t in fight._targets() if t.owner is brigitte)
        self.assertEqual(barrier.health, 250)
        shots = []
        shoot = type(fight)._shoot

        def observe(combat, shooter, command, state, number, time, **kwargs):
            before = barrier.health, fight.vitals_of(target).total
            result = shoot(combat, shooter, command, state, number, time, **kwargs)
            if shooter is enemy and state.node.pos == 37:
                shots.append((before, (barrier.health, fight.vitals_of(target).total)))
            return result

        with patch.object(type(fight), '_shoot', observe):
            run.step({brigitte: (-32768, 0, SECONDARY), enemy: (0, 0, SECONDARY)})
            for _ in range(60):
                run.step({brigitte: (-32768, 0, SECONDARY)})
        self.assertEqual(len(shots), 6)
        breaking = [after for before, after in shots if before[0] > 1 and after[0] == 1]
        self.assertEqual(breaking, [(1, 200)])
        # A later shot in the same burst can hit the now-unshielded owner.
        self.assertTrue(any(before[0] == 1 and after[1] < before[1] for before, after in shots))
        self.assertEqual(barrier.health, 1)
        self.assertGreater(barrier.instance.vars[61].value(), 0)
