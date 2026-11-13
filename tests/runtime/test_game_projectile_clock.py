"""One-flight replay never retains a temporary scheduler clock."""

import unittest
from types import SimpleNamespace

from ow174.game import ana_projectiles, projectile_clock


class ProjectileClockTests(unittest.TestCase):
    def test_selected_flight_only_and_host_clock_restored_on_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                selected = SimpleNamespace(last_tick=97)
                other = SimpleNamespace(last_tick=90)
                fight = SimpleNamespace(now=1.6, tick=100, rockets=[selected, other])
                calls = []

                def step(rocket):
                    self.assertIs(rocket, selected)
                    calls.append(fight.tick)
                    self.assertAlmostEqual(fight.now, fight.tick * projectile_clock.TICK)
                    if fail and fight.tick == 99:
                        raise RuntimeError("flight failure")
                    rocket.last_tick = fight.tick

                fight._fly_current = step
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "flight failure"):
                        projectile_clock.advance_accepted(fight, selected)
                else:
                    projectile_clock.advance_accepted(fight, selected)
                self.assertEqual(calls, [98, 99] if fail else [98, 99, 100])
                self.assertEqual((fight.now, fight.tick), (1.6, 100))
                self.assertEqual(other.last_tick, 90)

    def test_ana_gate_declines_other_definitions_and_unmapped_flights(self):
        for graph, definition, last in ((0x1C36, 1, 5), (0x0B94, 0x04000000000015B7, None)):
            with self.subTest(graph=graph):
                rocket = SimpleNamespace(source_graph=graph, definition=definition, last_tick=last)
                self.assertFalse(ana_projectiles.advance(object(), rocket))
