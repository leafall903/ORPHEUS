"""Separate NPC controller identity, live foreign-variable watches and cleanup."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node, output

from ow174.game.body_vm_snapshot import (
    BodyVMSnapshotError,
    export_component_vm,
    export_component_vm_with_identities,
)
from ow174.game.script import expr, play, runtime
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import OWNER_HEALTH
from ow174.game.script.npc_context import NpcContext


class NpcContextTests(unittest.TestCase):
    def test_play_request_context_uses_each_bound_vm_and_inherited_source(self):
        context, body, root = self.context()
        for component in (body, root):
            instance = component.create(make_graph(1), init=False)
            source_node = node(0, "STUStatescriptActionPlayScript")
            # Node fixtures are serialized dictionaries; the native context
            # only needs a diagnostic node when a lifecycle check rejects.
            source_node = make_graph(2, [source_node]).node(0)
            self.assertEqual(component.world.play_script_context(instance, source_node),
                             play.InvocationContext(component.entity, component.entity, 0))
            instance.set_source_entity(999)
            self.assertEqual(component.world.play_script_context(instance, source_node),
                             play.InvocationContext(component.entity, component.entity, 999))
            component.owner_dead = True
            self.assertEqual(component.world.play_script_context(instance, source_node),
                             play.InvocationContext(component.entity, component.entity, 999))
        self.assertFalse(context.retired)

    def test_play_request_context_rejects_foreign_or_stopped_source(self):
        for invalid in ("foreign", "stopped", "retired", "detached_world"):
            with self.subTest(invalid=invalid):
                context, body, root = self.context()
                component = runtime.Component(body.entity, world=body.world) if invalid == "foreign" else body
                instance = component.create(make_graph(1, [node(0, "STUStatescriptActionPlayScript")]),
                                            init=False)
                if invalid == "stopped":
                    component.destroy(instance)
                elif invalid == "retired":
                    context.retire()
                elif invalid == "detached_world":
                    body.world.control_context = None
                with self.assertRaises(runtime.UnsupportedScriptError):
                    context.play_script_context(instance, instance.graph.node(0))
                self.assertIsNone(root.fault)
                if invalid == "foreign":
                    self.assertIsNone(body.fault)

    def test_play_request_context_revalidates_after_effective_source_callback(self):
        context, body, root = self.context()
        instance = body.create(make_graph(1, [node(0, "STUStatescriptActionPlayScript")]), init=False)

        def detach():
            body.destroy(instance)
            return 999

        with (patch.object(instance, "source_entity", side_effect=detach),
              self.assertRaises(runtime.UnsupportedScriptError) as rejected):
            body.world.play_script_context(instance, instance.graph.node(0))
        self.assertEqual(rejected.exception.diagnostic["feature"], "NPC PlayScript source lifecycle")
        self.assertIsNone(root.fault)
        self.assertFalse(context.retired)

    def context(self):
        world = BotWorld(1, 1, {"0": 20})
        body = runtime.Component(100, world=world)
        context = NpcContext(100, 101, 0x040000000000227A, body)
        world.control_context = context
        root_world = BotWorld(1, 1, {"0": 20})
        root_world.control_context = context
        root = context.controller_component = runtime.Component(101, world=root_world)
        return context, body, root

    def test_known_pair_and_unknown_predicates_are_not_conflated(self):
        context, body, root = self.context()
        instance = body.create(make_graph(1), init=False)
        owner = {"$": "STU_B5A0CAF0"}
        self.assertEqual(instance.evaluate({"$": "STU_F7262B36", "m_801B298F": owner}), expr.Entity(101))
        root_instance = root.create(make_graph(2), init=False)
        self.assertEqual(root_instance.evaluate({"$": "STU_0AC15F78", "m_entity": owner}), expr.Entity(100))
        self.assertIs(context.native({"$": "STU_B96D3A98", "m_entity": owner}, instance), NotImplemented)
        self.assertIs(context.variable(999, 9134), NotImplemented)
        self.assertIs(body.world.control_context, root.world.control_context)

    def test_primary_possessor_uses_declared_pair_and_retired_typed_invalid(self):
        context, body, root = self.context()
        cfg = {"$": "STU_DB742151", "m_entity": {"$": "STU_B5A0CAF0"}}
        instances = [component.create(make_graph(index), init=False)
                     for index, component in enumerate((body, root), 1)]
        for instance in instances:
            self.assertEqual(instance.evaluate(cfg), expr.Entity(101))
            self.assertEqual(instance.evaluate({"$": "STU_DB742151"}), expr.Entity(0))
            self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": {
                "$": "STU_06610EBC", "m_configVars": [
                    {"$": "STU_B5A0CAF0"}, {"$": "STUConfigVarInt", "m_value": 999}]}}), expr.Entity(101))
            self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": {
                "$": "STU_06610EBC", "m_configVars": [
                    {"$": "unknown_failed_selector"}, {"$": "STU_B5A0CAF0"}]}}), expr.Entity(0))
        foreign = runtime.Component(999, world=body.world).create(make_graph(3), init=False)
        self.assertIs(context.native(cfg, foreign), NotImplemented)
        context.retire()
        for instance in instances:
            self.assertEqual(instance.evaluate(cfg), expr.Entity(0))

    def test_manual_target_is_only_an_authored_override_input(self):
        context, _, root = self.context()
        context.set_target(102)
        self.assertEqual(context.target_entity, 102)
        self.assertEqual(root.vars[14365].value(), expr.Entity(102))
        self.assertNotIn(1650, root.vars)
        for invalid in (True, -1, 1 << 32, 100, 101):
            with self.subTest(target=invalid), self.assertRaises(ValueError):
                context.set_target(invalid)
        context.set_target()
        self.assertEqual(root.vars[14365].value(), expr.Entity(0))
        context.retire()
        context.set_target(102)
        self.assertEqual(context.target_entity, 0)
        self.assertEqual(root.vars[14365].value(), expr.Entity(0))

    def test_foreign_variable_change_notifies_body_watcher(self):
        context, body, root = self.context()
        cfg = {"$": "STU_919FD47C", "m_expression": {"$": "STUConfigVarExpressionData",
               "m_opcodes": [14, 0, 7, 0, 15, 0], "m_dynamicVars": [{"$": "STUConfigVarDynamic",
               "m_identifier": 9134, "m_60DB8F99": 0}]}, "m_configVars": [
               {"$": "STU_F7262B36", "m_801B298F": {"$": "STU_B5A0CAF0"}}]}
        flag = {"$": "STUConfigVarDynamic", "m_identifier": 602, "m_60DB8F99": 1}
        definition = make_graph(1, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STUStatescriptStateBooleanSwitch", state=0, fields={
                "m_condition": cfg, "m_onTruePlug": output([2, "m_inPlug"]),
                "m_onFalsePlug": output([3, "m_inPlug"])}),
            node(2, "STUStatescriptActionSetVar", fields={"m_out_Var": flag,
                 "m_value": {"$": "STUConfigVarInt", "m_value": 1}}),
            node(3, "STUStatescriptActionSetVar", fields={"m_out_Var": flag,
                 "m_value": {"$": "STUConfigVarInt", "m_value": 0}}),
        ], states=[1], entries=[0])
        root.set(9134, False)
        instance = body.create(definition)
        body.run_now()
        self.assertEqual(body.vars[602].value(), 0)
        self.assertIn(instance.state(0), root.vars[9134].watchers)
        root.set(9134, True)
        self.assertTrue(instance.state(0).flags_changed)
        body.run_now()
        self.assertEqual(body.vars[602].value(), 1)
        context.retire()
        body.run_frame(1)
        self.assertEqual(body.vars[602].value(), 0)

    def test_health_is_live_pool_input_and_retirement_is_idempotent(self):
        context, body, root = self.context()
        instance = body.create(make_graph(1), init=False)
        cfg = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        body.set(OWNER_HEALTH, 350.0)
        self.assertEqual(instance.evaluate(cfg), 350)
        body.set(OWNER_HEALTH, 0.0)
        self.assertEqual(instance.evaluate(cfg), 0)
        root.create(make_graph(2))
        context.retire()
        context.retire()
        context.advance(100, (4, 5, 6))
        self.assertTrue(root.owner_dead)
        self.assertEqual(root.instances, {})
        self.assertIsNone(context.variable(101, 9134, create=True))
        self.assertEqual(instance.evaluate(cfg), 0)
        self.assertFalse(context.send(101, 1, {}, 100))

    def test_identity_pair_must_be_distinct_and_valid(self):
        _, body, _ = self.context()
        for pair in ((100, 100), (0, 101), (100, 0), (100, 1 << 32), (-1, 101)):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                NpcContext(*pair, 1, body)

    def test_isolated_snapshot_cannot_silently_omit_coupled_controller(self):
        context, body, _ = self.context()
        with self.assertRaisesRegex(BodyVMSnapshotError, "enclosing server-domain"):
            export_component_vm(body)
        body.set(600, 7)
        with self.assertRaisesRegex(BodyVMSnapshotError, "boundary"):
            export_component_vm_with_identities(body, domain_boundaries={
                id(context): (context, ["ref", 1]), id(body.vars[600]): (body.vars[600], ["ref", 2])})

    def test_behavior_selection_is_causal_vm_storage(self):
        component = runtime.Component(100, world=runtime.World(1, 1))
        definition = make_graph(1, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STU_91EF24A9", state=0),
        ], states=[1], entries=[0])
        instance = component.create(definition)
        component.run_now()
        state = instance.state(0)
        self.assertFalse(state.selected)
        before = export_component_vm(component)
        # Storage observation only: no selection callback is executed here.
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            state.selected = True
            self.assertNotEqual(export_component_vm(component), before)


if __name__ == "__main__":
    unittest.main()
