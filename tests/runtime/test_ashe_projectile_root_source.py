"""Actual E allocates pending native roots with their own typed source."""

import json
import unittest
from pathlib import Path

import test_ashe_dynamite as flight_fixture

from ow174.game.script import expr, runtime


class AsheProjectileRootSourceTests(unittest.TestCase):
    def test_actual_e_seeds_pending_roots_from_managed_component(self):
        spec = json.loads((Path(__file__).parents[2] /
            'fixtures/behavior/ashe-projectile-root-source-v1.json').read_text())
        run, owner, _enemy, manager, flight = flight_fixture.DynamiteTests().cast(distance=100)
        source = flight.managed_source
        self.assertTrue(source.current())
        self.assertNotIn(source.entity, (owner.body, flight.shot.entity))
        instances = tuple(source.component.instances.values())
        self.assertEqual([i.graph.index for i in instances], spec['graphs'])
        for instance in instances:
            with self.subTest(graph=instance.graph.index):
                self.assertEqual(instance.context().var(runtime.INSTANCE, 0x1B), expr.Entity(source.entity))
                self.assertEqual(instance.source_entity(), source.entity)
                self.assertIsNone(instance.descriptor_source_entity)
                self.assertTrue(instance.initialization_pending)
                self.assertFalse(instance.initialized)
                self.assertFalse(instance.states)
        for _ in range(3):
            run.step({})
        self.assertTrue(source.current())
        self.assertIs(manager.grenades[0], flight)
        self.assertTrue(all(i.initialization_pending and not i.states for i in instances))
        self.assertFalse(source.association_known)
        self.assertIsNone(source.associated_component)
