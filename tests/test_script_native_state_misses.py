"""Strict state-slot misses supplied by an explicit native factory catalog."""

import unittest

from ow174.game.script import runtime, scene
from tests.test_script_runtime_budget import graph, node, plug, setter

MISSING = "STU_724DFBC6"


class NativeStateMissTests(unittest.TestCase):
    def test_known_factory_miss_leaves_empty_slot_and_follows_other_links(self):
        world = scene.SceneWorld(native_state_misses=(MISSING,))
        component = runtime.Component(123, world=world, strict=True)
        definition = graph([
            setter(0, 5, (1, "m_beginPlug"), (2, "m_beginPlug")),
            node(1, MISSING, state=0, m_onBeginPlug=plug((3, "m_inPlug"))),
            node(2, "STUStatescriptState", state=1, m_onBeginPlug=plug((4, "m_inPlug"))),
            setter(3, 99),
            setter(4, 7),
        ])
        instance = component.create(definition)
        self.assertTrue(instance.run_node(definition.node(0), None))
        component.run_frame(1)
        self.assertEqual(instance.find_var(0, 7).value(), 7)
        self.assertIsNone(instance.state(0))
        self.assertNotIn(0, instance.states)
        self.assertTrue(instance.state(1).active)
        self.assertEqual(instance.state(1).counter, 1)

    def test_unlisted_unknown_state_stays_strict_and_catalog_is_scene_local(self):
        definition = graph([node(0, MISSING, state=0), node(1, "STU_UnknownOther", state=1)])
        configured = scene.SceneWorld(native_state_misses=(MISSING,))
        instance = runtime.Component(123, world=configured, strict=True).create(definition)
        self.assertIsNone(instance.state(0))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_UnknownOther"):
            instance.state(1)
        default = scene.SceneWorld()
        instance = runtime.Component(123, world=default, strict=True).create(definition)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, MISSING):
            instance.state(0)

    def test_catalog_does_not_erase_modeled_states(self):
        modeled = "STU_600E3C59"
        world = scene.SceneWorld(native_state_misses=(modeled,))
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(graph([node(0, modeled, state=0)]))
        self.assertIsNotNone(instance.state(0))
