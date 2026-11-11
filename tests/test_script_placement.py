"""104319 placement callback storage/clock dispatch; geometry remains external."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node, output
from test_symmetra_combat import arena, fire

from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.script import nodes, placement, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class PlacementWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.context = placement.Clock(1, 0, 1)
        self.replayed = False
        self.calls = []

    def placement_clock(self, state):
        return self.context

    def replay_placement(self, state, frame):
        self.calls.append(("replay", frame))
        return self.replayed

    def calculate_placement(self, state, *, commit):
        self.calls.append(("calculate", commit))
        return True


class PlacementTests(unittest.TestCase):
    def fixture(self, world=None, *, strict=True):
        definition = make_graph(
            0xFA01,
            [
                node(0, "STU_4C96470A", state=0, fields={"m_F6955E81": output((1, "m_inPlug"))}),
                node(
                    1,
                    "STUStatescriptActionSetVar",
                    fields={
                        "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 99},
                        "m_value": {"$": "STUConfigVarInt", "m_value": 7},
                    },
                ),
            ],
            states=(0,),
        )
        component = runtime.Component(123, world=world or PlacementWorld(), strict=strict)
        instance = component.create(definition)
        state = instance.state(0)
        self.assertIsInstance(state, nodes.Placement)
        state.begin()
        component.run_now()
        return component, instance, state

    def test_clock_rejects_invalid_native_word_and_divisor_inputs(self):
        for values in (
            (True, 0, 1),
            (-1, 0, 1),
            (1 << 32, 0, 1),
            (0, True, 1),
            (0, 1 << 63, 1),
            (0, 0, 0),
            (0, 0, -1),
            (0, 0, True),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                placement.Clock(*values)

    def test_first_callback_bypasses_phase_then_signed_remainder_gates_updates(self):
        world = PlacementWorld()
        component, _instance, state = self.fixture(world)
        world.context = placement.Clock(5, 0, 3)
        component.run_frame(1)
        self.assertEqual(world.calls, [("replay", 5), ("calculate", False)])
        world.calls.clear()
        for frame, clock in ((6, 1), (7, -1)):
            world.context = placement.Clock(frame, clock, 3)
            component.run_frame(frame)
            self.assertEqual(state.last_frame, 5)
            self.assertFalse(world.calls)
        world.context = placement.Clock(8, 2, 3)
        component.run_frame(8)
        self.assertEqual(state.last_frame, 8)
        self.assertEqual(world.calls, [("replay", 8), ("calculate", False)])

    def test_replay_suppresses_calculation_without_inventing_success_output(self):
        world = PlacementWorld()
        world.replayed = True
        component, instance, state = self.fixture(world)
        component.run_frame(1)
        self.assertEqual(world.calls, [("replay", 1)])
        self.assertIsNone(instance.find_var(0, 99).value())
        self.assertTrue(state.active)

    def test_unsigned_high_water_and_reactivation_match_server_fields(self):
        world = PlacementWorld()
        component, _instance, state = self.fixture(world)
        state.latest_frame = 20
        world.context = placement.Clock(5, 0, 1)
        component.run_frame(1)
        self.assertEqual((state.last_frame, state.latest_frame), (5, 20))
        state.end(False)
        state.begin()
        self.assertEqual((state.last_frame, state.latest_frame), (0xFFFFFFFF, 20))
        world.context = placement.Clock(0xFFFFFFFF, 0, 1)
        component.run_frame(2)
        self.assertEqual(state.latest_frame, 0xFFFFFFFF)
        world.context = placement.Clock(3, 0, 1)
        component.run_frame(3)
        self.assertEqual((state.last_frame, state.latest_frame), (3, 3))

    def test_strict_missing_clock_and_geometry_fail_closed(self):
        for world, feature in (
            (runtime.World(), "placement clock"),
            (PlacementWorld(), "placement geometry"),
        ):
            if isinstance(world, PlacementWorld):
                world.calculate_placement = lambda state, *, commit: NotImplemented
            component, _instance, _state = self.fixture(world)
            with self.subTest(feature=feature):
                with self.assertRaises(runtime.UnsupportedScriptError) as error:
                    component.run_frame(1)
                self.assertEqual(error.exception.diagnostic["feature"], feature)

    def test_permissive_missing_geometry_does_not_emit_success(self):
        world = PlacementWorld()
        world.calculate_placement = lambda state, *, commit: NotImplemented
        component, instance, state = self.fixture(world, strict=False)
        component.run_frame(1)
        self.assertEqual((state.last_frame, state.latest_frame), (1, 1))
        self.assertIsNone(instance.find_var(0, 99).value())
        self.assertTrue(state.active)

    def test_actual_ultimate_input_runs_the_callback_without_fake_geometry_admission(self):
        run, owner, _enemy = arena()
        script = owner.body_script
        script.ult_seconds = 0
        fire(run, owner, buttons=0)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        with patch.object(
            script.world, "calculate_placement", wraps=script.world.calculate_placement
        ) as calls:
            fire(run, owner, 10, buttons=32)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x156B)
        state = instance.node_state(instance.graph.node(57))
        self.assertIsInstance(state, nodes.Placement)
        self.assertNotEqual(state.last_frame, 0xFFFFFFFF)
        self.assertEqual(state.last_frame, state.latest_frame)
        self.assertGreater(calls.call_count, 0)
        self.assertTrue(all(call.kwargs == {"commit": False} for call in calls.call_args_list))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 1680)

    def test_placement_frame_fields_are_causal_read_only_vm_storage(self):
        run, owner, _enemy = arena()
        script = owner.body_script
        script.ult_seconds = 0
        fire(run, owner, buttons=0)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        fire(run, owner, 5, buttons=32)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x156B)
        state = instance.node_state(instance.graph.node(57))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(export_body_vm(script))
            state.latest_frame += 1
            self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)


if __name__ == "__main__":
    unittest.main()
