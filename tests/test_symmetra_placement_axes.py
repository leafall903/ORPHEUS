"""Native basis-row queries on actual owned Symmetra bodies and placement graphs."""

import math
import unittest
from types import SimpleNamespace

from test_symmetra_combat import arena, fire

from ow174.game import symmetra
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class PlacementAxisTests(unittest.TestCase):
    def fixture(self, graph_index=0x156B):
        run, owner, enemy = arena()
        fire(run, owner, buttons=0)
        component = owner.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == graph_index)
        return run, owner, enemy, instance

    def query(self, owner, instance, cls, target=None):
        cfg = {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"} if target is None else target}
        return owner.body_script.world.native(cfg, instance)

    def assert_vector(self, value, expected):
        self.assertIsInstance(value, expr.Vec3)
        for actual, wanted in zip((value.x, value.y, value.z), expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=6)

    def test_both_authored_placement_graphs_read_distinct_body_axes_at_cardinal_yaws(self):
        for graph_index in (0x156B, 0x1571):
            run, owner, _enemy, instance = self.fixture(graph_index)
            for yaw, forward, lateral in (
                (0, (0, 0, 1), (1, 0, 0)),
                (16384, (1, 0, 0), (0, 0, -1)),
                (-16384, (-1, 0, 0), (0, 0, 1)),
                (-32768, (0, 0, -1), (-1, 0, 0)),
            ):
                with self.subTest(graph=graph_index, yaw=yaw):
                    run.step({owner: (yaw, 0, 0)})
                    self.assert_vector(self.query(owner, instance, "STU_021D01CD"), forward)
                    self.assert_vector(self.query(owner, instance, "STU_66F3B89E"), lateral)

    def test_actual_ultimate_input_populates_its_authored_axis_stack_from_current_pose(self):
        run, owner, _enemy, instance = self.fixture()
        script = owner.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for yaw, lateral in ((0, (1, 0, 0)), (16384, (0, 0, -1)), (-16384, (0, 0, 1))):
            for _ in range(4):
                run.step({owner: (yaw, 0, 32)})
            self.assert_vector(instance.find_var(0, 3192).value(), lateral)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 1680)
        # This stack proves owned pose input; it does not prove the downstream
        # BD02E168 host callbacks or creation/orientation of a barrier actor.

    def test_pitch_does_not_turn_body_basis_into_a_camera_forward_query(self):
        run, owner, _enemy, instance = self.fixture()
        for pitch in (0, 8000, -8000):
            run.step({owner: (0, pitch, 0)})
            self.assert_vector(self.query(owner, instance, "STU_021D01CD"), (0, 0, 1))
            self.assert_vector(self.query(owner, instance, "STU_66F3B89E"), (1, 0, 0))

    def test_view_forward_uses_actual_yaw_and_pitch_while_body_axes_stay_planar(self):
        run, owner, _enemy, instance = self.fixture(0x1571)
        for yaw, pitch in ((0, 8000), (16384, -8000), (-16384, 4000), (8192, 0)):
            with self.subTest(yaw=yaw, pitch=pitch):
                run.step({owner: (yaw, pitch, 0)})
                angle = yaw * math.tau / 65536
                tilt = pitch * math.tau / 65536
                self.assert_vector(
                    self.query(owner, instance, "STU_2200A66D"),
                    (math.sin(angle) * math.cos(tilt), -math.sin(tilt), math.cos(angle) * math.cos(tilt)),
                )
                self.assertEqual(self.query(owner, instance, "STU_021D01CD").y, 0)

    def test_unknown_graph_entities_and_classes_remain_unsupported(self):
        _run, owner, enemy, instance = self.fixture()
        world = owner.body_script.world
        foreign = {"$": "STU_66F3B89E", "m_entity": {"$": "STUConfigVarInt", "m_value": enemy.body}}
        self.assertIs(symmetra.placement_direction_query(world, foreign, instance), NotImplemented)
        self.assertIs(
            symmetra.placement_direction_query(world, {"$": "STU_71A87E92"}, instance), NotImplemented
        )
        other = SimpleNamespace(graph=SimpleNamespace(index=0x157C))
        self.assertIs(symmetra.placement_direction_query(world, {"$": "STU_66F3B89E"}, other), NotImplemented)

    def test_retired_dead_unspawned_and_invalid_pose_inputs_are_not_fabricated(self):
        run, owner, enemy, instance = self.fixture()
        world = owner.body_script.world
        cfg = {"$": "STU_021D01CD", "m_entity": {"$": "STU_B5A0CAF0"}}
        for field in ("gone", "has_body", "spawned"):
            before = getattr(owner, field)
            setattr(owner, field, field == "gone")
            self.assertIs(symmetra.placement_direction_query(world, cfg, instance), NotImplemented)
            setattr(owner, field, before)
        for yaw in (math.nan, math.inf, 32769, 1e308):
            owner.mover.yaw = yaw
            self.assertIs(symmetra.placement_direction_query(world, cfg, instance), NotImplemented)
        owner.mover.yaw = 0
        old_pitch = owner.mover.pitch
        owner.mover.pitch = math.nan
        view = {"$": "STU_2200A66D", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertIs(symmetra.placement_direction_query(world, view, instance), NotImplemented)
        owner.mover.pitch = old_pitch
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        self.assertIs(symmetra.placement_direction_query(world, cfg, instance), NotImplemented)


if __name__ == "__main__":
    unittest.main()
