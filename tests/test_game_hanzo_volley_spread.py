"""Actual Hanzo fire keeps authored zero-angle launches and existing damage.

Normalized output is a VM value; these checks do not establish native random
ray sampling, animated muzzle transforms or original-client presentation.
"""

import unittest
from unittest.mock import patch

import test_game_hanzo_sonic_cooldown as sonic_tests

from ow174.game import bodytrace, movelog


class HanzoVolleySpreadTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))

    def test_normal_sonic_and_storm_actual_launches_have_unbound_zero_angle_spread(self):
        for button, count, damage in ((0, 1, 125), (8, 1, 125), (16, 5, 350)):
            with self.subTest(button=button):
                case = sonic_tests.SonicCooldownTests()
                run, hero, target, _ = case.arena()
                fight = run.match.combat
                samples = []
                shoot = fight._shoot

                def observed(shooter, command, state, number, time,
                             hero=hero, samples=samples, shoot=shoot, **kwargs):
                    if shooter is hero:
                        launch = state.node.field('m_5EC1EB38')
                        self.assertEqual(launch['$'], 'STU_239B571E')
                        self.assertIsNone(state.node.field('m_3FFC9EB9'))
                        self.assertEqual(state.spread_ramp, 0)
                        self.assertEqual(state.spread_at(time - state.start - state.offset), 1)
                        self.assertIsNone(launch.get('m_23C72C36'))
                        self.assertIsNone(launch.get('m_7F0FED02'))
                        self.assertIsNone(launch.get('m_429A6C85'))
                        samples.append((state.instance.graph.index, state.node.pos, number))
                    return shoot(shooter, command, state, number, time, **kwargs)

                with patch.object(fight, '_shoot', side_effect=observed):
                    if button:
                        case.step(run, hero, target, button)
                        case.step(run, hero, target)
                    if button == 16:
                        for _ in range(140):
                            case.step(run, hero, target, 1)
                            if len(samples) >= count:
                                break
                        case.step(run, hero, target)
                        for _ in range(15):
                            case.step(run, hero, target)
                    else:
                        case.draw(run, hero, target)
                self.assertEqual(len(samples), count)
                self.assertEqual(fight.vitals[target.body].total, 600 - damage)
