"""Proven custom-input behavior for the authored Sniper relay state."""
import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import runtime


class ActiveInputRelayTests(unittest.TestCase):
    def test_custom_input_is_consumed_inactive_and_forwards_only_while_active(self):
        definition = make_graph(1, [
            node(0, "STU_7B9E8909", state=0, fields={
                "m_inPlug": {"$": "STU_A1AAB8C4", "links": []},
                "m_outPlug": output([1, "m_inPlug"]),
            }),
            node(1, "STUStatescriptActionSetVar", fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 77},
                "m_value": {"$": "STUConfigVarInt", "m_value": 41},
            }),
            node(2, "STUStatescriptEntry", fields={"m_outPlug": output([0, "m_inPlug"])}),
            node(3, "STUStatescriptEntry", fields={"m_outPlug": output([0, "m_beginPlug"])}),
        ], states=[0])
        component = runtime.Component(123, strict=True)
        instance = component.create(definition, init=False)
        instance.init()
        state = instance.state(0)
        self.assertTrue(instance.follow(definition.node(2), "m_outPlug"))
        component.run_now()
        self.assertFalse(state.active)
        self.assertNotIn(77, instance.vars)
        self.assertTrue(instance.follow(definition.node(3), "m_outPlug"))
        component.run_now()
        self.assertTrue(state.active)
        self.assertNotIn(77, instance.vars)
        self.assertTrue(instance.follow(definition.node(2), "m_outPlug"))
        component.run_now()
        self.assertEqual(instance.vars[77].value(), 41)
        self.assertTrue(export_component_vm(component))
        state.end(False)
        instance.vars[77].base = 0
        self.assertTrue(instance.follow(definition.node(2), "m_outPlug"))
        component.run_now()
        self.assertEqual(instance.vars[77].value(), 0)
