"""Unmodelled mission nodes cannot manufacture progression or successful frames."""

import unittest
from unittest import mock

from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node, plug, setter


def mission(*items, strict=True):
    component = runtime.Component(123, strict=strict)
    entry = node(0, "STUStatescriptEntry", m_outPlug=plug((1, "m_inPlug")))
    instance = component.create(graph([entry, *items], entries=[0]))
    return component, instance


class StrictMissionTests(unittest.TestCase):
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

    def test_unknown_state_cannot_start_its_subgraph(self):
        component, instance = mission(
            node(1, "UnimplementedCutscene", state=0, m_onBeginPlug=plug((2, "m_inPlug"))),
            setter(2, 99),
        )
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertEqual(raised.exception.diagnostic['feature'], 'state')
        self.assertNotIn(7, instance.vars)

    def test_missing_plug_target_prevents_later_sibling_progress(self):
        component, instance = mission(
            node(1, "STUStatescriptActionSetVar", m_outPlug=plug((404, "x"), (2, "x"))), setter(2, 99),
        )
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_frame(0)
        self.assertEqual(raised.exception.diagnostic['feature'], 'plug target')
        self.assertNotIn(7, instance.vars)

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


if __name__ == '__main__':
    unittest.main()
