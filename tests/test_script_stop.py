"""Queued native22926252 termination, needed by authored support status timers."""

import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node, output

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, graph, nodes, runtime  # noqa: F401 -- register handlers


def integer(value):
    return {"$": "STUConfigVarInt", "m_value": value}


class StopScriptTests(unittest.TestCase):
    def setup_instance(self, target=None):
        definition = make_graph(1, [
            node(0, "STUStatescriptStateWait", state=0, fields={"m_timeout": integer(4)}),
            node(1, "STU_22926252", fields={
                "m_67B81D25": target, "m_outPlug": output([2, "m_inPlug"]),
            }),
            node(2, "STUStatescriptActionSetVar", fields={
                "m_out_Var": {"$": "STUConfigVarDynamic", "m_identifier": 77, "m_60DB8F99": 1},
                "m_value": integer(41),
            }),
        ], states=[0])
        component = runtime.Component(123, strict=True)
        instance = component.create(definition)
        component.run_now()
        instance.state(0).request(runtime.BEGIN)
        component.run_now()
        return component, instance

    def test_null_or_zero_selects_self_and_output_runs_before_queued_teardown(self):
        for cfg in (None, integer(0)):
            with self.subTest(cfg=cfg):
                component, instance = self.setup_instance(cfg)
                instance.run_node(instance.graph.node(1), None)
                self.assertIn(instance.id, component.instances)
                self.assertTrue(instance.state(0).active)
                self.assertEqual(component.entity_var(77).value(), 41)
                self.assertTrue(any(event.kind == runtime.STOP for event in instance.queue))
                component.run_now()
                self.assertNotIn(instance.id, component.instances)
                self.assertTrue(instance.stopped)
                self.assertFalse(instance.state(0).active)
                self.assertFalse(instance.queue)

    def test_nonzero_id_truncates_to_uint16_and_stops_only_selected_instance(self):
        component, source = self.setup_instance(integer(0x10002))
        target = component.create(make_graph(2), instance_id=2)
        component.run_now()
        source.run_node(source.graph.node(1), None)
        self.assertFalse(source.stopped)
        self.assertEqual([e.kind for e in target.queue], [runtime.STOP])
        component.run_now()
        self.assertIn(source.id, component.instances)
        self.assertNotIn(target.id, component.instances)
        self.assertTrue(source.state(0).active)

    def test_missing_nonzero_or_wrapped_zero_id_does_not_fall_back_to_self(self):
        for value in (99, 0x10000, -1):
            with self.subTest(value=value):
                component, instance = self.setup_instance(integer(value))
                instance.run_node(instance.graph.node(1), None)
                component.run_now()
                self.assertIn(instance.id, component.instances)
                self.assertTrue(instance.state(0).active)
                self.assertEqual(component.entity_var(77).value(), 41)

    def test_failed_instance_config_uses_native_zero_fallback(self):
        component, instance = self.setup_instance({"$": "STU_7BD2CA19", "m_resourceKey": None})
        instance.run_node(instance.graph.node(1), None)
        component.run_now()
        self.assertTrue(instance.stopped)

    def test_pending_stop_is_causal_snapshot_state_without_executing_it(self):
        component, instance = self.setup_instance()
        before = snapshot_digest(export_component_vm(component))
        instance.run_node(instance.graph.node(1), None)
        pending = snapshot_digest(export_component_vm(component))
        self.assertNotEqual(before, pending)
        self.assertFalse(instance.stopped)
        self.assertTrue(instance.state(0).active)
        self.assertEqual(pending, snapshot_digest(export_component_vm(component)))

    def test_stop_destroys_owned_subgraph_instances_and_cancels_their_timers(self):
        component, instance = self.setup_instance()
        child = component.create(instance.graph, parent=instance, parent_state=0)
        component.run_now()
        child.state(0).request(runtime.BEGIN)
        component.run_now()
        instance.run_node(instance.graph.node(1), None)
        component.run_now()
        self.assertTrue(child.stopped)
        self.assertFalse(child.queue)
        self.assertFalse(component.instances)


STATUS_BUNDLE = Path("D:/overwatch 1.7.4/verification/hero-lanes/0004/research") / (
    "game_graphs_support_status_174.json.gz"
)


class StatusWorld(runtime.World):
    """Explicit predicate/neutral-stat adapters; not native faction/status parity."""

    def native(self, cfg, instance):
        cls = cfg.get("$")
        if cls == "STU_AC829876":
            return cfg.get("m_filter", {}).get("m_84065FD1") == 1
        if cls == "STU_D3EB8D87":
            return False
        if cls == "STU_4049CF77":
            return instance.evaluate(cfg.get("m_A53100D5"))
        if cls == "STU_E72F6348":
            return True
        return super().native(cfg, instance)


@unittest.skipUnless(STATUS_BUNDLE.is_file(), "explicit extracted Ana/support bundle required")
class AuthoredStatusStopTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(graph, "GRAPHS_PATH", STATUS_BUNDLE))
        self.clear_graphs()
        self.addCleanup(self.clear_graphs)

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def check_lifetime(self, index, state, multiplier):
        component = runtime.Component(0xA0000001, world=StatusWorld())
        instance = component.play_loaded_graph(
            graph.graph(index), source=0xA0000002,
            params=((0x0A4000000000000A, expr.Entity(0xA0000002)),), instigator=0xA0000002,
        )
        component.run_frame(0)  # establish the invocation's actual timer origin
        component.run_frame(10)
        modifier = instance.state(state)
        self.assertTrue(modifier.active)
        self.assertEqual(modifier.evaluate(modifier.node.field("m_50530EAB")), multiplier)
        component.run_frame(249)
        self.assertTrue(modifier.active)
        self.assertFalse(instance.stopped)
        component.run_frame(250)
        self.assertTrue(instance.stopped)
        self.assertFalse(modifier.active)
        self.assertFalse(instance.queue)
        self.assertNotIn(instance.id, component.instances)

    def test_original_friendly_grenade_modifier_expires_via_authored_four_second_stop(self):
        self.check_lifetime(0x0B99, 4, 1.5)

    def test_original_hostile_grenade_modifier_expires_via_authored_four_second_stop(self):
        self.check_lifetime(0x0C72, 8, 0)


if __name__ == "__main__":
    unittest.main()
