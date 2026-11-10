"""Native source cache, allocation instigator and entity-instance request identity."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm, snapshot_digest
from ow174.game.script import expr, play, runtime, scene
from tests.test_script_play_loaded import definition, dynamic
from tests.test_script_play_request import requesting
from tests.test_script_runtime_budget import graph, node

OWNER, CALLER, INSTIGATOR = 0x800001AE, 0xA0000101, 0xC0000101


def instance(world=None, entity=OWNER):
    component = runtime.Component(entity, world=world, strict=True)
    return component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)


class SourceEntityTests(unittest.TestCase):
    def test_valid_cache_bypasses_new_builtin_values_until_the_source_setter_runs(self):
        active = instance()
        active.write((0, 0x1B), expr.Entity(INSTIGATOR))
        self.assertEqual(active.source_entity(), INSTIGATOR)
        active.write((0, 0x1B), expr.Entity(CALLER))
        self.assertEqual(active.source_entity(), INSTIGATOR)
        active.set_source_entity(OWNER)
        self.assertEqual(active.source_entity(), OWNER)
        self.assertEqual(active.find_var(0, 0x1B).value(), expr.Entity(OWNER))

    def test_invalid_cache_requeries_effective_builtin_and_only_entity_values_convert(self):
        active = instance()
        for value in (None, 17, expr.Asset(OWNER), expr.Entity(0), expr.Entity(0x1FFFFFFF)):
            with self.subTest(value=value):
                active.write((0, 0x1B), value)
                self.assertEqual(active.source_entity(), expr.to_entity(value))
        active.write((0, 0x1B), expr.Entity(INSTIGATOR))
        output = active.state(0)
        output.active = True
        output.output = lambda _: expr.Entity(CALLER)
        active.find_var(0, 0x1B).links.append(runtime.Link(output))
        self.assertEqual(active.source_entity(), CALLER)

    def test_source_setter_commits_cache_after_the_typed_variable_write(self):
        active = instance()
        active.set_source_entity(OWNER)
        output = active.state(0)
        output.active = True
        seen = []
        output.var_written = lambda var: seen.append((var.base, active.cached_source_entity))
        active.find_var(0, 0x1B).links.append(runtime.Link(output))
        active.set_source_entity(INSTIGATOR)
        self.assertEqual(seen, [(expr.Entity(INSTIGATOR), OWNER)])
        self.assertEqual(active.cached_source_entity, INSTIGATOR)

    def test_source_cache_uses_a_uint32_and_rejects_invalid_setter_input_before_writes(self):
        active = instance()
        active.write((0, 0x1B), expr.Entity(-1))
        self.assertEqual(active.source_entity(), 0xFFFFFFFF)
        for bad in (True, -1, 0x100000000, None):
            with self.subTest(entity=bad), self.assertRaises(ValueError):
                active.set_source_entity(bad)
        self.assertEqual(active.find_var(0, 0x1B).value(), expr.Entity(-1))

    def test_passive_storage_captures_cache_without_reading_or_changing_it(self):
        active = instance()
        active.set_source_entity(OWNER)
        active.write((0, 0x1B), expr.Entity(CALLER))
        with mock.patch.object(runtime.Instance, 'source_entity', side_effect=AssertionError('executed')):
            before = snapshot_digest(export_component_vm(active.component))
            active.cached_source_entity = CALLER
            self.assertNotEqual(snapshot_digest(export_component_vm(active.component)), before)
        active.cached_source_entity = True
        with self.assertRaisesRegex(BodyVMSnapshotError, 'source entity cache'):
            export_component_vm(active.component)


class AllocationSourceTests(unittest.TestCase):
    def test_new_instigator_is_visible_to_defaults_and_differs_from_start_entry_caller(self):
        model = definition(source=dynamic(41))
        model.fields['m_publicSchema'] = {'$': 'STUStatescriptSchema', 'm_entries': [
            {'m_0D09D2D9': '0x0D8000000000002A', 'm_value': dynamic(0x1B)}]}
        component = runtime.Component(OWNER, strict=True)
        active = component.play_loaded_graph(model, source=CALLER, instigator=INSTIGATOR)
        self.assertEqual(active.source_entity(), INSTIGATOR)
        self.assertIsNone(active.find_var(0, 42, create=False))
        with mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True):
            component.run_now()
        self.assertEqual(active.find_var(0, 42).value(), expr.Entity(INSTIGATOR))
        self.assertEqual(active.find_var(0, 41).value(), expr.Entity(CALLER))

    def test_skip_and_reuse_do_not_reset_the_existing_allocation_source(self):
        component = runtime.Component(OWNER, strict=True)
        model = definition()
        active = component.play_loaded_graph(model, source=CALLER, instigator=INSTIGATOR)
        for policy in (0, 2):
            component.play_loaded_graph(model, source=CALLER, instigator=OWNER, policy=policy)
            self.assertEqual(active.source_entity(), INSTIGATOR)

    def test_replacement_gets_a_fresh_instigator_and_only_zero_uses_the_owner_fallback(self):
        component = runtime.Component(OWNER, strict=True)
        model = definition()
        first = component.play_loaded_graph(model, source=CALLER, instigator=INSTIGATOR)
        second = component.play_loaded_graph(model, source=CALLER, instigator=0, policy=3)
        self.assertTrue(first.stopped)
        self.assertEqual(second.source_entity(), OWNER)
        third = component.play_loaded_graph(model, source=CALLER, instigator=0x1FFFFFFF, policy=1)
        self.assertEqual(third.find_var(0, 0x1B).value(), expr.Entity(0x1FFFFFFF))

    def test_invalid_instigator_does_not_mutate_existing_instances(self):
        component = runtime.Component(OWNER, strict=True)
        active = component.create(graph(), init=False)
        for bad in (True, -1, 0x100000000):
            with self.subTest(instigator=bad), self.assertRaises(ValueError):
                component.play_loaded_graph(active.graph, source=CALLER, instigator=bad, policy=3)
            self.assertEqual(list(component.instances.values()), [active])


class ScenePlayContextTests(unittest.TestCase):
    def test_live_component_context_does_not_grant_unimplemented_submission_or_success(self):
        world = scene.SceneWorld()
        world.bind(123)
        active, action = requesting(world=world)
        world.bind_script_component(123, active.component)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene PlayScript queue'):
            active.run_node(action, None)
        self.assertNotIn(7, active.vars)

    def test_bound_entity_instance_uses_its_component_identity_and_cached_inherited_source(self):
        world = scene.SceneWorld()
        world.bind(OWNER)
        active = instance(world)
        world.bind_script_component(OWNER, active.component)
        active.set_source_entity(INSTIGATOR)
        self.assertEqual(world.play_script_context(active, active.graph.node(0)),
                         play.InvocationContext(OWNER, OWNER, INSTIGATOR))

    def test_retired_or_replaced_instance_cannot_request_through_the_scene(self):
        for replacement in (False, True):
            world = scene.SceneWorld()
            world.bind(OWNER)
            active = instance(world)
            world.bind_script_component(OWNER, active.component)
            if replacement:
                world.unbind_script_component(OWNER)
                newer = instance(world)
                world.bind_script_component(OWNER, newer.component)
            else:
                active.component.destroy(active)
            with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene PlayScript source lifecycle'):
                world.play_script_context(active, active.graph.node(0))

    def test_source_retirement_during_an_effective_value_read_stops_before_request_submission(self):
        world = scene.SceneWorld()
        world.bind(OWNER)
        active = instance(world)
        world.bind_script_component(OWNER, active.component)
        output = active.state(0)
        output.active = True

        def read(_):
            world.unbind_script_component(OWNER)
            return expr.Entity(INSTIGATOR)

        output.output = read
        active.find_var(0, 0x1B).links.append(runtime.Link(output))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene PlayScript source lifecycle'):
            world.play_script_context(active, active.graph.node(0))


if __name__ == '__main__':
    unittest.main()
