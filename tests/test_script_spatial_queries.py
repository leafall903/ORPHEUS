"""Spatial scalar failure/lifecycle and actual D.Va creation/placement inputs."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import expr, graph, runtime
from ow174.game.script import spatial_queries as spatial

BODY = 0xA0000101


class ValueWorld(runtime.World):
    context = NotImplemented

    def spatial_query_context(self, entity):
        return self.context

    def native(self, cfg, instance):
        if cfg.get("$") == "Literal":
            return cfg["value"]
        if cfg.get("$") == "STU_FDBB0B67":
            return 1.0  # explicit unit-sized model fixture, not a general scale implementation.
        return super().native(cfg, instance)


def setup():
    world = ValueWorld()
    component = runtime.Component(BODY, world=world, strict=True)
    instance = component.create(make_graph(0xFFFF, [node(0, state=0)], states=[0]), init=False)
    state = instance.state(0)
    events = []
    world.context = spatial.Context(BODY, True, True,
        query=lambda cls, entity: events.append((cls, entity)) or expr.Vec3(7, 8, 9))
    return world, component, instance, state, events


def config(cls, value):
    return {"$": cls, "m_entity": {"$": "Literal", "value": value}}


class SpatialQueryTests(unittest.TestCase):
    def test_unknown_world_remains_strict(self):
        world, _, instance, _, _ = setup()
        world.context = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config("STU_8CBDD81C", expr.Entity(BODY)))

    def test_unavailable_context_skips_selector_watcher_and_helper(self):
        for cls in spatial.INTERFACES:
            for instance_present, namespace in ((False, True), (True, False)):
                world, _, instance, state, events = setup()
                world.context.instance_present = instance_present
                world.context.namespace_present = namespace
                self.assertEqual(instance.evaluate({"$": cls, "m_entity": {"$": "Unknown"}},
                                                   state, initial=99), 99)
                self.assertEqual(events, [])
                self.assertNotIn(cls, state.natives)

    def test_selector_conversion_helper_failure_and_destination_preservation(self):
        for cls in spatial.INTERFACES:
            for value, identifier in ((None, 0), (BODY, 0), (True, 0), (expr.Asset(BODY), 0),
                                      (expr.Entity(BODY + (1 << 32)), BODY),
                                      (((expr.Entity(BODY),),), BODY),
                                      ((None, expr.Entity(BODY)), 0)):
                world, _, instance, state, events = setup()
                self.assertEqual(instance.evaluate(config(cls, value), state), expr.Vec3(7, 8, 9))
                self.assertEqual(events, [(cls, identifier)])
                self.assertIn(cls, state.natives)
                world.context.query = lambda *_: None
                self.assertEqual(instance.evaluate(config(cls, value), state, initial=99), 99)

    def test_absent_and_failed_selector_use_null_entity_instead_of_self(self):
        for cls in spatial.INTERFACES:
            for selector in (None, {"$": "Literal", "value": expr.EVALUATION_FAILED}):
                _, _, instance, _, events = setup()
                self.assertEqual(instance.evaluate({"$": cls, "m_entity": selector}), expr.Vec3(7, 8, 9))
                self.assertEqual(events, [(cls, 0)])

    def test_retiring_or_replacing_context_during_helper_rejects_output(self):
        for mutation in ("retire", "replace", "stop"):
            world, _, instance, _, _ = setup()

            def query(*_, mutation=mutation, world=world, instance=instance):
                if mutation == "retire":
                    world.context.retired = True
                elif mutation == "replace":
                    world.context = spatial.Context(BODY, True, True)
                else:
                    instance.component.destroy(instance)
                return expr.Vec3(1, 2, 3)

            world.context.query = query
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config("STU_8CBDD81C", expr.Entity(BODY)))

    def test_unknown_helper_and_wrong_result_type_stay_strict(self):
        for callback in (NotImplemented, lambda *_: (1, 2, 3), lambda *_: NotImplemented):
            world, _, instance, _, _ = setup()
            world.context.query = callback
            with self.assertRaises(runtime.UnsupportedScriptError):
                instance.evaluate(config("STU_8CBDD81C", expr.Entity(BODY)))

    def test_actual_dva_initial_creation_keeps_authored_vertical_offset(self):
        world, _, instance, _, events = setup()
        actual = next(n for n in graph.graph(0x26A).nodes if n.pos == 7)
        world.context.query = lambda cls, entity: events.append((cls, entity)) or expr.Vec3(10, 4, -3)
        self.assertEqual(instance.evaluate(actual.field("m_position")), expr.Vec3(10, 3.25, -3))
        self.assertEqual(events, [("STU_8CBDD81C", BODY)])

    def test_actual_call_mech_cast_uses_distinct_engine_inputs(self):
        world, _, instance, _, events = setup()
        actual = next(n for n in graph.graph(0x26A).nodes if n.pos == 38)
        values = {
            "STU_0BA27A07": expr.Vec3(10, 4, -3),
            "STU_2EDEE064": expr.Vec3(0, 0, 1),
            "STU_021D01CD": expr.Vec3(1, 0, 0),
        }
        world.context.query = lambda cls, entity: events.append((cls, entity)) or values[cls]
        cast = actual.field("m_75198C98")
        self.assertEqual(instance.evaluate(cast["m_11EFE48E"]), expr.Vec3(10, 5.5, -1.5))
        self.assertEqual(instance.evaluate(cast["m_35961153"]), expr.Vec3(0, -1, 0))
        self.assertEqual(instance.evaluate(cast["m_CDEB2148"]), expr.Vec3(1, 0, 0))
        self.assertEqual(instance.evaluate(cast["m_castLength"]), 4)
        self.assertEqual(events, [("STU_0BA27A07", BODY), ("STU_2EDEE064", BODY),
                                  ("STU_2EDEE064", BODY),
                                  ("STU_021D01CD", BODY)])


if __name__ == "__main__":
    unittest.main()
