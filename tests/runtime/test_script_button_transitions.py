"""Input must wake authored transition conditions, not only button States."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import runtime
from ow174.game.script.driver import BodyScript


class ButtonTransitionTests(unittest.TestCase):
    def fixture(self, **flags):
        definition = make_graph(0xFEE0, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, state=0, fields={"m_transitionPlug": [output([2, "m_inPlug"])]}),
            node(2, "STU_7DEB522D", fields={
                "m_logicalButton": {"$": "STUConfigVarLogicalButton", "m_logicalButton": 36},
                "m_122DF0CA": [0, 0, 1], "m_truePlug": output([3, "m_beginPlug"]),
                **flags,
            }),
            node(3, state=1),
        ], states=(1, 3), entries=(0,))
        component = runtime.Component(0xA0000101, strict=True)
        instance = component.create(definition)
        component.run_frame(100)
        return component, instance

    def test_matching_button_wakes_active_owner_once_without_allocating_inactive_owner(self):
        component, instance = self.fixture()
        self.assertNotIn(1, instance.states)
        component.run_frame(101, [(35, True)])
        self.assertTrue(instance.state(0).active)
        component.run_frame(102, [(36, True)])
        self.assertFalse(instance.state(0).active)
        self.assertTrue(instance.state(1).active)
        self.assertEqual(instance.state(1).counter, 1)
        self.assertFalse(instance.state(0).flags_check)

    def test_default_down_edge_does_not_retrigger_rearmed_wait_while_held(self):
        component, instance = self.fixture()
        component.run_frame(101, [(36, True)])
        instance.state(0).request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(instance.state(0).active)
        component.run_frame(102)
        self.assertTrue(instance.state(0).active)
        component.run_frame(103, [(36, False)])
        self.assertTrue(instance.state(0).active)
        component.run_frame(104, [(36, True)])
        self.assertFalse(instance.state(0).active)
        self.assertEqual(component.button, runtime.NO_BUTTON)

    def test_up_edge_and_disabled_condition(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled):
                component, instance = self.fixture(m_87F97379=0, m_12A75BC7=1)
                component.run_frame(101, [(36, True)])
                self.assertTrue(instance.state(0).active)
                if disabled:
                    component.disabled[36] = [instance.state(0)]
                component.run_frame(102, [(36, False)])
                self.assertEqual(instance.state(0).active, disabled)

    def test_explicit_held_flag_checks_rearmed_wait_without_an_edge(self):
        component, instance = self.fixture(m_87F97379=0, m_00994FF0=1)
        component.run_frame(101, [(36, True)])
        self.assertFalse(instance.state(0).active)
        instance.state(0).request(runtime.BEGIN)
        component.run_now()
        self.assertFalse(instance.state(0).active)

    def test_original_reaper_shadow_step_input_reaches_target_selector(self):
        body = BodyScript(0x02E0000000000002, 0xA0000101, 1000)
        body.command(1001, 0x10)
        shadow = next(i for i in body.component.instances.values() if i.graph.index == 0xF2)
        self.assertFalse(shadow.state(1).active)
        self.assertTrue(shadow.state(0).active)
        self.assertTrue(shadow.state(2).active)


if __name__ == "__main__":
    unittest.main()
