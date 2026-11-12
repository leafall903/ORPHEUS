"""Actual full/empty magazine inputs across skill scope exit; no resource writes."""

import unittest

import test_doomfist_resources as resources

from ow174.game import combat
from ow174.game.script import nodes


class HeldPrimaryExitTests(unittest.TestCase):
    def exercise(self, button, graph):
        for empty in (False, True):
            with self.subTest(initially_empty=empty):
                fixture = resources.HandCannonResourceTests()
                fixture.setUp()
                if empty:
                    fixture.empty()
                fixture.step()
                before = fixture.volleys()
                fixture.step(button)
                instance = next(i for i in fixture.a.body_script.component.instances.values()
                                if i.graph.index == graph)
                self.assertTrue(instance.state(0).flags & nodes.Ability.RUNNING)
                for _ in range(220):
                    fixture.step(1)
                    self.assertGreaterEqual(fixture.ammo(), 0)
                    self.assertLessEqual(fixture.ammo(), 4)
                self.assertGreaterEqual(fixture.volleys() - before, 2 if empty else 4)
                for _ in range(300):
                    fixture.step()
                self.assertEqual(fixture.ammo(), 4)
                previous = fixture.volleys()
                fixture.step(1)
                self.assertEqual(fixture.ammo(), 3)
                self.assertEqual(fixture.volleys(), previous + 1)
                self.assertEqual(fixture.vitals().total, 200)
                self.assertFalse(fixture.a.client.messages(combat.HIT))

    def test_actual_full_and_empty_primary_recovers_after_uppercut(self):
        self.exercise(8, 0xDD8)

    def test_actual_full_and_empty_primary_cancels_punch_and_recovers(self):
        self.exercise(2, 0xD06)

    def test_actual_full_and_empty_primary_recovers_after_ground_slam(self):
        self.exercise(16, 0xB27)
