"""Distinct plug arrivals, completion checks and aborts for native1113EBD5."""

import unittest
from unittest.mock import patch

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import runtime
from tests.runtime.test_game_graph_analysis import make_graph, node, output
from tests.script_fixtures import export_synthetic_component_vm


class JoinTests(unittest.TestCase):
    def setup_join(self, *, linked=True, count=2):
        definition = make_graph(1, [
            node(0, "STUStatescriptState", state=0),
            node(1, "STUStatescriptState", state=1),
            node(2, "STU_1113EBD5", fields={
                "m_122DF0CA": [0, 1, 99], "m_B24D1794": count,
                "m_outPlug": output([3, "m_inPlug"]) if linked else output(),
            }),
            node(3, "STUStatescriptActionSetVar", fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 77},
                "m_value": {"$": "STUConfigVarInt", "m_value": 41},
            }),
            node(4, "STUStatescriptEntry", fields={"m_outPlug": output([2, "m_inPlug"])}),
            node(5, "STUStatescriptEntry", fields={"m_outPlug": output([2, "m_inPlug"])}),
            node(6, "STUStatescriptEntry", fields={
                "m_transitionPlug": [output([2, "m_inPlug"]), output([2, "m_inPlug"])],
            }),
            node(7, "STUStatescriptState", state=2),
        ], states=[0, 1, 7])
        component = runtime.Component(123, strict=True)
        instance = component.create(definition, init=False)
        instance.init()
        for index in (0, 1):
            state = instance.state(index)
            state.request(runtime.BEGIN)
        component.run_now()
        return component, instance

    def arrive(self, instance, producer, path="m_outPlug", caller=None):
        return instance.follow(instance.graph.node(producer), path, caller)

    def test_distinct_edges_complete_and_abort_referenced_states_once(self):
        component, instance = self.setup_join()
        self.assertFalse(self.arrive(instance, 4))
        component.run_now()
        self.assertFalse(self.arrive(instance, 4))
        self.assertEqual(len(instance.join_inputs), 1)
        self.assertTrue(self.arrive(instance, 5))
        self.assertEqual(instance.vars[77].value(), 41)
        self.assertFalse(instance.join_inputs)
        component.run_now()
        self.assertFalse(any(state.active for state in instance.states.values()))
        self.assertIsNone(instance.plug_source)

    def test_partial_arrival_rechecks_active_siblings_but_excludes_caller(self):
        component, instance = self.setup_join()
        caller = instance.state(0)
        self.assertFalse(self.arrive(instance, 4, caller=caller))
        checks = [event.state.index for event in instance.queue if event.kind == runtime.CHECK]
        self.assertEqual(checks, [1])
        self.arrive(instance, 4, caller=caller)
        self.assertEqual([event.state.index for event in instance.queue], [1])
        self.assertNotIn(99, instance.states)
        component.run_now()

    def test_two_array_plugs_from_same_producer_are_distinct(self):
        _, instance = self.setup_join()
        self.assertFalse(self.arrive(instance, 6, "m_transitionPlug[0]"))
        self.assertFalse(self.arrive(instance, 6, "m_transitionPlug[0]"))
        self.assertTrue(self.arrive(instance, 6, "m_transitionPlug[1]"))

    def test_refused_output_still_clears_arrivals_without_aborting_states(self):
        component, instance = self.setup_join(linked=False)
        self.assertFalse(self.arrive(instance, 4))
        component.run_now()
        self.assertFalse(self.arrive(instance, 5))
        self.assertFalse(instance.join_inputs)
        self.assertTrue(all(state.active for state in instance.states.values()))
        self.assertFalse(self.arrive(instance, 4))
        self.assertEqual(len(instance.join_inputs), 1)

    def test_native_single_input_count_does_not_complete_on_first_arrival(self):
        _, instance = self.setup_join(count=1)
        self.assertFalse(self.arrive(instance, 4))
        self.assertFalse(self.arrive(instance, 5))
        self.assertEqual(len(instance.join_inputs), 2)

    def test_abort_lookup_occurs_after_output_callback(self):
        component, instance = self.setup_join()
        instance.graph.node(2).fields["m_122DF0CA"] = [2]
        instance.graph.node(3).cls = "STU_TestJoinOutput"

        def output_callback(inst, node, caller):
            inst.state(2).begin()
            return True

        with patch.dict(runtime.HANDLERS, {"STU_TestJoinOutput": output_callback}):
            self.assertFalse(self.arrive(instance, 4))
            self.assertTrue(self.arrive(instance, 5))
            component.run_now()
        self.assertFalse(instance.state(2).active)

    def test_pending_arrivals_are_causal_snapshot_state_and_retire_with_instance(self):
        component, instance = self.setup_join()
        before = snapshot_digest(export_synthetic_component_vm(component))
        self.arrive(instance, 4)
        component.run_now()
        self.assertNotEqual(snapshot_digest(export_synthetic_component_vm(component)), before)
        component.destroy(instance)
        self.assertFalse(instance.join_inputs)

    def test_direct_call_without_origin_is_refused(self):
        _, instance = self.setup_join()
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.run_node(instance.graph.node(2), None)
