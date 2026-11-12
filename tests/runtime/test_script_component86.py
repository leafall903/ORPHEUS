"""Component86 priority lists and state lifetime."""
import math
import unittest

from tests.test_script_runtime_budget import graph, node
from ow174.game.script import component86, expr, runtime, scene


class Component86Tests(unittest.TestCase):
    def test_vector_record_tracks_value_and_priority_then_retires(self):
        entity, vector_var, priority_var = 123, 14, 15

        def dynamic(identifier):
            return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": expr.INSTANCE}

        definition = graph([
            node(0, "STU_CF8C7CC5", state=0, m_00D49978=expr.Entity(entity),
                 m_priority=expr.Asset(42)),
            node(1, "STU_FC16FF45", state=1, m_00D49978=expr.Entity(entity),
                 m_7CCD8B4B=dynamic(vector_var), m_priority=dynamic(priority_var)),
        ])
        world = scene.SceneWorld()
        world.bind(entity)
        registry = component86.Registry({42: (5, False), 43: (10, False)})
        world.set_component86(entity, registry)
        component = runtime.Component(entity, world=world, strict=True)
        instance = component.create(definition)
        ordinary, vector = (instance.state(index) for index in range(2))
        with component.operation():
            instance.write((expr.INSTANCE, priority_var), expr.Asset(42))
            ordinary.begin()
            vector.begin()
        self.assertEqual([entry.kind for entry in registry.entries], [2, 3])
        self.assertEqual(registry.entries[1].vector, expr.Vec3(0, 0, 0))

        position = expr.Vec3(1, 2, 3)
        with component.operation():
            instance.write((expr.INSTANCE, vector_var), position)
        component.run_frame(1)
        self.assertEqual([entry.state for entry in registry.entries], [ordinary, vector])
        self.assertEqual(registry.entries[1].vector, position)
        with component.operation():
            instance.write((expr.INSTANCE, priority_var), expr.Asset(43))
        component.run_frame(2)
        self.assertEqual([entry.state for entry in registry.entries], [vector, ordinary])
        self.assertEqual((registry.entries[0].priority, registry.entries[0].vector), (10, position))
        with component.operation():
            vector.end(False)
        self.assertEqual([entry.state for entry in registry.entries], [ordinary])

    def test_numeric_pair_shares_primary_list_and_retires_on_end(self):
        entity = 123
        definition = graph([
            node(0, "STU_CF8C7CC5", state=0, m_00D49978=expr.Entity(entity)),
            node(1, "STU_473549CA", state=1, m_00D49978=expr.Entity(entity),
                 m_49EEBB65=0.0, m_priority=expr.Asset(42)),
            node(2, "STU_473549CA", state=2, m_00D49978=expr.Entity(entity),
                 m_49EEBB65=-2.5, m_FD4D6C19=3),
        ])
        world = scene.SceneWorld()
        world.bind(entity)
        registry = component86.Registry({42: (9999.0, True)})
        world.set_component86(entity, registry)
        component = runtime.Component(entity, world=world, strict=True)
        instance = component.create(definition)
        ordinary, first, second = [instance.state(index) for index in range(3)]
        with component.operation():
            ordinary.begin()
            first.begin()
            second.begin()
        self.assertEqual([entry.state for entry in registry.entries], [first, ordinary, second])
        self.assertEqual([entry.kind for entry in registry.entries], [1, 2, 1])
        self.assertEqual(registry.entries[0].numeric_pair, component86.NumericPair(0.0, expr.f32(math.pi)))
        self.assertEqual(registry.entries[2].numeric_pair, component86.NumericPair(-2.5, 3.0))
        self.assertEqual(registry.selection_entries, [])
        with component.operation():
            first.end(False)
            second.end(False)
        self.assertEqual([entry.state for entry in registry.entries], [ordinary])

    def test_numeric_pair_missing_component_queues_abort_before_reading_parameters(self):
        entity = 123
        definition = graph([
            node(0, "STU_473549CA", state=0, m_00D49978=expr.Entity(entity),
                 m_49EEBB65={"$": "unrecognized_numeric_fixture"}),
        ])
        world = scene.SceneWorld()
        world.bind(entity)
        world.set_component86(entity, None)
        component = runtime.Component(entity, world=world, strict=True)
        state = component.create(definition).state(0)
        with component.operation():
            state.begin()
        self.assertTrue(state.active)
        component.run_frame(1)
        self.assertFalse(state.active)
        self.assertIsNone(component.fault)

    def test_selection_record_lives_in_its_own_list_and_rejection_queues_abort(self):
        entity, identifier = 123, 0x0D8000000000023A
        definition = graph([
            node(0, "STU_066B4605", state=0, m_00D49978=expr.Entity(entity),
                 m_D66E7D68=0, m_F2810377=expr.Asset(identifier), m_F8585ABF=False),
            node(1, "STU_CF8C7CC5", state=1, m_00D49978=expr.Entity(entity)),
            node(2, "STU_066B4605", state=2, m_00D49978=expr.Entity(entity),
                 m_D66E7D68=0, m_F2810377=expr.Asset(identifier + 1)),
        ])
        world = scene.SceneWorld()
        world.bind(entity)
        registry = component86.Registry(accepted_selections=((0, identifier),))
        world.set_component86(entity, registry)
        component = runtime.Component(entity, world=world, strict=True)
        instance = component.create(definition)
        selected, ordinary, rejected = [instance.state(index) for index in range(3)]
        with component.operation():
            selected.begin()
            ordinary.begin()
            rejected.begin()
        self.assertTrue(rejected.active)
        self.assertEqual([entry.state for entry in registry.entries], [ordinary])
        self.assertEqual([entry.state for entry in registry.selection_entries], [selected])
        component.run_frame(1)
        self.assertFalse(rejected.active)
        with component.operation():
            selected.end(False)
        self.assertEqual(registry.selection_entries, [])
        self.assertEqual([entry.state for entry in registry.entries], [ordinary])
        world.unbind(entity)
        self.assertEqual(registry.entries, [])
