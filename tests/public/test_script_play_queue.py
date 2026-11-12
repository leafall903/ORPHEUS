"""Real component routing, resource waits, request ordering and transient graph lifetimes."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import snapshot_digest
from ow174.game.script import expr, modifiers, play, play_queue, runtime, scene, scene_host
from tests.public.test_script_play_loaded import KEY, definition
from tests.public.test_script_play_request import requesting
from tests.public.test_script_startup import definition as component_definition
from tests.public.test_script_startup import spec
from tests.script_fixtures import export_synthetic_component_vm, graph, node

A, B = 123, 456
GUID = (0x0580 << 48) | 2


def request(*, policy=1, byte=1, params=(), overrides=()):
    return play.Request(play.Resource(GUID, overrides), (B,),
                        play.InvocationContext(A, A, A), params, policy, byte)


def model():
    result = definition()
    result.index = 2
    return result


class PlayQueueTests(unittest.TestCase):
    def queue(self, provider=None):
        target = model()
        component = runtime.Component(B, strict=True)
        provider = (lambda _: play_queue.Load('ready', target)) if provider is None else provider
        return play_queue.Queue(component, provider), target

    def test_ready_request_creates_real_independent_instance_and_queues_start_inputs(self):
        queue, target = self.queue()
        queue.component.frame, queue.component.now = 30, 480
        queue.submit(request(params=((KEY, (17,)),)))
        active = queue.component.instances[1]
        self.assertIs(active.graph, target)
        self.assertIsNone(active.parent)
        self.assertEqual(active.cached_source_entity, A)
        self.assertEqual(active.initialized_frame, 30)
        self.assertIsNone(active.find_var(0, 41, create=False))
        self.assertFalse(queue.pending)
        with mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True):
            queue.component.run_now()
        self.assertEqual(active.find_var(0, 41).value(), (17,))

    def test_pending_resource_does_not_allocate_and_failed_resource_drops_the_request(self):
        load = play_queue.Load('pending')
        queue, _target = self.queue(lambda _: load)
        queue.submit(request())
        self.assertEqual(len(queue.pending), 1)
        self.assertFalse(queue.component.instances)
        load.phase = 'failed'
        queue.flush()
        self.assertFalse(queue.pending)
        self.assertFalse(queue.component.instances)

    def test_waiting_requests_flush_in_reverse_order_and_preserve_remaining_order(self):
        loads = [play_queue.Load('pending') for _ in range(3)]
        queue, target = self.queue(lambda _: loads[len(queue.pending)])
        for value in (10, 20, 30):
            queue.submit(request(params=((KEY, value),)))
        loads[1].phase, loads[1].definition = 'ready', target
        queue.flush()
        self.assertEqual([p.request.params[0][1] for p in queue.pending], [10, 30])
        for load in (loads[0], loads[2]):
            load.phase, load.definition = 'ready', target
        queue.flush()
        values = [i.queue[0].assignment[-1] for i in queue.component.instances.values()]
        self.assertEqual(values, [20, 30, 10])

    def test_skip_does_not_acquire_or_apply_schema(self):
        queue, target = self.queue()
        queue.component.create(target)
        with mock.patch.object(queue, 'acquire', side_effect=AssertionError('must skip')):
            queue.submit(request(policy=0, overrides=((7, 17),)))
        self.assertEqual(len(queue.component.instances), 1)

    def test_replacement_retires_matching_roots_and_children_before_failed_acquisition(self):
        queue, target = self.queue(lambda _: None)
        old = queue.component.create(target)
        child = queue.component.create(graph(), parent=old)
        queue.submit(request(policy=3))
        self.assertTrue(old.stopped and child.stopped)
        self.assertFalse(queue.component.instances)

    def test_reuse_uses_first_current_match_and_retains_allocation_identity_and_flags(self):
        queue, target = self.queue()
        first = queue.component.create(target)
        queue.component.create(target)
        first.set_source_entity(B)
        first.auto_retire = False
        queue.submit(request(policy=2, byte=0, params=((KEY, 23),)))
        self.assertEqual(len(queue.component.instances), 2)
        self.assertEqual(first.cached_source_entity, B)
        self.assertFalse(first.auto_retire)
        self.assertEqual(first.queue[0].assignment[-1], 23)

    def test_uninitialized_reuse_keeps_request_until_owner_startup_is_prepared(self):
        queue, target = self.queue()
        active = queue.component.create(target, init=False)
        queue.submit(request(policy=2))
        self.assertEqual(queue.pending[0].instance_id, active.id)
        active.init()
        queue.flush()
        self.assertFalse(queue.pending)

    def test_unknown_resources_and_wrong_resource_identity_remain_errors(self):
        for load in (NotImplemented, play_queue.Load('ready'), play_queue.Load('unknown')):
            queue, _ = self.queue(lambda _, value=load: value)
            with self.subTest(load=load), self.assertRaises(ValueError):
                queue.submit(request())
        queue, _ = self.queue(lambda _: play_queue.Load('ready', graph()))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            queue.submit(request())

    def test_reentrant_provider_or_retired_queue_cannot_keep_creating_instances(self):
        queue, target = self.queue()
        queue.acquire = lambda _: queue.submit(request())
        with self.assertRaisesRegex(ValueError, 'reentrant'):
            queue.submit(request())
        queue.acquire = lambda _: (queue.retire(), play_queue.Load('ready', target))[1]
        with self.assertRaisesRegex(ValueError, 'retired'):
            queue.submit(request())
        self.assertFalse(queue.component.instances)

    def test_native_capacity_boundary_drops_pending_requests_above_the_limit(self):
        queue, target = self.queue()
        with mock.patch.object(play_queue, 'INSTANCE_LIMIT', 1):
            queue.component.create(target)
            queue.submit(request())
            self.assertEqual(len(queue.component.instances), 2)
            queue.submit(request())
            self.assertEqual(len(queue.component.instances), 2)
            self.assertFalse(queue.pending)


class AutomaticLifetimeTests(unittest.TestCase):
    def active(self, entries=()):
        component = runtime.Component(B, strict=True)
        active = component.create(graph(list(entries), entries=[item['pos'] for item in entries]))
        active.auto_retire = True
        return component, active

    def test_only_zero_request_byte_enables_automatic_retirement(self):
        for byte in (0, 1, 2, 128, 255):
            queue, _ = PlayQueueTests().queue()
            queue.submit(request(byte=byte))
            self.assertEqual(queue.component.instances[1].auto_retire, byte == 0)

    def test_quiescent_instance_waits_through_the_frame_after_initialization(self):
        component, active = self.active()
        component.run_frame(0)
        self.assertTrue(active.retirement_pending)
        component.run_frame(1)
        self.assertIn(active.id, component.instances)
        component.run_frame(2)
        self.assertNotIn(active.id, component.instances)

    def test_explicit_stop_retirement_requires_world_opt_in_and_a_root(self):
        for opted_in, transient, child in ((True, True, False), (False, True, False),
                                            (True, False, False), (False, False, False),
                                            (True, True, True)):
            with self.subTest(opted_in=opted_in, transient=transient, child=child):
                component = runtime.Component(B, strict=True)
                parent = component.create(graph()) if child else None
                instance = component.create(graph([node(0, 'STUStatescriptState', state=0)]),
                                            parent=parent)
                instance.auto_retire = transient
                state = instance.state(0)
                state.begin()
                instance.post(runtime.Event(component.now, 0, runtime.STOP))
                with (
                    mock.patch.object(component.world, 'retires_stopped_play', return_value=opted_in),
                    mock.patch.object(state, 'on_destroyed', wraps=state.on_destroyed) as cleanup,
                ):
                    component.run_now()

                retired = opted_in and not child
                self.assertTrue(instance.stopped)
                cleanup.assert_called_once_with()
                self.assertEqual(instance.id not in component.instances, retired)
                self.assertEqual(instance in component.destroyed, retired)

    def test_events_and_active_states_keep_the_transient_instance_alive(self):
        component = runtime.Component(B, strict=True)
        active = component.create(graph([node(0, 'STUStatescriptState', state=0)]))
        active.auto_retire = True
        state = active.state(0)
        state.active = True
        component.frame = 5
        active.check_auto_retirement()
        self.assertIn(active.id, component.instances)
        state.active = False
        active.post(runtime.Event(999, 0, runtime.ASSIGN, assignment=(0, 7, 17)))
        active.check_auto_retirement()
        self.assertIn(active.id, component.instances)
        active.queue.clear()
        active.check_auto_retirement()
        self.assertNotIn(active.id, component.instances)

    def test_enabled_other_entries_keep_alive_but_start_entries_and_disabled_entries_do_not(self):
        for cls, disabled, retires in [('STU_BB4AC733', False, True), ('STU_8A7E9DA8', False, True),
                                        ('STUStatescriptEntryGameMessage', False, True),
                                        ('STUStatescriptEntry', False, False),
                                        ('STUStatescriptEntry', True, True)]:
            component, active = self.active([node(0, cls)])
            active.queue.clear()
            active.disabled_start_entries.clear()
            if disabled:
                active.disabled_start_entries.add(0)
            component.frame = 5
            active.check_auto_retirement()
            self.assertEqual(active.id not in component.instances, retires)

    def test_ordinary_initial_entries_disable_before_their_queued_execution(self):
        component, active = self.active([node(0, 'STUStatescriptEntry')])
        self.assertEqual(active.disabled_start_entries, {0})
        self.assertEqual(len(active.queue), 1)
        component.run_frame(2)
        self.assertTrue(active.stopped)

    def test_passive_storage_keeps_lifetime_flags_and_initialization_frame(self):
        component, active = self.active()
        with mock.patch.object(runtime.Instance, 'check_auto_retirement',
                               side_effect=AssertionError('executed')):
            before = snapshot_digest(export_synthetic_component_vm(component))
            active.retirement_pending = True
            self.assertNotEqual(before, snapshot_digest(export_synthetic_component_vm(component)))


class SceneQueueTests(unittest.TestCase):
    def setup_scene(self, provider=None):
        world = scene.SceneWorld()
        world.bind(A)
        world.bind(B)
        caller, action = requesting(world=world, m_targets={'$': 'SceneTarget'})
        original = world.native
        world.native = lambda cfg, active: (expr.Entity(B) if cfg.get('$') == 'SceneTarget'
                                           else original(cfg, active))
        world.bind_script_component(A, caller.component)
        receiver = runtime.Component(B, world=world, strict=True)
        world.bind_script_component(B, receiver)
        target = model()
        queue = play_queue.Queue(receiver, (lambda _: play_queue.Load('ready', target))
                                  if provider is None else provider)
        world.set_play_queue(B, queue)
        return world, caller, action, receiver, queue

    def test_authored_action_routes_to_real_component_before_following_success(self):
        _world, caller, action, receiver, queue = self.setup_scene()
        caller.run_node(action, None)
        self.assertEqual(caller.find_var(0, 7).value(), 99)
        self.assertEqual(receiver.instances[1].cached_source_entity, A)
        self.assertFalse(queue.pending)

    def test_pending_resource_allows_parent_output_and_target_retirement_cancels_pending(self):
        world, caller, action, _receiver, queue = self.setup_scene(lambda _: play_queue.Load('pending'))
        caller.run_node(action, None)
        self.assertEqual(caller.find_var(0, 7).value(), 99)
        self.assertEqual(len(queue.pending), 1)
        world.unbind(B)
        self.assertTrue(queue.closed)
        self.assertFalse(queue.pending)

    def test_unknown_provider_stops_before_parent_success(self):
        _, caller, action, _, _ = self.setup_scene(lambda _: NotImplemented)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'resource availability is unknown'):
            caller.run_node(action, None)
        self.assertNotIn(7, caller.vars)

    def test_active_self_replacement_stops_before_retiring_the_source(self):
        world, caller, action, _receiver, _queue = self.setup_scene()
        selected = play.Request(play.Resource((0x580 << 48) | caller.graph.index), (A,),
                                play.InvocationContext(A, A, A), (), 3, 0)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'active self replacement'):
            world.submit_script_request(caller, action, selected)
        self.assertIn(caller.id, caller.component.instances)

    def test_same_index_other_asset_type_is_not_an_active_self_replacement(self):
        world, caller, action, _, _ = self.setup_scene()
        provider = mock.Mock(return_value=None)
        world.set_play_queue(A, play_queue.Queue(caller.component, provider))
        guid = (0x980 << 48) | caller.graph.index
        selected = play.Request(play.Resource(guid), (A,), play.InvocationContext(A, A, A), (), 3, 0)
        world.submit_script_request(caller, action, selected)
        provider.assert_called_once_with(guid)
        self.assertIsNone(caller.component.fault)
        self.assertIn(caller.id, caller.component.instances)

    def test_host_flushes_declared_resources_and_retires_queues_on_close(self):
        world = scene.SceneWorld()
        world.bind(A)
        host = scene_host.SceneHost(world, graph_loader=lambda _: graph())
        component = host.add(A, component_definition(spec(1)))
        load = play_queue.Load('pending')
        host.configure_play_requests(lambda _: load)
        queue = world.play_queues[A]
        queue.submit(request())
        load.phase, load.definition = 'ready', model()
        with mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True):
            self.assertTrue(host.advance(20))
        self.assertFalse(queue.pending)
        self.assertEqual(len(component.instances), 2)
        host.close()
        self.assertTrue(queue.closed)
        self.assertFalse(world.play_queues)

    def test_configuration_seals_the_initial_component_catalog(self):
        host = scene_host.SceneHost(scene.SceneWorld())
        host.configure_play_requests(lambda _: None)
        with self.assertRaises(ValueError):
            host.configure_play_requests(lambda _: None)
        host.world.bind(A)
        with self.assertRaises(ValueError):
            host.add(A, component_definition(spec(1)))

    def test_submission_does_not_evaluate_the_source_context_a_second_time(self):
        _, caller, action, _, _ = self.setup_scene()
        with mock.patch.object(caller, 'source_entity', wraps=caller.source_entity) as inherited:
            caller.run_node(action, None)
        inherited.assert_called_once_with()

    def test_failed_stale_source_cannot_clear_its_replacement_services(self):
        world, caller, action, _, _ = self.setup_scene()
        world.unbind_script_component(A)
        newer = runtime.Component(A, world=world, strict=True)
        world.bind_script_component(A, newer)
        service = modifiers.NumericModifiers()
        service.upsert(modifiers.Record(1, 7, 0, 19, 2, 2.0, 0.0))
        before = list(service.records)
        world.set_property_modifiers(A, service)
        queue = play_queue.Queue(newer, lambda _: None)
        world.set_play_queue(A, queue)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'source lifecycle'):
            world.submit_script_request(caller, action, request())
        self.assertEqual(service.records, before)
        self.assertIs(world.play_queues[A], queue)
        self.assertFalse(queue.closed)


if __name__ == '__main__':
    unittest.main()
