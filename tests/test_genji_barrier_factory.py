"""Actual Reinhardt RMB/Pet/0096 boundary; this does not accept ray interception."""

import unittest

import test_genji_pools as pool_tests
from test_genji_combat import GenjiFixture

from ow174.game.script import expr


class GenjiBarrierFactoryTests(GenjiFixture):
    def scene(self):
        run, genji, reinhardt, _ = pool_tests.GenjiPoolTests.scene(self, "Reinhardt", 4)
        for _ in range(90):
            run.step({})
        return run, genji, reinhardt

    def raise_barrier(self, run, reinhardt):
        for _ in range(40):
            run.step({reinhardt: (-32768, 0, 2)})
        self.assertEqual(len(run.match.combat.barriers), 1)
        return next(iter(run.match.combat.barriers.values()))

    def test_actual_pet_and_cross_entity_call_create_independent_health_component(self):
        run, _genji, reinhardt = self.scene()
        self.assertFalse(run.match.combat.barriers)
        barrier = self.raise_barrier(run, reinhardt)
        self.assertNotEqual(barrier.entity, reinhardt.body)
        self.assertIsNot(barrier.component, reinhardt.body_script.component)
        self.assertEqual({i.graph.index for i in barrier.component.instances.values()}, {0x25B4, 0x96})
        called = next(i for i in barrier.component.instances.values() if i.graph.index == 0x96)
        self.assertTrue(called.state(1).active)
        self.assertEqual(expr.to_float(called.vars[586].value()), 1600)
        self.assertEqual(expr.to_float(called.vars[587].value()), 1600)
        self.assertEqual(called.source_entity(), reinhardt.body)
        self.assertEqual(barrier.vitals.total, 1600)
        self.assertEqual(barrier.component.entity_var(253).value(), 1600)
        owner_graph = next(i for i in reinhardt.body_script.component.instances.values()
                           if i.graph.index == 0x95)
        self.assertEqual(owner_graph.vars[38].value(), expr.Entity(barrier.entity))
        self.assertEqual(self.health(run, reinhardt), 500)

    def test_release_retires_child_and_next_activation_uses_new_identity(self):
        run, _genji, reinhardt = self.scene()
        old = self.raise_barrier(run, reinhardt)
        for _ in range(30):
            run.step({})
        self.assertFalse(run.match.combat.barriers)
        self.assertTrue(old.retired)
        self.assertFalse(old.component.instances)
        new = self.raise_barrier(run, reinhardt)
        self.assertNotEqual(new.entity, old.entity)
        self.assertIsNot(new.component, old.component)
        self.assertEqual(new.vitals.total, 1600)

    def test_body_replacement_cannot_retain_old_child_or_replay_its_request(self):
        run, _genji, reinhardt = self.scene()
        old = self.raise_barrier(run, reinhardt)
        source = reinhardt.body_script
        owner_graph = next(i for i in source.component.instances.values() if i.graph.index == 0x95)
        run.match.switch_hero(reinhardt, reinhardt.hero)
        self.assertTrue(old.retired)
        self.assertFalse(run.match.combat.barriers)
        source.world.compatibility_play_script(owner_graph, owner_graph.graph.node(6))
        self.assertFalse(run.match.combat.barriers)

    def test_death_retires_barrier_on_next_combat_update(self):
        run, genji, reinhardt = self.scene()
        old = self.raise_barrier(run, reinhardt)
        target = next(t for t in run.match.combat._targets() if t.owner is reinhardt)
        run.match.combat._damage(genji, target, 1000, False, None, run.frame)
        run.step({})
        self.assertTrue(old.retired)
        self.assertFalse(run.match.combat.barriers)


if __name__ == "__main__":
    unittest.main()
