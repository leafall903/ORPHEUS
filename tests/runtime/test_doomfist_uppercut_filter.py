"""Original Uppercut filter producer, separate from victim helper adoption."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat
from ow174.game.script import expr


class UppercutFilterTests(DoomfistFixture, unittest.TestCase):
    def test_actual_uppercut_dispatches_nonempty_original_helper_targets_once(self):
        self.prepare(1.5)
        self.match.switch_hero(self.b, self.a.hero)
        self.settle(1)
        world = self.a.body_script.world
        calls = []
        original = world.play_script_host

        def observe(instance, node):
            if (instance.graph.index, node.pos) == (0x0DD8, 22):
                calls.append(instance.evaluate(node.field('m_targets')))
            return original(instance, node)

        with patch.object(world, 'play_script_host', side_effect=observe):
            self.run.step({self.a: (0, 0, 8)})
            self.settle(2)
        self.assertEqual(calls, [(expr.Entity(self.b.body),)])
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertFalse(self.a.body_script.component.parameter_scopes)

    def test_original_assignment_matches_native_filter_cases_and_scopes_restore(self):
        spec = json.loads((Path(__file__).resolve().parents[2] /
                           'fixtures/behavior/doomfist-uppercut-filter-v1.json').read_text())
        instance = next(i for i in self.a.body_script.component.instances.values()
                        if i.graph.index == 0x0DD8)
        world = self.a.body_script.world
        node = instance.graph.node(55)
        for case in spec['cases']:
            with self.subTest(case=case):
                instance.write((expr.INSTANCE, 375), tuple(expr.Entity(v) for v in case['input']))
                instance.write((expr.INSTANCE, 7457), tuple(expr.Entity(v) for v in case['previous']))
                result = world.assignment_value(instance, node, None)
                self.assertEqual(result, tuple(expr.Entity(v) for v in case['output']))
                self.assertFalse(instance.component.parameter_scopes)
        other = instance.graph.node(34)
        self.assertEqual(world.assignment_value(instance, other, 17), 17)
