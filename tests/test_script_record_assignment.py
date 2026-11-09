"""Known224E record paths: copy-on-write storage and actual pending stun rows."""

import unittest
from unittest.mock import patch

import test_game_brigitte_shield as shield_tests
from test_script_assignment_arrays import dynamic, setup

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import expr, graph, play, record_assignment, runtime


def destination(index=0, member=215):
    return {"$": "STU_919FD47C", "m_expression": {
        "m_opcodes": [8, 0, 6, 0, 17, 7, 1, 15, 0],
        "m_dynamicVars": [dynamic(7), dynamic(member)], "m_D99EF254": [index],
    }}


class RecordAssignmentTests(unittest.TestCase):
    def instance(self):
        return setup({"$": "STUConfigVarInt", "m_value": 1})[1]

    def test_new_rows_preserve_neighbors_and_previous_immutable_values(self):
        instance = self.instance()
        self.assertTrue(record_assignment.store(instance, destination(), 0.75))
        first = instance.vars[7].value()
        self.assertTrue(record_assignment.store(instance, destination(member=41), expr.Entity(123)))
        self.assertTrue(record_assignment.store(instance, destination(1), 0.8))
        current = instance.vars[7].value()
        self.assertEqual(first, (expr.Map(((215, 0.75),)),))
        self.assertEqual(current[0].get(215), 0.75)
        self.assertEqual(current[0].get(41), expr.Entity(123))
        self.assertEqual(current[1].get(215), 0.8)

    def test_dynamic_index_is_evaluated_at_assignment_time(self):
        instance = self.instance()
        cfg = destination()
        cfg["m_expression"]["m_dynamicVars"].append(dynamic(8))
        cfg["m_expression"]["m_opcodes"][2:4] = [8, 2]
        instance.write((0, 8), 0)
        record_assignment.store(instance, cfg, 10)
        instance.write((0, 8), 1)
        record_assignment.store(instance, cfg, 20)
        self.assertEqual([row.get(215) for row in instance.vars[7].value()], [10, 20])

    def test_invalid_sparse_scalar_and_foreign_entity_targets_leave_storage_unchanged(self):
        instance = self.instance()
        for value in (None, 17, expr.Entity(123), (expr.Entity(123),)):
            for index in (-1, 0, 2, 200):
                with self.subTest(value=value, index=index):
                    instance.write((0, 7), value)
                    if value is None and index == 0:
                        continue
                    self.assertFalse(record_assignment.store(instance, destination(index), 9))
                    self.assertEqual(instance.vars[7].value(), value)

    def test_malformed_and_computed_paths_do_not_change_existing_record(self):
        instance = self.instance()
        value = (expr.Map(((215, 0.75),)),)
        instance.write((0, 7), value)
        for code in ([8, 0, 6, 0, 17, 7, 1, 15], [8, -1, 0],
                     [8, 0, 6, 0, 23, 0], [8, 0, 6, 0, 17, 7, 1, 15, 0, 0]):
            cfg = destination()
            cfg["m_expression"]["m_opcodes"] = code
            self.assertFalse(record_assignment.store(instance, cfg, 99))
            self.assertEqual(instance.vars[7].value(), value)

    def test_actual_stun_start_consumes_embedded_source_duration_and_effect_record(self):
        run, source, target, _ = shield_tests.BarrierTests().fixture()
        parent = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0xEE3)
        resource = play.resolve_resource(parent, parent.graph.node(13))
        component = target.body_script.component
        child = component.play_loaded_graph(graph.graph(0x224E), source=source.body,
                                            overrides=resource.overrides, policy=1, instigator=source.body)
        component.run_now()
        row = child.vars[31528].value()[0]
        self.assertEqual(row.get(215), 0.75)
        self.assertEqual(row.get(41), expr.Entity(source.body))
        self.assertEqual(row.get(28383), expr.Asset(0x030000000000220D))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            self.assertTrue(export_component_vm(component))
        # Standalone allocation has no hit-recipient lease: the actor filter
        # remains unresolved and control is not claimed from allocation alone.
        status = component.entity_var(888, create=False)
        self.assertFalse(status.value() if status is not None else False)
        self.assertFalse(run.match.combat.brigitte.stuns)


if __name__ == "__main__":
    unittest.main()
