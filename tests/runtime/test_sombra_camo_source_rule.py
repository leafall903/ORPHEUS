"""Actual Camo85 source lifecycle, with unknown native caches kept unavailable."""

import unittest
from unittest import mock

import test_game_sombra_combat as scenes

from ow174.game import body_vm_snapshot, replay_values
from ow174.game.script import entity_control, runtime
from tests.script_fixtures import graph, node


class CamoSourceRuleTests(unittest.TestCase):
    def cast(self):
        scene = scenes.SombraCombatTests()
        scene.setUp()
        scene.press(8)
        scene.press(0, 60)
        instance = scene.camo()
        state = instance.node_state(instance.graph._by_pos[85])
        self.assertTrue(state.active)
        return scene, instance.component, state

    def test_actual_camo_registers_once_and_unlinks_on_authored_exit(self):
        scene, component, state = self.cast()
        self.assertEqual(component.state_registries[entity_control.CLASS], [state])
        self.assertFalse(state.control_cache_ready)
        self.assertEqual(state.control_generation, 0)
        self.assertIs(component.world.entity_control_context(component.entity), NotImplemented)
        scene.press(0, 3)
        self.assertEqual(component.state_registries[entity_control.CLASS], [state])
        no_refresh = mock.patch.object(
            entity_control, "refresh_cache", side_effect=AssertionError("snapshot evaluated"),
        )
        with no_refresh:
            data = replay_values.decode(body_vm_snapshot.export_body_vm(scene.a.body_script)["value"])
        registry = dict(dict(data["component"])["state_registries"]["items"])[entity_control.CLASS]
        identity = ["state", state.instance.id, state.index]
        self.assertEqual(registry["items"], [{"ref": identity}])
        records = [item for item in data["objects"] if item["ref"] == identity]
        self.assertEqual(len(records), 1)
        self.assertFalse(dict(records[0]["fields"])["control_cache_ready"])
        scene.press(8)
        scene.press(0, 60)
        self.assertFalse(state.active)
        self.assertEqual(component.state_registries[entity_control.CLASS], [])
        self.assertFalse(state.control_cache_ready)

    def test_actual_body_retirement_unlinks_its_original_component_rule(self):
        scene, component, state = self.cast()
        old = scene.a.body_script
        self.assertEqual(component.state_registries[entity_control.CLASS], [state])
        self.assertTrue(scene.run.match.switch_hero(scene.a, scene.b.hero))
        self.assertTrue(old.retired)
        self.assertFalse(component.state_registries.get(entity_control.CLASS))
        self.assertFalse(scene.a.body_script.component.state_registries.get(entity_control.CLASS))

    def test_registered_source_cannot_supply_uninitialized_native_cache(self):
        _, component, state = self.cast()
        distance = mock.Mock(side_effect=AssertionError("unavailable cache reached distance service"))
        with self.assertRaises(runtime.UnsupportedScriptError):
            entity_control.combine(state, component, (False, True, True, False, False), distance)
        distance.assert_not_called()
        self.assertEqual(component.fault.diagnostic["feature"], "entity control rule cache unavailable")

    def test_strict_known_absent_engine_context_still_publishes_ready_cache(self):
        # An explicitly absent engine is distinct from the ordinary Match's
        # unavailable service. Strict refresh must retain its existing work.
        world = runtime.World()
        component = runtime.Component(0x800001AE, world=world, strict=True)
        instance = component.create(graph([node(0, entity_control.CLASS, state=0)]), init=False)
        instance.initialized = True
        state = instance.state(0)
        with mock.patch.object(world, "entity_control_context", return_value=None):
            state.begin()
        self.assertTrue(state.control_cache_ready)
        self.assertEqual(state.control_radius, -1.0)
        self.assertEqual(state.control_generation, 1)
        self.assertEqual(component.state_registries[entity_control.CLASS], [state])
