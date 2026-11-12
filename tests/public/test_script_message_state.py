"""Native persistent sender registration, explicit services and teardown."""

import unittest

from ow174.game.body_vm_snapshot import BodyVMSnapshotError
from ow174.game.script import expr, message_state, runtime, scene
from tests.script_fixtures import export_synthetic_component_vm, graph, node, plug, setter

OWNER, OTHER = 0xA0000100, 0xA0000101
MESSAGE = 0x02400000000006FF


def literal(value):
    return {'$': 'SenderLiteral', 'value': value}


def global_target(first=1, a=0, b=0):
    return {'$': 'STU_1D719351', 'm_6A8DB059': first, 'm_1199BFE0': a, 'm_111E4A0E': b}


def targets(value):
    return {'$': 'STU_9B213168', 'm_targets': value}


class SenderWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'SenderLiteral':
            value = cfg['value']
            return value(instance) if callable(value) else value
        return super().native(cfg, instance)


def setup(*, policy=None, **fields):
    world = SenderWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    notifications, calls = [], []
    host = message_state.Host(notify=notifications.append)
    policy = policy or message_state.Policy(False, False, False)
    context = message_state.Context(component, host,
        policy=lambda state: calls.append('policy') or policy,
        frame=lambda state: calls.append('frame') or 10,
        priority=lambda state, resource: calls.append(('priority', resource)) or None,
        timer_notify=lambda state: calls.append('timer'))
    world.set_message_state_context(OWNER, context)
    data = {'m_gameMessage': literal(expr.Asset(MESSAGE)), 'm_priority': None,
            'm_target': global_target(), 'm_params': []}
    data.update(fields)
    model = graph([node(0, 'STU_38EE1100', state=0, **data), setter(1, 31)])
    instance = component.create(model, init=False)
    instance.initialized = True
    state = instance.state(0)
    return world, component, instance, state, context, calls, notifications


def begin(state):
    with state.instance.component.operation():
        state.begin()


def keys(state):
    return [(key.entity, key.flags, key.message) for key in state.message_keys]


class MessageStateTests(unittest.TestCase):
    def test_begin_registers_before_authored_plugs_and_never_reads_lazy_parameters(self):
        _, _, instance, state, context, calls, _ = setup(
            m_onBeginPlug=plug((1, 'm_inPlug')), m_params=[{'m_value': {'$': 'Unknown'}}])
        begin(state)
        self.assertEqual(instance.find_var(0, 7).value(), 31)
        self.assertEqual(keys(state), [(0, 1, MESSAGE & 0xFFFFFFFF)])
        self.assertEqual((state.message_frame, state.message_sequence), (10, 0))
        self.assertEqual(calls, [('priority', 0), 'frame', 'policy'])
        self.assertIs(context.host.manager.channels[state.message_keys[0]].senders[0].state, state)
        self.assertFalse(instance.queue)

    def test_native_null_post_registration_callback_does_not_repeat_transition_actions(self):
        _, component, instance, state, _, _, _ = setup(m_transitionPlug=[plug((1, 'm_inPlug'))])
        calls = []
        instance.graph.node(1).fields['m_value'] = literal(lambda instance: calls.append('transition') or 31)
        begin(state)
        self.assertEqual(calls, ['transition'])
        message_state.refresh(state)
        self.assertEqual(calls, ['transition'])
        component.run_now()
        self.assertFalse(state.active)

    def test_missing_failed_nonasset_and_low_word_zero_messages_clear_prior_registration(self):
        for value in (None, expr.EVALUATION_FAILED, 7, expr.Asset(0x0240000000000000)):
            _, _, _, state, context, calls, _ = setup()
            begin(state)
            state.node.fields['m_gameMessage'] = None if value is None else literal(value)
            calls.clear()
            message_state.refresh(state)
            self.assertFalse(state.message_keys)
            self.assertFalse(context.host.manager.channels)
            self.assertFalse(calls)
            self.assertEqual((state.message_frame, state.message_sequence), (10, 0))

    def test_global_channel_matrix_uses_explicit_role_and_authored_bytes(self):
        policy = message_state.Policy(True, True, False, True)
        _, _, _, state, _, _, _ = setup(policy=policy, m_target=global_target(1, 1, 1), m_67FD6A60=1)
        begin(state)
        self.assertEqual([row[1] for row in keys(state)], [4, 8, 16, 32])
        _, _, _, state, _, _, _ = setup(policy=message_state.Policy(True, False, False, False),
                                        m_target=global_target(1, 1, 0), m_67FD6A60=1)
        begin(state)
        self.assertEqual([row[1] for row in keys(state)], [2, 16])

    def test_generic_targets_keep_duplicates_skip_invalid_and_use_both_channels(self):
        policy = message_state.Policy(True, True, False, False, True)
        _, _, _, state, context, _, _ = setup(policy=policy, m_67FD6A60=1,
            m_target=targets(literal((expr.Entity(OTHER), expr.Entity(0), expr.Entity(OTHER),
                                     expr.Entity(0xFFFFFFFF), OTHER))))
        begin(state)
        self.assertEqual(keys(state), [(OTHER, 16, MESSAGE & 0xFFFFFFFF),
                                      (OTHER, 2, MESSAGE & 0xFFFFFFFF)]*2)
        self.assertEqual(len(context.host.manager.channels), 2)
        self.assertEqual(context.host.manager.next_sequence, 4)

    def test_world_policy_can_force_primary_and_unused_unknown_policy_fields_are_not_read(self):
        _, _, _, state, _, _, _ = setup(policy=message_state.Policy(True, False, True, False, False, True),
                                        m_target=targets(literal(expr.Entity(OTHER))))
        begin(state)
        self.assertEqual([row[1] for row in keys(state)], [32])
        _, _, _, state, _, _, _ = setup(policy=message_state.Policy(False, False, False),
                                        m_target=targets(literal(expr.Entity(OTHER))))
        begin(state)
        self.assertEqual([row[1] for row in keys(state)], [1])

    def test_unknown_mode_policy_priority_resource_and_native_frame_remain_strict(self):
        for field, callback in (
            ('policy', lambda state: message_state.Policy(True, False, False)),
            ('priority', lambda state, resource: NotImplemented),
            ('frame', lambda state: None),
        ):
            _, component, _, state, context, _, _ = setup()
            setattr(context, field, callback)
            with self.assertRaises(runtime.UnsupportedScriptError):
                begin(state)
            self.assertIsNotNone(component.fault)
            self.assertTrue(context.retired)
            self.assertFalse(context.host.manager.channels)

    def test_priority_is_resolved_as_a_resource_with_scalar_success_and_first_asset(self):
        _, _, _, state, context, _, _ = setup(m_priority=literal(((expr.Asset(91),), 99)))
        seen = []

        def priority(state, resource):
            seen.append(resource)
            return message_state.Priority(1.25, True)

        context.priority = priority
        begin(state)
        record = context.host.manager.channels[state.message_keys[0]].senders[0]
        self.assertEqual(seen, [91])
        self.assertEqual((record.priority, record.prefer_later), (1.25, True))

    def test_reentry_tick_and_dependency_refresh_reuse_sequence_and_update_targets(self):
        _, component, instance, state, context, _, _ = setup(m_target=targets(
            {'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0}))
        instance.write((0, 41), expr.Entity(OWNER))
        begin(state)
        original = (state.counter, state.message_frame, state.message_sequence)
        begin(state)
        self.assertEqual((state.counter, state.message_frame, state.message_sequence), original)
        instance.write((0, 41), expr.Entity(OTHER))
        component.run_now()
        self.assertEqual([row[0] for row in keys(state)], [OTHER])
        context.frame = lambda state: 500
        component.run_frame(1)
        self.assertEqual((state.message_frame, state.message_sequence), original[1:])
        self.assertEqual([row[0] for row in keys(state)], [OTHER])

    def test_abort_removes_all_keys_and_notifies_receivers_in_native_order(self):
        _, component, _, state, context, _, events = setup(m_target=targets(
            literal((expr.Entity(OTHER), expr.Entity(OTHER)))))
        begin(state)
        key = state.message_keys[0]
        receiver = object()
        with component.operation(), context.host.use(context) as manager:
            manager.register_receiver(key, receiver)
            state.end(False)
        self.assertEqual(events, [receiver])
        self.assertFalse(state.message_keys)
        self.assertFalse(context.host.manager.channels[key].senders)
        self.assertFalse(state.watched)

    def test_detach_and_component_failure_drop_only_their_states_without_callbacks(self):
        for action in ('detach', 'fail'):
            world, component, instance, state, context, _, events = setup()
            begin(state)
            if action == 'detach':
                world.unbind_script_component(OWNER, component)
            else:
                with self.assertRaises(runtime.UnsupportedScriptError):
                    component.unsupported(instance, 'fixture', state.node.cls)
            self.assertTrue(context.retired)
            self.assertFalse(context.host.manager.channels)
            self.assertFalse(events)

    def test_destroy_after_external_detach_cannot_touch_a_replacement_component(self):
        world, component, instance, state, context, _, events = setup()
        begin(state)
        world.unbind_script_component(OWNER, component)
        replacement = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, replacement)
        with component.operation():
            component.destroy(instance)
        self.assertIs(world.script_components[OWNER], replacement)
        self.assertIsNone(component.fault)
        self.assertIsNone(replacement.fault)
        self.assertFalse(context.host.manager.channels)
        self.assertFalse(events)

    def test_budget_failure_during_registration_discards_the_partial_sender(self):
        _, component, _, state, context, _, _ = setup(m_target=targets(literal(
            tuple(expr.Entity(OTHER+n) for n in range(10)))))
        with self.assertRaises(runtime.ScriptBudgetError), component.operation():
            component._work_left = 12
            state.begin()
        self.assertTrue(context.retired)
        self.assertFalse(context.host.manager.channels)
        self.assertFalse(state.message_keys)
        self.assertFalse(context.host.current)

    def test_retirement_during_priority_callback_prevents_frame_policy_and_registration(self):
        _, component, _, state, context, calls, _ = setup()

        def retire(state, resource):
            context.retire()
            return None

        context.priority = retire
        with self.assertRaises(runtime.UnsupportedScriptError):
            begin(state)
        self.assertFalse(context.host.manager.channels)
        self.assertFalse(calls)
        self.assertIsNotNone(component.fault)

    def test_missing_service_and_unrelated_component_cannot_borrow_a_context(self):
        world, component, _, state, context, _, _ = setup()
        world.message_contexts.clear()
        with self.assertRaises(runtime.UnsupportedScriptError):
            begin(state)
        self.assertIs(world.message_state_context(OWNER, component=runtime.Component(OWNER)), NotImplemented)
        self.assertFalse(context.host.manager.channels)
        self.assertIsNotNone(component.fault)

    def test_timer_notification_uses_constructor_flag_and_only_param_one(self):
        for target, byte, expected in ((global_target(), 0, ['timer']),
                                      (global_target(1, 1), 0, []), (global_target(), 1, [])):
            _, _, _, state, _, calls, _ = setup(m_target=target, m_67FD6A60=byte)
            begin(state)
            calls.clear()
            state.timer(2)
            state.timer(1)
            state.timer(1)
            self.assertEqual(calls, expected)

    def test_unknown_target_is_strict_but_known_base_and_empty_iterator_register_nothing(self):
        for target in ({'$': 'STU_F121C8C5'}, targets(literal(()))):
            _, _, _, state, context, _, _ = setup(m_target=target)
            begin(state)
            self.assertFalse(context.host.manager.channels)
            self.assertEqual((state.message_frame, state.message_sequence), (10, -1))
        _, _, _, state, _, _, _ = setup(m_target={'$': 'Unknown'})
        with self.assertRaises(runtime.UnsupportedScriptError):
            begin(state)

    def test_full_shared_manager_export_stays_unsupported_without_running_services(self):
        _, component, _, state, context, _, _ = setup()
        begin(state)

        def fail(*args):
            self.fail('passive export ran a native service')

        context.policy = context.frame = context.priority = fail
        with self.assertRaises(BodyVMSnapshotError):
            export_synthetic_component_vm(component)

    def test_reply_sequence_survives_cleanup_and_uses_low_word(self):
        _, component, _, state, _, _, _ = setup()
        begin(state)
        self.assertEqual(state.payload(), {'reply': 0})
        state.message_sequence = 0x12345
        with component.operation():
            state.end(False)
        self.assertEqual(state.payload(), {'reply': 0x2345})

    def test_shared_host_uses_each_components_budget_and_detaches_only_its_senders(self):
        world, component, instance, state, context, _, events = setup()
        world.bind(OTHER)
        other = runtime.Component(OTHER, world=world, strict=True)
        world.bind_script_component(OTHER, other)
        second_context = message_state.Context(other, context.host,
            policy=lambda state: message_state.Policy(False, False, False), frame=lambda state: 10,
            priority=lambda state, resource: None)
        world.set_message_state_context(OTHER, second_context)
        child = other.create(instance.graph, init=False)
        child.initialized = True
        second = child.state(0)
        begin(state)
        begin(second)
        self.assertEqual((state.message_sequence, second.message_sequence), (0, 1))
        world.unbind_script_component(OWNER, component)
        self.assertFalse(second_context.retired)
        with other.operation(), context.host.use(second_context) as manager:
            self.assertEqual(manager.collect(second.message_keys[0]), (second,))
        self.assertFalse(events)

    def test_retired_context_replacement_cannot_be_borrowed_by_an_old_state(self):
        world, _, _, state, context, _, _ = setup()
        begin(state)
        context.retire()
        replacement = message_state.Context(context.component, context.host,
            policy=context.policy, frame=context.frame, priority=context.priority)
        world.set_message_state_context(OWNER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            message_state.refresh(state)
        self.assertFalse(context.host.manager.channels)


if __name__ == '__main__':
    unittest.main()
