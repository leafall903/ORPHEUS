"""Unmodelled mission nodes cannot manufacture progression or successful frames."""

import unittest
from unittest import mock

from ow174.game.script import runtime
from tests.script_fixtures import graph, node, plug, setter


def mission(*items, strict=True, graph_index=1):
    component = runtime.Component(123, strict=strict)
    entry = node(0, "STUStatescriptEntry", m_outPlug=plug((1, "m_inPlug")))
    definition = graph([entry, *items], entries=[0])
    definition.index = graph_index
    instance = component.create(definition)
    return component, instance


class StrictMissionTests(unittest.TestCase):
    def test_delayed_boolean_switch_cancels_and_restarts_branch_timers(self):
        predicate = {"$": "STUConfigVarDynamic", "m_identifier": 9}
        component, instance = mission(
            node(1, "STU_B591EBFE", state=0, m_BD83C8E6=predicate,
                 m_7130456B={"$": "STUConfigVarFloat", "m_value": 0.06},
                 m_ED60694A={"$": "STUConfigVarFloat", "m_value": 0.04},
                 m_trueSubgraphPlug={"$": "STU_904BFCEC", "m_FF3DAF1E": [1],
                                     "links": [[2, "m_beginPlug"]]},
                 m_falseSubgraphPlug={"$": "STU_904BFCEC", "m_FF3DAF1E": [2],
                                      "links": [[3, "m_beginPlug"]]}),
            node(2, "STUStatescriptState", state=1),
            node(3, "STUStatescriptState", state=2),
        )
        component.run_frame(0)
        switch, on, off = (instance.state(i) for i in range(3))
        self.assertTrue(off.active)

        instance.write((runtime.INSTANCE, 9), True)
        component.run_frame(1)
        instance.write((runtime.INSTANCE, 9), False)
        component.run_frame(2)
        self.assertEqual(instance.timers(), [])
        self.assertFalse(on.active)

        instance.write((runtime.INSTANCE, 9), True)
        for frame in range(3, 6):
            component.run_frame(frame)
        self.assertTrue(off.active)
        for frame in (6, 7):
            component.run_frame(frame)
        self.assertTrue(on.active)
        self.assertFalse(off.active)
        self.assertEqual(switch.payload(), {"a": True, "b": False})

        instance.write((runtime.INSTANCE, 9), False)
        for frame in range(8, 11):
            component.run_frame(frame)
        self.assertFalse(on.active)
        self.assertTrue(off.active)
        self.assertEqual(switch.payload(), {"a": False, "b": False})
        switch.end(False)
        self.assertFalse(off.active)

    def test_unknown_action_does_not_follow_its_success_plug(self):
        component, instance = mission(
            node(1, "UnimplementedEncounter", m_outPlug=plug((2, "m_inPlug"))), setter(2, 99),
        )
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertNotIn(7, instance.vars)
        self.assertEqual(raised.exception.diagnostic, {
            "entity": 123, "instance": 1, "graph": 1, "frame": 0, "time_ms": 0,
            "feature": "node", "class": "UnimplementedEncounter", "node": 1,
        })
        self.assertIs(component.fault, raised.exception)
        self.assertTrue(instance.stopped)
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.run_frame(1)

    def test_legacy_body_policy_remains_permissive(self):
        component, instance = mission(
            node(1, "UnimplementedEncounter", m_outPlug=plug((2, "m_inPlug"))), setter(2, 99),
            strict=False,
        )
        component.run_frame(0)
        self.assertEqual(instance.vars[7].value(), 99)

    def test_native_trace_action_follows_output_without_evaluating_value(self):
        component, instance = mission(
            node(1, "STU_9F3FE778", m_value={"$": "UnimplementedTraceValue"},
                 m_outPlug=plug((2, "m_inPlug"))),
            setter(2, 99),
        )
        component.run_frame(0)
        self.assertEqual(instance.vars[7].value(), 99)

    def test_unknown_state_cannot_start_its_subgraph(self):
        component, instance = mission(
            node(1, "UnimplementedCutscene", state=0, m_onBeginPlug=plug((2, "m_inPlug"))),
            setter(2, 99),
        )
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertEqual(raised.exception.diagnostic['feature'], 'state')
        self.assertNotIn(7, instance.vars)

    def test_junkenstein_base_states_only_run_in_the_controller_graph(self):
        for cls in ('STU_6A6BBB8D', 'STU_918B3FA9', 'STU_14611C1B', 'STU_200B27C5',
                    'STU_D52B9026', 'STU_4531992F', 'STU_55F72184', 'STU_632B74B9'):
            with self.subTest(cls=cls):
                state_node = node(1, cls, state=0, m_onBeginPlug=plug((2, 'm_inPlug')))
                component, instance = mission(state_node, setter(2, 99), graph_index=0x0C87)
                component.run_frame(0)
                self.assertEqual(instance.vars[7].value(), 99)
                self.assertEqual(instance.state(0).payload(), {})

                component, instance = mission(state_node, setter(2, 99))
                with self.assertRaises(runtime.UnsupportedScriptError) as raised:
                    component.run_frame(0)
                self.assertEqual(raised.exception.diagnostic['class'], cls)
                self.assertNotIn(7, instance.vars)

    def test_boss_controller_base_state_is_scoped_to_the_authored_node(self):
        for cls, authored_graph, authored_position in (
            ('STU_AF1A4047', 0x0AAA, 3), ('STU_E0D2F3AA', 0x0BC6, 14), ('STU_4531992F', 0x0CB5, 20),
        ):
            for index, position in ((authored_graph, authored_position),
                                    (authored_graph, authored_position + 1), (1, authored_position)):
                with self.subTest(cls=cls, index=index, position=position):
                    model = graph([
                        node(0, 'STUStatescriptEntry', m_outPlug=plug((position, 'm_beginPlug'))),
                        node(position, cls, state=0, m_onBeginPlug=plug((5, 'm_inPlug'))),
                        setter(5, 99),
                    ], entries=[0])
                    model.index = index
                    component = runtime.Component(123, strict=True)
                    instance = component.create(model)
                    if (index, position) == (authored_graph, authored_position):
                        component.run_frame(0)
                        self.assertEqual(instance.vars[7].value(), 99)
                    else:
                        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'controller state'):
                            component.run_frame(0)
                        self.assertNotIn(7, instance.vars)

    def test_tire_wave_states_continue_with_known_absent_local_combat_context(self):
        def wave():
            model = graph([
                node(0, 'STUStatescriptEntry', m_outPlug=plug((52, 'm_beginPlug'))),
                node(52, 'STU_B5C87F3D', state=0, m_onBeginPlug=plug((60, 'm_beginPlug'))),
                node(60, 'STU_76F3EAEF', state=1),
            ], entries=[0])
            model.index = 0x0CBC
            component = runtime.Component(123, strict=True)
            return component, component.create(model)

        for available in (None, False, True):
            with self.subTest(available=available):
                component, instance = wave()
                component.world.local_combat_context_available = available
                if available is False:
                    component.run_frame(0)
                    component.run_frame(6)
                    self.assertTrue(instance.state(0).active)
                    self.assertTrue(instance.state(1).active)
                else:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'local combat context'):
                        component.run_frame(0)

    def test_presentation_states_require_a_known_absent_service(self):
        for cls, capability in (
            ('STU_F5A99B2A', 'tips_context_available'),
            ('STUStatescriptStateUXPresenter', 'ux_presenter_available'),
            ('STU_E13B30A8', 'ux_presenter_available'),
        ):
            for available in (None, False, True):
                with self.subTest(cls=cls, available=available):
                    component, instance = mission(node(1, cls, state=0))
                    setattr(component.world, capability, available)
                    if available is False:
                        component.run_frame(0)
                        self.assertTrue(instance.state(0).active)
                        instance.state(0).end(False)
                        self.assertFalse(instance.state(0).active)
                    else:
                        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'context'):
                            component.run_frame(0)

    def test_missing_plug_target_prevents_later_sibling_progress(self):
        component, instance = mission(
            node(1, "STUStatescriptActionSetVar", m_outPlug=plug((404, "x"), (2, "x"))), setter(2, 99),
        )
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertEqual(raised.exception.diagnostic['feature'], 'plug target')
        self.assertNotIn(7, instance.vars)

    def test_active_output_consumes_input_without_starting_the_state(self):
        component, instance = mission(
            node(1, 'STU_7B9E8909', state=0, m_outPlug=plug((2, 'm_inPlug'))), setter(2, 99),
        )
        component.run_frame(0)
        state = instance.state(0)
        self.assertFalse(state.active)
        self.assertNotIn(7, instance.vars)

        state.request(runtime.BEGIN)
        component.run_frame(1)
        self.assertTrue(state.active)
        self.assertNotIn(7, instance.vars)
        with component.operation():
            self.assertTrue(state.plug_enter('m_inPlug'))
        component.run_frame(2)
        self.assertEqual(instance.vars[7].value(), 99)

    def test_unknown_world_query_does_not_become_null(self):
        component, instance = mission(setter(1, 99))
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            instance.evaluate({'$': 'UnimplementedEncounterQuery'})
        self.assertEqual(raised.exception.diagnostic['feature'], 'world query')
        self.assertIsNotNone(component.fault)

    def test_deferred_handler_cannot_fall_back_to_success(self):
        component, _instance = mission(node(1, "DeferredNative", m_outPlug=plug((2, "x"))), setter(2, 99))
        with (
            mock.patch.dict(runtime.HANDLERS, DeferredNative=lambda *_args: NotImplemented),
            self.assertRaises(runtime.UnsupportedScriptError),
        ):
            component.run_frame(0)

    def test_missing_subscript_fault_clears_pending_work_and_input(self):
        component, instance = mission(node(1, 'STU_37D754C8', state=0))
        component.shots.append(('pending shot',))
        component.held.append(20)
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertEqual(raised.exception.diagnostic['feature'], 'SubScript graph')
        self.assertFalse(component.shots)
        self.assertFalse(component.held)
        self.assertFalse(instance.queue)
        self.assertFalse(any(state.active for state in instance.states.values()))

    def test_client_only_unknown_node_is_skipped(self):
        client_node = node(1, 'UnimplementedClientView', m_outPlug=plug((2, 'x')))
        client_node['client_only'] = True
        component, instance = mission(client_node, setter(2, 99))
        component.run_frame(0)
        self.assertIsNone(component.fault)
        self.assertNotIn(7, instance.vars)

    def test_subgraph_exit_skips_client_only_state_references(self):
        client_state = node(2, 'UnimplementedClientView', state=1)
        client_state['client_only'] = True
        parent = node(1, 'STUStatescriptState', state=0,
                      m_subgraphPlug=plug((2, 'm_beginPlug')) | {'m_FF3DAF1E': [1]})
        component, instance = mission(parent, client_state)
        component.run_frame(0)
        state = instance.state(0)
        self.assertTrue(state.active)
        state.end(False)
        self.assertFalse(state.active)
        self.assertNotIn(1, instance.states)


if __name__ == '__main__':
    unittest.main()
