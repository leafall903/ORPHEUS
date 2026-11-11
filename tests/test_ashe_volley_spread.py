"""Actual Viper normalized State output; damage-ray spread remains unimplemented."""

import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures

from ow174.game.content import HEROES_PATH
from ow174.game.script import expr, graph


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheVolleySpreadTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight

    def test_actual_hip_ramp_and_ads_null_ramp_at_reported_shot_time(self):
        for ads in (False, True):
            with self.subTest(ads=ads):
                run, player, _enemy = self.fight(distance=30)
                samples = []
                original = run.match.combat._shoot

                def shoot(shooter, command, state, number, time, *,
                          player=player, samples=samples, original=original, **kwargs):
                    if shooter is player and state.instance.graph.index == 0x14FC:
                        # Combat drains reports after the State ended. Its current
                        # clock is later; sample the actual report timestamp.
                        elapsed = time - state.start - state.offset
                        samples.append((state.node.pos, state.spread_ramp,
                                        state.spread0, state.spread_at(elapsed), state.ammo0))
                    return original(shooter, command, state, number, time, **kwargs)

                with patch.object(run.match.combat, "_shoot", side_effect=shoot):
                    for _ in range(60):
                        run.step({player: (0, 0, 2 if ads else 0)})
                    for frame in range(360):
                        buttons = (1 if frame % 60 < 5 else 0) | (2 if ads else 0)
                        run.step({player: (0, 0, buttons)})
                self.assertEqual(len(samples), 6)
                initial = 0.0
                for shot, (position, ramp, before, output, ammo) in enumerate(samples):
                    self.assertEqual(position, 14 if ads else 220)
                    self.assertEqual(ramp, 0.0 if ads else 6.0)
                    self.assertEqual(ammo, 12 - shot)
                    self.assertEqual(before, 0.0 if ads else initial)
                    expected = 1.0 if ads else min(1.0, expr.f32(expr.f32(1 / 6) + initial))
                    self.assertEqual(output, expected)
                    initial = expected
