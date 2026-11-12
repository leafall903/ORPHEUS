"""Actual placement input with reconstructed static queries, not injected outcomes."""

import math
import unittest

from test_symmetra_combat import arena

from ow174.game.collision import World, flat_world
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE


class StaticPlacementTests(unittest.TestCase):
    def scene(self, *, world=None):
        run, owner, enemy = arena()
        world = flat_world(0) if world is None else world
        run.match.collision_world = world
        for player, position in ((owner, (0, 0, 0)), (enemy, (0, 0, 20))):
            player.mover.use(world=world)
            player.mover.position[:] = position
        owner.body_script.ult_seconds = 0
        owner.body_script.component.set(ULT_CHARGE, 1680)
        return run, owner

    def preview(self, run, owner, button, graph, node, *, yaw=0):
        for _ in range(4):
            run.step({owner: (yaw, 0, button)})
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == graph)
        state = instance.node_state(instance.graph.node(node))
        self.assertTrue(state.active)
        return instance, state

    def test_floor_preview_uses_actual_resource_limits_for_ultimate_and_teleporter(self):
        for button, graph, node in ((32, 0x156B, 57), (16, 0x1571, 69)):
            with self.subTest(graph=hex(graph)):
                run, owner = self.scene()
                _instance, state = self.preview(run, owner, button, graph, node)
                self.assertTrue(state.evaluate(state.node.field('m_2339572B'), watch=False))
                point = state.evaluate(state.node.field('m_30710B76'), watch=False)
                maximum = state.evaluate(state.node.field('m_6386E7B4'), watch=False)
                minimum = state.evaluate(state.node.field('m_00A23E4B'), watch=False)
                self.assertAlmostEqual(point.y, 0)
                distance = math.dist(tuple(point), run.match.combat._eye(owner))
                self.assertGreater(distance, maximum - 1)
                self.assertLessEqual(distance, maximum + .0001)
                self.assertGreaterEqual(distance, minimum)
                self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 1680)

    def test_actual_yaw_updates_preview_without_replacing_the_result_service(self):
        run, owner = self.scene()
        instance, _ = self.preview(run, owner, 32, 0x156B, 57)
        first = instance.find_var(0, 2478).value()
        self.preview(run, owner, 32, 0x156B, 57, yaw=16384)
        second = instance.find_var(0, 2478).value()
        self.assertGreater(first.z, 30)
        self.assertGreater(second.x, 30)
        self.assertAlmostEqual(second.z, 0, places=4)

    def test_no_ground_has_no_valid_preview_and_keeps_ultimate_charge(self):
        run, owner = self.scene(world=World([]))
        instance, _ = self.preview(run, owner, 32, 0x156B, 57)
        self.assertFalse(instance.find_var(0, 1002).value())
        run.step({owner: (0, 0, 1)})
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 1680)

    def test_wall_candidates_remain_in_front_of_the_occluding_wall(self):
        floor = [(-50, 0, -50, -50, 0, 50, 50, 0, 50), (-50, 0, -50, 50, 0, 50, 50, 0, -50)]
        wall = [(-5, 0, 3, 5, 0, 3, 5, 5, 3), (-5, 0, 3, 5, 5, 3, -5, 5, 3)]
        run, owner = self.scene(world=World(floor + wall))
        instance, _ = self.preview(run, owner, 32, 0x156B, 57)
        self.assertTrue(instance.find_var(0, 1002).value())
        point = instance.find_var(0, 2478).value()
        self.assertAlmostEqual(point.y, 0)
        self.assertLess(point.z, 3)

    def test_missing_collision_remains_an_unknown_service(self):
        run, owner = self.scene()
        _instance, state = self.preview(run, owner, 32, 0x156B, 57)
        run.match.collision_world = None
        self.assertIs(owner.body_script.world.placement_geometry(state), NotImplemented)

    def test_non_ground_material_cannot_produce_a_valid_preview(self):
        floor = [(-50, 0, -50, -50, 0, 50, 50, 0, 50), (-50, 0, -50, 50, 0, 50, 50, 0, -50)]
        run, owner = self.scene(world=World(floor, surfaces=[1, 1]))
        instance, _ = self.preview(run, owner, 32, 0x156B, 57)
        self.assertFalse(instance.find_var(0, 1002).value())

    def test_teleporter_rejects_ground_inside_its_authored_minimum_range(self):
        run, owner = self.scene(world=flat_world(0, half_size=1))
        instance, state = self.preview(run, owner, 16, 0x1571, 69)
        self.assertEqual(state.evaluate(state.node.field('m_00A23E4B'), watch=False), 4)
        self.assertFalse(instance.find_var(0, 1002).value())
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 1680)

    def test_explicit_commit_preserves_the_real_static_preview_in_the_authored_position(self):
        run, owner = self.scene()
        instance, state = self.preview(run, owner, 32, 0x156B, 57)
        point = instance.find_var(0, 2478).value()
        # The engine confirmation owner is still an explicit fixture admission.
        # Actual input confirmation and the Pet actor are separate acceptance.
        self.assertTrue(owner.body_script.world.calculate_placement(state, commit=True))
        owner.body_script.component.run_now()
        self.assertEqual(instance.find_var(0, 17085).value(), point)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_world_position_bridge_rejects_dynamic_parent_and_bone_queries(self):
        from ow174.game import symmetra_placement

        run, owner = self.scene()
        instance, _ = self.preview(run, owner, 32, 0x156B, 57)
        cfg = instance.graph.node(89).field('m_value')
        self.assertEqual(instance.evaluate(cfg), instance.find_var(0, 2478).value())
        for parent in (expr.Entity(owner.body), None, False):
            with self.subTest(parent=parent):
                instance.write((0, 1883), parent)
                self.assertIs(symmetra_placement.static_transform(owner.body_script.world, cfg, instance),
                              NotImplemented)
        instance.write((0, 1883), expr.Entity(0))
        self.assertEqual(instance.evaluate(cfg), instance.find_var(0, 2478).value())
        self.assertIs(symmetra_placement.static_transform(owner.body_script.world,
                                                        dict(cfg, m_bone=1), instance), NotImplemented)


if __name__ == '__main__':
    unittest.main()
