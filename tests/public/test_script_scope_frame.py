"""The ordinary scope end phase precedes one deferred native notification pass."""

import unittest
from unittest import mock

from ow174.game.script import effect_allocation, effect_routing, runtime, scene, scopes
from tests.test_script_for_code import KEY, row
from tests.test_script_runtime_budget import graph, node, plug, setter
from tests.test_script_scopes import CLASS, OWNER


class FrameWorld(scene.SceneWorld):
    def __init__(self):
        super().__init__()
        self.received = []
        self.on_code = None

    def code_changed(self, component, token):
        self.received.append((component.now, component.code_value(KEY)))
        if self.on_code is not None:
            self.on_code(component)


def setup(*, scope=None, extra=(), frame=50):
    world = FrameWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    host = scopes.ForwardHost(component)
    if frame is not None:
        host.set_frame(frame)
    world.set_subgraph_scopes(OWNER, host)
    dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}
    instance = component.create(graph([
        node(0, CLASS, state=0, m_5B10D01F=scope or plug((4, 'm_inPlug'))),
        node(1, 'STU_CD46AF93', state=1, m_values=[row(dynamic)], m_DC897C87=1),
        *extra, setter(4, 7),
    ]), init=False)
    instance.initialized = True
    instance.write((0, 7), 0)
    component.code_observers.append((1, KEY))
    return component, instance, host, world


class OrdinaryScopeFrameTests(unittest.TestCase):
    def test_scope_changes_are_visible_to_the_same_frames_deferred_notification(self):
        component, instance, host, world = setup()
        for index in (0, 1):
            instance.state(index).request(runtime.BEGIN)
        host.run_frame(0)
        self.assertEqual(world.received, [(15, (True, 7))])
        self.assertEqual(component.frame, 0)
        self.assertEqual(host.frame, 50)  # Native history clock remains an explicit input.
        self.assertFalse(host.history[instance.state(0)].records)

    def test_deferred_begin_keeps_its_scope_edge_pending_until_the_next_end_phase(self):
        _, instance, host, world = setup()
        scope = instance.state(0)
        world.on_code = lambda _: scope.begin()
        instance.state(1).request(runtime.BEGIN)
        host.run_frame(0)
        self.assertTrue(scope.active)
        self.assertEqual(instance.vars[7].value(), 0)
        self.assertEqual(host.history[scope].records, [scopes.Edge(50, True)])
        world.on_code = None
        host.set_frame(51)
        host.run_frame(1)
        self.assertEqual(instance.vars[7].value(), 7)
        self.assertFalse(host.history[scope].records)

    def test_deferred_end_leaves_custom_children_until_the_next_end_phase(self):
        component, instance, host, world = setup(
            scope=plug((2, 'm_beginPlug')), extra=[node(2, 'STUStatescriptState', state=2)],
        )
        scope = instance.state(0)
        scope.node.fields['m_5B10D01F']['m_FF3DAF1E'] = [2]
        scope.begin()
        host.drain()
        component.run_now()
        child = instance.state(2)
        self.assertTrue(child.active)
        world.on_code = lambda _: scope.end(False)
        instance.state(1).request(runtime.BEGIN)
        host.run_frame(0)
        self.assertFalse(scope.active)
        self.assertTrue(child.active)
        self.assertEqual(host.history[scope].records, [scopes.Edge(50, False)])
        world.on_code = None
        host.run_frame(1)
        self.assertFalse(child.active)
        self.assertFalse(host.history[scope].records)

    def test_scope_queued_native_state_begins_on_the_next_frame(self):
        _, instance, host, world = setup(scope=plug((1, 'm_beginPlug')))
        instance.state(0).request(runtime.BEGIN)
        host.run_frame(0)
        self.assertFalse(instance.state(1).active)
        self.assertFalse(world.received)
        host.run_frame(1)
        self.assertTrue(instance.state(1).active)
        self.assertEqual(world.received, [(31, (True, 0))])

    def test_deferred_queued_begin_is_processed_before_the_next_scope_end_phase(self):
        _, instance, host, world = setup()
        scope = instance.state(0)
        world.on_code = lambda _: scope.request(runtime.BEGIN)
        instance.state(1).request(runtime.BEGIN)
        host.run_frame(0)
        self.assertFalse(scope.active)
        self.assertFalse(host.history[scope].records)
        world.on_code = None
        host.run_frame(1)
        self.assertTrue(scope.active)
        self.assertEqual(scope.start, 16)
        self.assertEqual(instance.vars[7].value(), 7)

    def test_multiple_due_times_share_one_end_phase_and_notification_pass(self):
        extra = [node(2, 'STUStatescriptStateWait', state=2,
                      m_timeout={'$': 'STUConfigVarFloat', 'm_value': .005},
                      m_onFinishedPlug=plug((5, 'm_inPlug'))),
                 node(3, 'STUStatescriptStateWait', state=3,
                      m_timeout={'$': 'STUConfigVarFloat', 'm_value': .010},
                      m_onFinishedPlug=plug((6, 'm_inPlug'))),
                 setter(5, 50), setter(6, 60)]
        _, instance, host, world = setup(extra=extra)
        for index in range(4):
            instance.state(index).request(runtime.BEGIN)
        with mock.patch.object(host, 'drain', wraps=host.drain) as drain:
            host.run_frame(0)
        self.assertEqual(drain.call_count, 1)
        self.assertEqual([instance.state(index).ended_at for index in (2, 3)], [5, 10])
        self.assertEqual(world.received, [(15, (True, 7))])

    def test_retired_world_binding_cannot_drive_an_ordinary_frame(self):
        component, instance, host, _ = setup()
        instance.state(0).request(runtime.BEGIN)
        component.world.unbind(OWNER)
        with self.assertRaises(ValueError):
            host.run_frame(2)
        self.assertEqual(component.now, 0)
        self.assertEqual(instance.vars[7].value(), 0)

    def test_missing_native_clock_fails_before_deferred_notifications(self):
        component, instance, host, world = setup(frame=None)
        for index in (0, 1):
            instance.state(index).request(runtime.BEGIN)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            host.run_frame(0)
        self.assertEqual(error.exception.diagnostic['feature'], 'subgraph scope frame')
        self.assertIs(component.fault, error.exception)
        self.assertFalse(world.received)
        self.assertFalse(host.history)

    def test_failed_scope_callback_prevents_the_deferred_notification_pass(self):
        component, instance, host, world = setup(scope=plug((3, 'm_inPlug')),
                                                extra=[node(3, 'UnknownOrdinaryScopeAction')])
        for index in (0, 1):
            instance.state(index).request(runtime.BEGIN)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            host.run_frame(0)
        self.assertEqual(error.exception.diagnostic['class'], 'UnknownOrdinaryScopeAction')
        self.assertIs(component.fault, error.exception)
        self.assertFalse(world.received)
        self.assertFalse(host.history)
        self.assertFalse(component.code_pending[1])

    def test_failed_deferred_callback_retires_the_effect_created_in_the_end_phase(self):
        component, instance, host, world = setup()
        instance.graph.node(0).cls = 'STU_582E0D5F'
        entries = effect_allocation.EffectList()
        world.set_effect_context(OWNER, effect_routing.Context(OWNER, object(), False))
        world.set_effect_target(OWNER, effect_routing.Target(OWNER, object(), entries))
        observed = []

        def fail(current):
            observed.append((len(entries.entries), entries.entries[0].live))
            current.unsupported(instance, 'ordinary deferred test', 'NativeService')

        world.on_code = fail
        for index in (0, 1):
            instance.state(index).request(runtime.BEGIN)
        with self.assertRaises(runtime.UnsupportedScriptError):
            host.run_frame(0)
        self.assertEqual(observed, [(1, True)])
        self.assertFalse(entries.entries[0].live)
        self.assertIsNone(entries.entries[0].effect.observer)
        self.assertFalse(host.history)
        self.assertIsNotNone(component.fault)

    def test_scope_and_deferred_callbacks_share_one_frame_work_budget(self):
        component, instance, host, world = setup(scope=plug((3, 'm_inPlug')),
                                                extra=[node(3, 'OrdinaryScopeWork')])

        def work(*_):
            for _ in range(600):
                component.consume_work('ordinary phase test')
            return True

        world.on_code = work
        for index in (0, 1):
            instance.state(index).request(runtime.BEGIN)
        with mock.patch.dict(runtime.HANDLERS, OrdinaryScopeWork=work), \
                mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 1000), \
                self.assertRaises(runtime.ScriptBudgetError):
            host.run_frame(0)
        self.assertEqual(len(world.received), 1)
        self.assertFalse(host.history)
        self.assertTrue(instance.stopped)


if __name__ == '__main__':
    unittest.main()
