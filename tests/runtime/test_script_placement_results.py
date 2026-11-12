"""Admitted static geometry uses native result order; no cast or transport proof."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import expr, nodes, placement, runtime


def variable(index):
    return {"$": "STUConfigVarDynamic", "m_identifier": index}


class ResultWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.result = placement.StaticResult(True, expr.Vec3(2, 3, 4))
        self.context = placement.Clock(17, 17, 1)
        self.history = True
        self.events = []
        self.recorded = []

    def placement_geometry(self, state):
        self.events.append("geometry")
        return self.result

    def placement_clock(self, state):
        self.events.append("frame")
        return self.context

    def record_placement(self, state, value):
        self.events.append("history")
        self.recorded.append((value, tuple(state.instance.find_var(0, i).value() for i in (1, 2, 3))))
        return self.history


class PlacementResultTests(unittest.TestCase):
    def fixture(self, *, strict=True):
        fields = {
            "m_2FECD1E3": variable(1), "m_30710B76": variable(2), "m_2339572B": variable(3),
            "m_F6955E81": output((1, "m_inPlug")), "m_425337C6": output((2, "m_inPlug")),
        }
        actions = [node(0, "STU_4C96470A", state=0, fields=fields)]
        for position, value in ((1, 1), (2, -1)):
            actions.append(node(position, "STUStatescriptActionSetVar", fields={
                "m_out_Var": variable(90), "m_value": {"$": "STUConfigVarInt", "m_value": value},
            }))
        world = ResultWorld()
        component = runtime.Component(123, world=world, strict=strict)
        instance = component.create(make_graph(0xFA02, actions, states=(0,)), init=False)
        state = instance.state(0)
        self.assertIsInstance(state, nodes.Placement)
        for index, value in ((1, expr.Entity(77)), (2, expr.Vec3(8, 9, 10)), (3, True)):
            instance.write((0, index), value)
        return world, instance, state

    def test_static_preview_writes_then_records_without_following_confirmation(self):
        world, instance, state = self.fixture()
        self.assertTrue(world.calculate_placement(state, commit=False))
        self.assertEqual(world.events, ["geometry", "frame", "history"])
        value, seen = world.recorded[0]
        self.assertEqual(value, placement.Record(17, expr.Entity(0), expr.Asset(0), expr.Vec3(2, 3, 4), True))
        self.assertEqual(seen, (expr.Entity(0), expr.Vec3(2, 3, 4), True))
        self.assertIsNone(instance.find_var(0, 90).value())

    def test_explicit_commit_forwards_only_after_history_acceptance(self):
        world, instance, state = self.fixture()
        self.assertTrue(world.calculate_placement(state, commit=True))
        self.assertEqual(instance.find_var(0, 90).value(), 1)
        self.assertEqual(len(world.recorded), 1)

    def test_failure_retains_point_entity_and_records_default_failure_before_output(self):
        world, instance, state = self.fixture()
        world.result = placement.StaticResult(False, expr.Vec3(50, 60, 70))
        self.assertTrue(world.calculate_placement(state, commit=True))
        value, seen = world.recorded[0]
        self.assertEqual(seen, (expr.Entity(77), expr.Vec3(8, 9, 10), False))
        self.assertEqual(value, placement.Record(
            17, expr.Entity(0), expr.Asset(0), expr.Vec3(0, 0, 0), False))
        self.assertEqual(instance.find_var(0, 90).value(), -1)

    def test_unknown_geometry_and_known_missing_owner_do_not_publish_results(self):
        for result in (NotImplemented, None):
            with self.subTest(result=result):
                world, instance, state = self.fixture()
                world.result = result
                returned = world.calculate_placement(state, commit=True)
                self.assertIs(returned, NotImplemented if result is NotImplemented else True)
                self.assertEqual(world.events, ["geometry"])
                self.assertEqual(instance.find_var(0, 2).value(), expr.Vec3(8, 9, 10))
                self.assertIsNone(instance.find_var(0, 90).value())

    def test_unknown_clock_or_history_stops_before_confirmation_in_both_vm_modes(self):
        for strict in (False, True):
            for missing in ("clock", "history"):
                with self.subTest(strict=strict, missing=missing):
                    world, instance, state = self.fixture(strict=strict)
                    if missing == "clock":
                        world.context = NotImplemented
                    else:
                        world.history = NotImplemented
                    if strict:
                        with self.assertRaises(runtime.UnsupportedScriptError) as error:
                            world.calculate_placement(state, commit=True)
                        expected = ("placement result clock" if missing == "clock"
                                    else "placement history recording")
                        self.assertEqual(error.exception.diagnostic["feature"], expected)
                    else:
                        self.assertIs(world.calculate_placement(state, commit=True), NotImplemented)
                    self.assertIsNone(instance.find_var(0, 90).value())
                    self.assertEqual(instance.find_var(0, 2).value(), expr.Vec3(2, 3, 4))

    def test_wrong_provider_shapes_and_nonfinite_static_points_are_rejected(self):
        world, _instance, state = self.fixture(strict=False)
        world.result = True
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            world.calculate_placement(state, commit=True)
        self.assertEqual(error.exception.diagnostic["feature"], "placement result shape")
        for valid, point in ((1, expr.Vec3(0, 0, 0)), (True, (0, 0, 0)),
                             (True, expr.Vec3(True, 0, 0)), (True, expr.Vec3(float("nan"), 0, 0)),
                             (True, expr.Vec3(float("inf"), 0, 0)), (True, expr.Vec3(1e80, 0, 0)),
                             (True, expr.Vec3(10**1000, 0, 0))):
            with self.subTest(valid=valid, point=point), self.assertRaises(ValueError):
                placement.StaticResult(valid, point)


if __name__ == "__main__":
    unittest.main()
