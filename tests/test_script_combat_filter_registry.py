"""Actual damage host and original tag callback share one native state registry."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node
from test_script_npc_combat_states import OWNER, RESOURCE, TAG, filter_fields
from ow174.game.body_vm_snapshot import export_component_vm_with_identities
from ow174.game.content import JUNKENSTEIN_MODES
from ow174.game.script import combat_mod_filter, graph, nodes, runtime, scene


class CombatFilterRegistryTests(unittest.TestCase):
    def filters(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        world.bind(OWNER, hero=0x02E0000000000040, definition=0x040000000000028F)
        component = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, component)
        authored = graph.graph(0x0CBC).node(141)
        fields = {key: authored.field(key) for key in
                  ("m_60703B1E", "m_41B10FF2", "m_5CFFCBAE", "m_50530EAB")}
        hosted = component.create(make_graph(0x0CBC, [node(141, authored.cls, state=0, fields=fields)], states=[141]), init=False).state(0)
        tagged = component.create(make_graph(0xF112, [node(0, combat_mod_filter.CLASS, state=0, fields=filter_fields())], states=[0]), init=False).state(0)
        return component, world, hosted, tagged

    def test_actual_host_and_tag_callback_register_together_and_retire_independently(self):
        component, world, hosted, tagged = self.filters()
        tagged.begin()
        hosted.begin()
        self.assertIsInstance(hosted, nodes.CombatDamageFilter)
        self.assertIsInstance(hosted, nodes.CombatModFilter)
        self.assertEqual(component.state_registries[combat_mod_filter.CLASS], [tagged, hosted])
        self.assertEqual(tagged.payload(), {"link": (hosted.instance.id, hosted.index)})
        self.assertEqual(tagged.filter_event(RESOURCE, (TAG,)), combat_mod_filter.Result(0, 2.0, 1, (True, True)))
        self.assertIsNone(hosted.filter_event(RESOURCE, (TAG,)))
        self.assertAlmostEqual(world.outgoing_combat_damage(OWNER, 20, ()), 16)
        tagged.end(False)
        self.assertEqual(component.state_registries[combat_mod_filter.CLASS], [hosted])
        self.assertAlmostEqual(world.outgoing_combat_damage(OWNER, 20, ()), 16)
        component.destroy(hosted.instance)
        self.assertEqual(component.state_registries[combat_mod_filter.CLASS], [])
        self.assertNotIn(OWNER, world.combat_damage_filters)
        self.assertEqual(world.outgoing_combat_damage(OWNER, 20, ()), 20)

    def test_actual_incoming_barrier_filter_keeps_host_binding_and_cleanup(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        world.bind(OWNER, definition=0x04000000000026B0)
        component = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, component)
        authored = graph.graph(0x156C).node(9)
        fields = {key: authored.field(key) for key in
                  ("m_60703B1E", "m_41B10FF2", "m_F60AA143", "m_50530EAB")}
        state = component.create(make_graph(0x156C, [node(9, authored.cls, state=0, fields=fields)], states=[9]), init=False).state(0)
        state.begin()
        self.assertEqual(component.state_registries[combat_mod_filter.CLASS], [state])
        self.assertEqual(world.incoming_barrier_damage(OWNER, 20, (0x0D800000000006F9,)), 10)
        self.assertEqual(world.incoming_barrier_damage(OWNER, 20, (TAG,)), 20)
        state.end(False)
        self.assertEqual(component.state_registries[combat_mod_filter.CLASS], [])
        self.assertNotIn(OWNER, world.combat_damage_filters)
        self.assertEqual(world.incoming_barrier_damage(OWNER, 20, (0x0D800000000006F9,)), 20)

    def test_stored_filter_profile_is_read_only_stable_and_sensitive(self):
        component, world, hosted, tagged = self.filters()
        hosted.begin()
        tagged.begin()
        def capture():
            return export_component_vm_with_identities(
                component, external_reference=lambda value: ["scene-world", OWNER] if value is world else None,
            )[0]
        before = (component.now, component.frame, hosted.counter, tagged.counter)
        with patch.object(scene.SceneWorld, "bind_combat_damage_filter", side_effect=AssertionError("provider called during export")):
            with patch.object(combat_mod_filter, "profile", side_effect=AssertionError("profile decoded during export")):
                original = capture()
                self.assertEqual(capture(), original)
                saved = tagged.combat_filter
                resource, allow, deny, mode = saved
                tagged.combat_filter = resource, allow, deny, mode + 1
                self.assertNotEqual(capture(), original)
                tagged.combat_filter = saved
                self.assertEqual(capture(), original)
        self.assertEqual((component.now, component.frame, hosted.counter, tagged.counter), before)
