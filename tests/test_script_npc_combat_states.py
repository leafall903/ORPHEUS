
"""Strict NPC combat-state registration and explicit event dispatch."""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import combat_mod_filter, expr, health_queries, nodes, runtime, scene, scopes

OWNER = 0xA0000100
RESOURCE, TAG = 0x0D80000000000318, 0x0D800000000019C4


def fixture(cls, fields, *, binding_absent=False):
    world = scene.SceneWorld()
    world.bind(OWNER)
    registry = scopes.Registry()
    world.set_subgraph_scopes(OWNER, registry)
    if binding_absent:
        world.declare_spatial_binding_owner_absent(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    definition = make_graph(0xF110, [node(0, cls, state=0, fields=fields)], states=[0])
    instance = component.create(definition, init=False)
    return component, instance.state(0), registry


def filter_fields():
    return {
        "m_60703B1E": f"0x{RESOURCE:016X}",
        "m_F60AA143": [f"0x{TAG:016X}"],
        "m_41B10FF2": 1,
        "m_50530EAB": {"$": "STUConfigVarInt", "m_value": 2},
        "m_941C56C2": {"$": "STUConfigVarBool", "m_value": 1},
    }


class NPCCombatStateTests(unittest.TestCase):
    def test_registered_tag_filter_uses_exact_resource_and_retires(self):
        component, state, _ = fixture(combat_mod_filter.CLASS, filter_fields())
        self.assertIsInstance(state, nodes.CombatModFilter)
        self.assertIsNone(state.filter_event(RESOURCE, (TAG,)))
        state.begin()
        self.assertEqual(component.state_registries[state.node.cls], [state])
        self.assertEqual(state.filter_event(RESOURCE, iter([TAG])),
                         combat_mod_filter.Result(0, 2.0, 1, (True, True)))
        self.assertIsNone(state.filter_event(RESOURCE + 5, (TAG,)))
        self.assertIsNone(state.filter_event(RESOURCE, (TAG + 1,)))
        state.end(False)
        self.assertEqual(component.state_registries[state.node.cls], [])
        self.assertIsNone(state.filter_event(RESOURCE, (TAG,)))

    def test_deny_tag_vetoes_and_unknown_entity_filter_fails_strict(self):
        fields = filter_fields()
        fields["m_5CFFCBAE"] = [f"0x{TAG + 1:016X}"]
        _, state, _ = fixture(combat_mod_filter.CLASS, fields)
        state.begin()
        self.assertIsNone(state.filter_event(RESOURCE, iter([TAG, TAG + 1])))
        fields["m_5E6F1B8B"] = {"$": "STUConfigVarFilter"}
        _, unsupported, _ = fixture(combat_mod_filter.CLASS, fields)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "combat modifier filter profile"):
            unsupported.begin()

    def test_controller_linked_state_uses_standard_order_and_cleanup(self):
        definition = make_graph(0xF110, [
            node(0, "STU_274FF373", state=0),
            node(1, "STU_274FF373", state=1),
        ], states=[0, 1])
        component = runtime.Component(OWNER, strict=True)
        instance = component.create(definition, init=False)
        first, second = instance.state(0), instance.state(1)
        self.assertIsInstance(first, nodes.NPCControllerLinkedState)
        first.begin()
        second.begin()
        self.assertEqual(first.payload(), {"link": (instance.id, 1)})
        self.assertEqual(second.payload(), {})
        first.end(False)
        self.assertEqual(component.state_registries[first.node.cls], [second])
        second.end(False)
        self.assertEqual(component.state_registries[first.node.cls], [])

    def test_component51_counter_balances_overlap_and_instance_retirement(self):
        definition = make_graph(0xF110, [
            node(0, "STU_064D4DE7", state=0),
            node(1, "STU_064D4DE7", state=1),
        ], states=[0, 1])
        world = scene.SceneWorld()
        world.bind(OWNER)
        counter = health_queries.StateCounter(0xFFFFFFFF)
        world.set_component51_state_counter(OWNER, counter)
        component = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, component)
        instance = component.create(definition, init=False)
        first, second = instance.state(0), instance.state(1)
        first.begin()
        self.assertEqual(counter.value, 0)
        second.begin()
        self.assertEqual(counter.value, 1)
        first.end(False)
        self.assertEqual(counter.value, 0)
        component.destroy(instance)
        self.assertEqual(counter.value, 0xFFFFFFFF)

    def test_weighted_output_uses_strict_prefix_and_then_ordinary_output(self):
        chosen = {"$": "STUConfigVarDynamic", "m_identifier": 101}
        copied = {"$": "STUConfigVarDynamic", "m_identifier": 102}
        weighted = [
            {"$": "STU_2AA453C0", "m_weight": 1, "links": [[1, "m_inPlug"]]},
            {"$": "STU_2AA453C0", "m_weight": 2, "links": [[2, "m_inPlug"]]},
            None,
        ]
        definition = make_graph(0xF110, [
            node(0, "STU_8502CA73", fields={"m_D84D6E03": weighted, "m_outPlug": output([3, "m_inPlug"])}),
            node(1, "STUStatescriptActionSetVar", fields={"m_out_Var": chosen, "m_value": 11}),
            node(2, "STUStatescriptActionSetVar", fields={"m_out_Var": chosen, "m_value": 22}),
            node(3, "STUStatescriptActionSetVar", fields={"m_out_Var": copied, "m_value": chosen}),
        ])

        class Draw:
            def uniform(self, start, stop):
                self.bounds = start, stop
                return draw

        for draw, expected in ((0, 11), (1, 22), (3, None)):
            with self.subTest(draw=draw):
                component = runtime.Component(OWNER, strict=True)
                component.rng = Draw()
                instance = component.create(definition, init=False)
                self.assertTrue(instance.run_node(definition.node(0), None))
                self.assertEqual(instance.context().var(expr.INSTANCE, 102), expected)
                self.assertEqual(component.rng.bounds, (0, 3.0))

    def test_spatial_scope_requires_explicit_owner_absence(self):
        _, state, registry = fixture("STU_96333BBE", {}, binding_absent=True)
        state.begin()
        registry.enter(state)
        registry.exit(state)
        state.end(False)
        _, unknown, unknown_registry = fixture("STU_96333BBE", {})
        unknown.begin()
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "spatial binding owner"):
            unknown_registry.enter(unknown)
