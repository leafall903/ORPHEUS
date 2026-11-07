"""Auxiliary subgraph interfaces have an explicit object and dispatch lifetime."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import runtime, scene, scopes
from tests.test_script_runtime_budget import graph, node, plug, setter

CLASS = 'STU_BD02E168'
OWNER = 0xA0000101


def definition(*, extra=None):
    scope = plug((1, 'm_beginPlug'))
    scope.update({'$': 'STU_904BFCEC', 'm_FF3DAF1E': [1]})
    return graph([node(0, CLASS, state=0, m_5B10D01F=scope, **(extra or {})),
                  node(1, 'STUStatescriptState', state=1, m_onBeginPlug=plug((2, 'm_inPlug'))),
                  setter(2, 7)])


def setup(*, service='registered', strict=True, model=None):
    world = scene.SceneWorld()
    world.bind(OWNER)
    registry = scopes.Registry()
    if service == 'registered':
        world.set_subgraph_scopes(OWNER, registry)
    elif service == 'none':
        world.set_subgraph_scopes(OWNER, None)
    component = runtime.Component(OWNER, world=world, strict=strict)
    active = component.create(model or definition(), init=False)
    active.initialized = True
    return component, active, registry


class ScopeInterfaceTests(unittest.TestCase):
    def test_allocation_registers_an_inactive_object_without_running_its_subgraph(self):
        component, active, registry = setup()
        state = active.state(0)
        self.assertEqual(registry.states, [state])
        self.assertFalse(state.active)
        self.assertFalse(active.queue)
        self.assertNotIn(7, active.vars)
        self.assertIs(active.state(0), state)
        self.assertEqual(len(registry.states), 1)
        component.run_now()
        self.assertNotIn(7, active.vars)

    def test_enter_can_run_while_the_parent_is_inactive_and_exit_aborts_children(self):
        component, active, registry = setup()
        state, child = active.state(0), active.state(1)
        registry.enter(state)
        self.assertFalse(child.active)
        component.run_now()
        self.assertTrue(child.active)
        self.assertFalse(state.active)
        self.assertEqual(active.find_var(0, 7).value(), 7)
        registry.exit(state)
        self.assertFalse(child.active)
        self.assertEqual(registry.states, [state])

    def test_exit_cancels_a_child_begin_queued_at_the_current_time(self):
        component, active, registry = setup()
        state = active.state(0)
        registry.enter(state)
        self.assertTrue(active.queue)
        registry.exit(state)
        component.run_now()
        self.assertFalse(active.state(1).active)
        self.assertEqual(active.state(1).counter, 0)
        self.assertNotIn(7, active.vars)

    def test_ordinary_state_begin_end_do_not_invent_auxiliary_dispatch(self):
        component, active, registry = setup()
        state = active.state(0)
        state.request(runtime.BEGIN)
        component.run_now()
        self.assertTrue(state.active)
        self.assertNotIn(7, active.vars)
        registry.enter(state)
        component.run_now()
        child = active.state(1)
        state.end(True)
        self.assertTrue(child.active)
        registry.exit(state)
        self.assertFalse(child.active)

    def test_instance_retirement_releases_inactive_interfaces_and_does_not_follow_plugs(self):
        model = definition(extra={'m_onEndPlug': plug((2, 'm_inPlug'))})
        component, active, registry = setup(model=model)
        state = active.state(0)
        registry.enter(state)
        component.run_now()
        before = active.find_var(0, 7).value()
        component.destroy(active)
        self.assertFalse(registry.states)
        self.assertIsNone(state.scope_registry)
        self.assertEqual(active.find_var(0, 7).value(), before)
        with self.assertRaises(ValueError):
            registry.enter(state)

    def test_cross_instance_registration_and_id_reuse_do_not_keep_retired_objects(self):
        component, first, registry = setup()
        old = first.state(0)
        second = component.create(definition(), init=False)
        second.initialized = True
        live = second.state(0)
        self.assertEqual(registry.states, [old, live])
        component.destroy(first)
        fresh = component.create(definition(), instance_id=first.id, init=False)
        fresh.initialized = True
        new = fresh.state(0)
        self.assertEqual(registry.states, [live, new])
        with self.assertRaises(ValueError):
            registry.exit(old)

    def test_unknown_registration_service_stops_strict_allocation(self):
        component, active, _ = setup(service='unknown')
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            active.state(0)
        self.assertEqual(raised.exception.diagnostic['feature'], 'subgraph scope registration')
        self.assertEqual(raised.exception.diagnostic['node'], 0)
        self.assertTrue(active.stopped)
        component.destroy(active)

    def test_declared_no_service_and_legacy_policy_do_not_manufacture_callbacks(self):
        for options in ({'service': 'none'}, {'service': 'unknown', 'strict': False}):
            component, active, registry = setup(**options)
            state = active.state(0)
            state.request(runtime.BEGIN)
            component.run_now()
            self.assertTrue(state.active)
            self.assertIsNone(state.scope_registry)
            self.assertFalse(registry.states)
            self.assertNotIn(7, active.vars)

    def test_entity_retirement_clears_interfaces_and_reuse_requires_a_fresh_service(self):
        component, active, registry = setup()
        state = active.state(0)
        component.world.unbind(OWNER)
        self.assertFalse(registry.states)
        with self.assertRaises(ValueError):
            registry.enter(state)
        component.destroy(active)
        component.world.bind(OWNER)
        self.assertIs(component.world.subgraph_scopes(OWNER), NotImplemented)

    def test_failed_callback_clears_registrations_and_rejects_later_dispatch(self):
        model = definition()
        model.node(0).fields['m_5B10D01F']['links'] = [[3, 'm_inPlug']]
        model._by_pos[3] = type(model.node(0))(node(3, 'MissingScopeAction'))
        _component, active, registry = setup(model=model)
        state = active.state(0)
        with self.assertRaises(runtime.UnsupportedScriptError):
            registry.enter(state)
        self.assertFalse(registry.states)
        with self.assertRaises(ValueError):
            registry.enter(state)

    def test_callback_recursion_shares_the_vm_follow_budget_and_failure_cleanup(self):
        model = definition()
        model.node(0).fields['m_5B10D01F']['links'] = [[3, 'm_inPlug']]
        model._by_pos[3] = type(model.node(0))(node(3, 'ScopeLoop'))
        _component, active, registry = setup(model=model)
        state = active.state(0)
        with mock.patch.dict(runtime.HANDLERS, ScopeLoop=lambda *_: registry.enter(state)), \
                self.assertRaises(runtime.ScriptBudgetError):
            registry.enter(state)
        self.assertFalse(registry.states)
        self.assertTrue(active.stopped)

    def test_service_declarations_require_bound_entities_and_a_stable_lifetime(self):
        component, _, registry = setup()
        for entity, value in [(True, registry), (0xA0000201, registry), (OWNER, object())]:
            with self.assertRaises(ValueError):
                component.world.set_subgraph_scopes(entity, value)
        component.world.set_subgraph_scopes(OWNER, registry)
        with self.assertRaises(ValueError):
            component.world.set_subgraph_scopes(OWNER, scopes.Registry())

    def test_passive_state_storage_has_no_untracked_registry_reference(self):
        component = runtime.Component(OWNER)
        active = component.create(definition(), init=False)
        state = active.state(0)
        self.assertIsNone(state.scope_registry)
        export_component_vm(component)


if __name__ == '__main__':
    unittest.main()
