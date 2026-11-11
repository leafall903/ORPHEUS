"""StopAbility preserves native deferred timer3 ordering and instance isolation."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import nodes, runtime


class StopAbilityTests(unittest.TestCase):
    def definition(self):
        return make_graph(0xFEE2, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STUStatescriptStateAbility", state=0, fields={
                "m_A5F1A77B": {"$": "STUConfigVarLogicalButton", "m_logicalButton": 35},
                "m_B969AFC9": {"$": "STU_904BFCEC", "m_FF3DAF1E": [1],
                                "links": [[2, "m_beginPlug"]]},
            }),
            node(2, state=1),
            node(3, "STU_187D5125", fields={"m_outPlug": output([4, "m_inPlug"])}),
            node(4, "STUStatescriptActionSetVar", fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 900, "m_60DB8F99": 0},
                "m_value": {"$": "STUConfigVarInt", "m_value": 1},
            }),
        ], states=(1, 2), entries=(0,))

    def test_outgoing_plug_runs_before_stop_and_another_instance_keeps_its_ability(self):
        component = runtime.Component(0xA0000101, strict=True)
        first, other = (component.create(self.definition()) for _ in range(2))
        component.run_frame(100)
        component.run_frame(101, [(35, True)])
        ability = first.state(0)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertTrue(first.state(1).active)
        first.run_node(first.graph.node(3), None)
        self.assertEqual(first.vars[900].value(), 1)
        self.assertTrue(first.state(1).active)  # stop is queued, not inline
        component.run_now()
        self.assertFalse(ability.flags & (nodes.Ability.RUNNING | nodes.Ability.PUSHED))
        self.assertFalse(first.state(1).active)
        self.assertTrue(other.state(1).active)
        self.assertTrue(other.state(0).flags & nodes.Ability.RUNNING)
        # Held input cannot instantly restart the stopped ability; a fresh edge can.
        component.run_frame(102, [(35, False)])
        component.run_frame(103, [(35, True)])
        self.assertTrue(first.state(1).active)
        self.assertEqual(first.state(1).counter, 2)

    def test_state_index_order_skips_nonabilities_and_does_not_allocate_missing_states(self):
        definition = make_graph(0xFEE3, [
            node(0, "STUStatescriptStateAbility", state=0),
            node(1, state=1),
            node(2, "STUStatescriptStateAbility", state=2),
            node(3, "STUStatescriptStateAbility", state=3),
            node(4, "STU_187D5125"),
        ], states=(0, 1, 2, 3), entries=())
        component = runtime.Component(0xA0000101, strict=True)
        instance = component.create(definition, init=False)
        for index in (2, 1, 0):
            instance.state(index)
        instance.run_node(definition.node(4), None)
        self.assertEqual(list(instance.states), [2, 1, 0])
        self.assertEqual([(e.state.index, e.kind, e.param, e.time) for e in instance.queue],
                         [(0, runtime.TIMER, 3, 0), (2, runtime.TIMER, 3, 0)])


if __name__ == "__main__":
    unittest.main()
