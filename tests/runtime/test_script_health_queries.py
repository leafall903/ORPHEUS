"""Native health-query result/failure contract with an explicit host service."""
import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import expr, health_queries, nodes, runtime, scene


def query(cls=health_queries.CURRENT, entity=None):
    return {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"} if entity is None else entity}


class HealthWorld(runtime.World):
    def __init__(self, result=(125.5, 350.0)):
        super().__init__()
        self.result = result
        self.calls = []

    def body_health(self, entity):
        self.calls.append(entity)
        return self.result


class HealthQueryTests(unittest.TestCase):
    def instance(self, world):
        component = runtime.Component(0xA0000100, world=world)
        return component.create(make_graph(0xF001))

    def test_current_maximum_and_nested_array_entity_scalar(self):
        world = HealthWorld()
        instance = self.instance(world)
        self.assertEqual(instance.evaluate(query()), 125.5)
        self.assertEqual(instance.evaluate(query(health_queries.MAXIMUM)), 350.0)
        self.assertEqual(instance.evaluate(query(entity=((expr.Entity(0xA0000102),),))), 125.5)
        self.assertEqual(world.calls, [0xA0000100, 0xA0000100, 0xA0000102])

    def test_invalid_entity_writes_zero_without_lookup(self):
        world = HealthWorld()
        instance = self.instance(world)
        for value in (expr.Entity(0), expr.Entity(0xFFFFFFFF), None, 2, (), (None,)):
            with self.subTest(value=value):
                cfg = {"$": health_queries.CURRENT, "m_entity": value}
                self.assertEqual(instance.evaluate(cfg, initial=77), 0.0)
        self.assertEqual(world.calls, [])

    def test_missing_entity_preserves_destination_and_absent_component_writes_zero(self):
        world = HealthWorld(None)
        instance = self.instance(world)
        self.assertEqual(instance.evaluate(query(), initial=77), 77)
        world.result = (0.0, 0.0)
        self.assertEqual(instance.evaluate(query(), initial=77), 0.0)

    def test_unknown_host_service_remains_unimplemented(self):
        instance = self.instance(runtime.World())
        evaluation = instance.context(None)
        self.assertIs(health_queries.evaluate(evaluation, query()), NotImplemented)

    def test_native_dependency_poll_rechecks_changed_health(self):
        body = make_graph(0xF001, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STUStatescriptStateBooleanSwitch", state=0, fields={"m_condition": query()}),
        ], entries=[0], states=[1])
        world = HealthWorld((125, 350))
        component = runtime.Component(0xA0000100, world=world)
        instance = component.create(body)
        component.run_frame(1)
        state = instance.states[0]
        self.assertIsInstance(state, nodes.BooleanSwitch)
        self.assertTrue(state.current)
        self.assertIn(health_queries.CURRENT, state.natives)
        world.result = (0, 350)
        component.run_frame(2)
        self.assertFalse(state.current)

    def test_missing_native_classes_are_not_classified_as_health(self):
        instance = self.instance(HealthWorld())
        self.assertIs(health_queries.evaluate(instance.context(None), {"$": "STU_F7262B36"}),
                      NotImplemented)

    def test_scene_totals_require_explicit_maximum_and_follow_pool_updates(self):
        world = scene.SceneWorld()
        instance = self.instance(world)
        entity = instance.component.entity
        world.bind(entity)
        world.set_health_pool_amounts(entity, 120, 30, 10)
        self.assertIs(health_queries.evaluate(instance.context(None), query()), NotImplemented)
        world.set_health_pool_amounts(entity, 120, 30, 10, maximum=250)
        self.assertEqual(instance.evaluate(query()), 160.0)
        self.assertEqual(instance.evaluate(query(health_queries.MAXIMUM)), 250.0)
        world.set_health_pool_amounts(entity, 5, 0, 0, maximum=250)
        self.assertEqual(instance.evaluate(query()), 5.0)
        world.unbind(entity)
        self.assertEqual(instance.evaluate(query(), initial=77), 77)

    def test_filtered_query_reads_only_the_selected_explicit_pool(self):
        world = scene.SceneWorld()
        instance = self.instance(world)
        world.bind(instance.component.entity)
        world.set_health_pool_amounts(instance.component.entity, 120, 30, 10)
        cfg = query(health_queries.FILTERED_CURRENT)
        for kind, expected in enumerate((120.0, 30.0, 10.0)):
            cfg["m_7E8B666C"] = {"$": "STU_297E1A30", "m_7E8B666C": kind}
            self.assertEqual(instance.evaluate(cfg), expected)
        cfg.pop("m_7E8B666C")
        self.assertEqual(instance.evaluate(cfg), 120.0)
        cfg["m_7BE02F89"] = expr.Asset(7)
        self.assertIs(health_queries.evaluate(instance.context(None), cfg), NotImplemented)
        cfg.pop("m_7BE02F89")
        world.unbind(instance.component.entity)
        self.assertIs(health_queries.evaluate(instance.context(None), cfg), NotImplemented)
        cfg["m_entity"] = expr.Entity(0)
        self.assertEqual(instance.evaluate(cfg), 0.0)


if __name__ == "__main__":
    unittest.main()
