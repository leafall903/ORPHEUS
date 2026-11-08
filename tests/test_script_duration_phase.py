"""Explicitly reconstructed fixed phases preserve timing, aborts and strict faults."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import runtime


def phase(*, strict=False, extra=None):
    fields = {
        "m_ED97A97C": {"$": "STUConfigVarInt", "m_value": 1},
        "m_7C8AD485": {"$": "STUConfigVarBool", "m_value": 1},
        "m_onFinishedPlug": output([2, "m_inPlug"]),
        "m_onAbortPlug": output([3, "m_inPlug"]),
        **(extra or {}),
    }

    def setter(pos, key):
        return node(pos, "STUStatescriptActionSetVar", fields={
            "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": key, "m_60DB8F99": 0},
            "m_value": {"$": "STUConfigVarBool", "m_value": 1},
        })

    definition = make_graph(0xFA10, [
        node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
        node(1, "STU_54FEB21C", state=0, fields=fields), setter(2, 99), setter(3, 98),
    ], states=[1], entries=[0])
    component = runtime.Component(123, strict=strict)
    instance = component.create(definition)
    return component, instance


class FixedPhaseTests(unittest.TestCase):
    def test_literal_duration_finishes_once_after_one_second(self):
        component, instance = phase()
        component.run_frame(0)
        component.run_frame(61)
        self.assertNotIn(99, instance.vars)
        component.run_frame(62)
        self.assertTrue(instance.vars[99].value())
        self.assertFalse(instance.state(0).active)
        self.assertNotIn(98, instance.vars)

    def test_abort_cancels_the_finish_deadline(self):
        component, instance = phase()
        component.run_frame(0)
        component.run_frame(20)
        instance.state(0).request(runtime.ABORT)
        component.run_now()
        component.run_frame(100)
        self.assertTrue(instance.vars[98].value())
        self.assertNotIn(99, instance.vars)

    def test_unknown_provider_variant_does_not_manufacture_completion(self):
        component, instance = phase(extra={"m_C7A42E2A": {"$": "STU_076E0DBA"}})
        component.run_frame(0)
        component.run_frame(100)
        self.assertTrue(instance.state(0).active)
        self.assertNotIn(99, instance.vars)

    def test_strict_execution_still_rejects_reconstructed_semantics(self):
        component, _ = phase(strict=True)
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertEqual(raised.exception.diagnostic["feature"], "reconstructed duration phase")
