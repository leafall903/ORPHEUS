"""Actual graphs with declared static geometry/commit services; casts stay open."""

import unittest
from unittest.mock import patch

import test_symmetra_ultimate as ultimate_tests
from test_symmetra_combat import fire

from ow174.game.script import expr, placement
from ow174.game.script.driver import ULT_CHARGE


class SymmetraPlacementResultTests(unittest.TestCase):
    setup = ultimate_tests.SymmetraUltimateTests.setup

    def test_admitted_preview_point_is_bound_before_explicit_commit_spends_charge(self):
        run, owner, _enemy, component, instance, _ability, cost = self.setup()
        component.set(ULT_CHARGE, cost)
        result = placement.StaticResult(True, expr.Vec3(2, 1, 5))
        with (
            patch.object(owner.body_script.world, "placement_geometry", return_value=result),
            patch.object(owner.body_script.world, "confirm_placement", return_value=NotImplemented),
        ):
            fire(run, owner, 10, buttons=32)
            self.assertEqual(instance.find_var(0, 2478).value(), result.point)
            self.assertTrue(instance.find_var(0, 1002).value())
            self.assertEqual(instance.find_var(0, 12080).value(), expr.Entity(0))
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
            fire(run, owner, buttons=0)
            fire(run, owner, buttons=1)
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
            # Explicit owner admission of commit, still not recovered transport.
            state = instance.node_state(instance.graph.node(57))
            self.assertTrue(owner.body_script.world.calculate_placement(state, commit=True))
            component.run_now()
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(instance.node_state(instance.graph.node(0)).active)

    def test_admitted_invalid_geometry_preserves_preview_point_and_charge(self):
        run, owner, _enemy, component, instance, _ability, cost = self.setup()
        component.set(ULT_CHARGE, cost)
        previous = expr.Vec3(9, 8, 7)
        with patch.object(owner.body_script.world, "placement_geometry",
                          return_value=placement.StaticResult(False)):
            fire(run, owner, 10, buttons=32)
            instance.write((0, 2478), previous)  # After authored entry resets its variables.
            fire(run, owner, 3, buttons=0)
            self.assertEqual(instance.find_var(0, 2478).value(), previous)
            self.assertFalse(instance.find_var(0, 1002).value())
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
            state = instance.node_state(instance.graph.node(57))
            self.assertTrue(owner.body_script.world.calculate_placement(state, commit=True))
            component.run_now()
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)


if __name__ == "__main__":
    unittest.main()
