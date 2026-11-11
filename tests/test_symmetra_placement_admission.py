"""Declared placement callback inputs exercise authored server resource routing.

These are not end-to-end placement tests. Engine success/failure callbacks are
explicitly admitted; their native generation/validation remains OPEN.
"""

import unittest
from unittest.mock import patch

import test_symmetra_ultimate as ultimate_tests
from test_symmetra_combat import fire

from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE


class PlacementAdmissionTests(unittest.TestCase):
    setup = ultimate_tests.SymmetraUltimateTests.setup

    def test_scope_children_are_client_only_and_rejected_by_server_execution(self):
        run, owner, _enemy, _component, _instance, _ability, _cost = self.setup()
        fire(run, owner, 10, buttons=32)
        found = []
        for instance in owner.body_script.component.instances.values():
            if instance.graph.index not in (0x156B, 0x1571):
                continue
            for node in instance.graph.nodes:
                if node.cls != "STU_BD02E168":
                    continue
                for position, _path in node.links("m_5B10D01F"):
                    child = instance.graph.node(position)
                    found.append((instance.graph.index, node.pos, child.pos))
                    self.assertTrue(child.client_only)
                    self.assertFalse(instance.accepts(child))
        self.assertEqual(set(found), {(0x156B, 106, 61), (0x156B, 107, 97), (0x1571, 124, 84)})

    def test_unowned_confirm_waits_for_declared_success_before_charge_and_axis_assignment(self):
        for rotated in (False, True):
            with self.subTest(rotated=rotated):
                run, owner, _enemy, component, instance, _ability, cost = self.setup()
                component.set(ULT_CHARGE, cost)
                fire(run, owner, 10, buttons=32)
                if rotated:
                    fire(run, owner, 5, buttons=0)
                    fire(run, owner, 5, buttons=32)
                self.assertEqual(bool(instance.find_var(0, 4201).value()), rotated)
                fire(run, owner, buttons=0)
                # This test isolates the missing confirmation-owner branch;
                # the actual server wall owner has separate deployment tests.
                with patch.object(owner.body_script.world, 'confirm_placement', return_value=NotImplemented):
                    fire(run, owner, buttons=1)
                fire(run, owner, 5, buttons=0)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
                # Explicitly admitted original engine success output. No cast,
                # callback transport, point result or Pet actor is fabricated.
                placement = instance.graph.node(57)
                self.assertFalse(placement.client_only)
                instance.follow(placement, "m_F6955E81", instance.node_state(placement))
                component.run_now()
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(
                    instance.find_var(0, 2483).value(), expr.Vec3(1, 0, 0) if rotated else expr.Vec3(0, 0, 1)
                )
                wait = instance.node_state(instance.graph.node(0))
                self.assertTrue(wait.active)
                self.assertAlmostEqual(wait.evaluate(wait.node.field("m_timeout"), watch=False), 0.25)

    def test_declared_failure_output_keeps_charge_available(self):
        run, owner, _enemy, component, instance, _ability, cost = self.setup()
        component.set(ULT_CHARGE, cost)
        fire(run, owner, 10, buttons=32)
        with patch.object(owner.body_script.world, 'confirm_placement', return_value=NotImplemented):
            fire(run, owner, buttons=1)
        placement = instance.graph.node(57)
        instance.follow(placement, "m_425337C6", instance.node_state(placement))
        component.run_now()
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)


if __name__ == "__main__":
    unittest.main()
