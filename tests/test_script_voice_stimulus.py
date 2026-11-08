"""Shared voice lifecycle, separate polling, timer plugs and explicit service failures."""

import unittest
from unittest import mock

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, runtime, scene
from ow174.game.script import voice_stimulus as voice
from tests.test_script_play_loaded import dynamic
from tests.test_script_runtime_budget import graph, node, plug, setter

OWNER, OTHER, GUID = 0xA0000101, 0x80000002, 0x0EE000000000001F


def assign(position, identifier, value):
    result = setter(position, value)
    result['fields']['m_out_Var']['m_identifier'] = identifier
    return result


def model(**fields):
    values = {'m_stimulus': {'$': 'STUConfigVarVoiceStimulus', 'm_resourceKey': None,
                             'm_voiceStimulus': f'0x{GUID:016X}'},
        'm_targets': {'$': 'STU_B5A0CAF0'}, 'm_B0263D2D': None,
        'm_E0E02BD4': {'$': 'STUConfigVarFloat', 'm_value': 0.0}, 'm_FC2F7AAA': dynamic(79),
        'm_onFinishedPlug': plug((1, 'm_inPlug')), 'm_onEndPlug': plug((2, 'm_inPlug')),
        'm_F96FC964': plug((3, 'm_inPlug'))}
    values.update(fields)
    return graph([node(0, 'STU_40E05F6B', state=0, **values), assign(1, 80, 7),
                  assign(2, 81, 8), assign(3, 82, 9)])


def setup(*, strict=True, host='present', **fields):
    world = scene.SceneWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=strict)
    world.bind_script_component(OWNER, component)
    active = component.create(model(**fields), init=False)
    active.initialized = True
    state = active.state(0)
    now, events = [100.0], []
    manager = voice.Manager(load=lambda guid: voice.Stimulus(guid, True), allow=lambda _: True,
        submit=lambda st, asset, targets: (events.append(('submit', asset.guid, targets)), 17)[1],
        duration=lambda handle, asset, mode: (events.append(('duration', handle, mode)), 0.25)[1],
        part=lambda st, part: (events.append(('part', part)), 0.0)[1])
    context = voice.Context(component, clock=lambda: now[0], lookup=lambda: manager)
    if host == 'present':
        world.set_voice_context(OWNER, context)
    elif host == 'absent':
        world.set_voice_context(OWNER, None)
    return world, component, active, state, context, manager, now, events


class VoiceStimulusTests(unittest.TestCase):
    def test_unknown_context_stops_strict_begin_while_known_absence_leaves_caches_uninitialized(self):
        for host in ('unknown', 'absent'):
            _, _, _, state, context, _, _, _ = setup(host=host)
            if host == 'unknown':
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'voice stimulus context'):
                    state.begin()
            else:
                state.begin()
                self.assertTrue(state.active)
                self.assertEqual(state.voice_handle, 0)
                self.assertIsNone(state.voice_attempts)
                self.assertFalse(context.registered)

    def test_begin_resets_shared_caches_and_registers_a_separate_poll_without_ordinary_ticks(self):
        _, _, active, state, context, _, _, events = setup()
        state.begin()
        self.assertEqual(context.registered, [state])
        self.assertFalse(active.ticking)
        self.assertEqual((state.voice_attempts, state.voice_duration, state.voice_part), (0, 0.0, 1))
        self.assertEqual((state.voice_started_at, state.voice_delay, state.voice_cooldown), (100.0, 0.0, 0.0))
        self.assertFalse(events)

    def test_manager_absence_still_resets_begin_but_poll_waits_for_a_known_manager(self):
        _, _, _, state, context, _, _, events = setup()
        context.lookup = lambda: None
        state.begin()
        self.assertEqual(state.voice_part, 1)
        context.poll()
        self.assertFalse(events)

    def test_delay_uses_explicit_global_seconds_and_does_not_advance_on_script_frames(self):
        _, component, _, state, context, _, now, events = setup(
            m_B0263D2D={'$': 'STUConfigVarFloat', 'm_value': 2.0})
        state.begin()
        component.run_frame(1000)
        context.poll()
        self.assertFalse(events)
        now[0] = 102.0
        context.poll()
        self.assertEqual(events[0][0], 'submit')

    def test_valid_targets_are_deduplicated_in_order_and_empty_targets_still_submit(self):
        for targets, expected in [(None, ()),
                ((expr.Entity(OTHER), expr.Entity(OWNER), expr.Entity(OTHER), 7, None), (OTHER, OWNER))]:
            world, _, active, state, context, _, _, events = setup(m_targets=dynamic(10))
            active.write((0, 10), targets)
            state.begin()
            context.poll()
            self.assertEqual(events[0], ('submit', GUID, expected))
            self.assertIs(world.voice_context(OWNER), context)

    def test_duration_ack_queues_immediate_timer_and_keeps_wall_clock_deadline(self):
        _, component, active, state, context, _, _, _ = setup(
            m_E0E02BD4={'$': 'STUConfigVarFloat', 'm_value': 0.125})
        state.begin()
        component.now = 1234
        context.poll()
        self.assertEqual(state.voice_duration, expr.f32(375*expr.f32(0.001)))
        self.assertEqual(state.voice_retry_at, 100.375)
        self.assertEqual(state.voice_handle, 0)
        event = active.queue[0]
        self.assertEqual((event.kind, event.param, event.time), (runtime.TIMER, 1, 1234))
        self.assertIsNone(active.find_var(0, 79, create=False))

    def test_timer_writes_duration_then_follows_voice_plug_and_finish_does_not_repeat_it(self):
        _, component, active, state, context, _, now, _ = setup()
        state.begin()
        context.poll()
        component.run_now()
        self.assertEqual(active.find_var(0, 79).value(), 0.25)
        self.assertEqual(active.find_var(0, 80).value(), 7)
        active.write((0, 80), 99)
        now[0] = 100.3
        context.poll()
        component.run_now()
        self.assertFalse(state.active)
        self.assertEqual(active.find_var(0, 80).value(), 99)
        self.assertEqual(active.find_var(0, 81).value(), 8)
        self.assertFalse(context.registered)

    def test_first_zero_part_result_queries_again_and_waits_on_a_positive_second_result(self):
        _, component, _, state, context, manager, now, events = setup()
        state.begin()
        context.poll()
        component.run_now()
        now[0] = state.voice_retry_at
        values = iter([0.0, 0.5])
        queries = []
        manager.part = lambda st, part: (queries.append(part), next(values))[1]
        context.poll()
        self.assertEqual(queries, [2, 1])
        self.assertTrue(state.active)
        self.assertEqual(state.voice_part, 1)
        self.assertEqual(len(events), 2)

    def test_nonzero_part_updates_deadline_and_byte_then_queues_timer2_without_duration_write(self):
        _, component, active, state, context, manager, now, _ = setup()
        state.begin()
        context.poll()
        component.run_now()
        active.write((0, 79), 99.0)
        now[0] = state.voice_retry_at
        manager.part = lambda st, part: 0.5
        context.poll()
        self.assertEqual(state.voice_part, 2)
        self.assertEqual(state.voice_retry_at, now[0]+0.5)
        component.run_now()
        self.assertEqual(active.find_var(0, 79).value(), 99.0)

    def test_resource_failure_uses_strict_greater_than_timeout_and_finishes_through_onend(self):
        _, component, active, state, context, manager, now, _ = setup()
        manager.load = lambda _: None
        state.begin()
        context.poll()
        self.assertTrue(state.voice_failed and state.active)
        self.assertFalse(active.queue)
        now[0] += 0.001
        context.poll()
        self.assertEqual(active.queue[0].op, runtime.FINISH)
        component.run_now()
        self.assertFalse(state.active)
        self.assertIsNone(active.find_var(0, 80, create=False))

    def test_duration_retries_fail_on_attempt_six_even_when_that_result_is_positive(self):
        _, _, _, state, context, manager, _, _ = setup()
        manager.duration = lambda *args: 0.0
        state.begin()
        for _ in range(5):
            context.poll()
        manager.duration = lambda *args: 0.5
        context.poll()
        self.assertEqual(state.voice_attempts, 6)
        self.assertTrue(state.voice_failed)
        self.assertEqual(state.voice_duration, 0.0)

    def test_missing_descriptor_during_duration_poll_still_queries_guid_with_mode_false(self):
        _, _, _, state, context, manager, _, _ = setup()
        manager.duration = lambda *args: 0.0
        state.begin()
        context.poll()
        manager.load = lambda _: None
        queries = []
        manager.duration = lambda *args: (queries.append(args), 0.0)[1]
        context.poll()
        self.assertEqual(queries, [(17, GUID, False)])
        self.assertEqual(state.voice_attempts, 2)

    def test_missing_guid_during_duration_poll_does_not_increment_attempts(self):
        _, _, active, state, context, manager, _, _ = setup(m_stimulus=dynamic(11))
        active.write((0, 11), expr.Asset(GUID))
        manager.duration = lambda *args: 0.0
        state.begin()
        context.poll()
        active.write((0, 11), None)
        context.poll()
        self.assertEqual(state.voice_attempts, 1)

    def test_permission_callback_does_not_replace_the_sampled_manager(self):
        _, _, _, state, context, manager, _, events = setup()
        def allow(_):
            context.lookup = lambda: voice.Manager()
            return True
        manager.allow = allow
        state.begin()
        context.poll()
        self.assertEqual([event[0] for event in events], ['submit', 'duration'])

    def test_unknown_consumers_stop_before_fabricating_a_failure_or_duration(self):
        for field in ('load', 'allow', 'submit', 'duration'):
            _, _, _, state, context, manager, _, _ = setup()
            setattr(manager, field, NotImplemented)
            state.begin()
            with self.subTest(field=field), self.assertRaises(runtime.UnsupportedScriptError):
                context.poll()

    def test_retirement_during_submit_stops_before_handle_commit_or_duration_query(self):
        _, _, _, state, context, manager, _, events = setup()
        manager.submit = lambda *args: (context.retire(), 17)[1]
        state.begin()
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'context lifecycle'):
            context.poll()
        self.assertEqual(state.voice_handle, 0)
        self.assertFalse(events)

    def test_failure_notification_is_a_plug_only_and_expired_notifications_are_ignored(self):
        _, _, active, state, context, _, _, _ = setup()
        state.begin()
        self.assertTrue(context.notify_failure(state))
        self.assertEqual(active.find_var(0, 82).value(), 9)
        self.assertTrue(state.active)
        state.end(False)
        self.assertFalse(context.notify_failure(state))

    def test_context_retires_with_component_unbind_and_failure(self):
        for failure in (False, True):
            world, component, active, state, context, _, _, _ = setup()
            state.begin()
            if failure:
                with self.assertRaises(runtime.UnsupportedScriptError):
                    component.unsupported(active, 'fixture', 'unknown')
            else:
                world.unbind_script_component(OWNER)
            self.assertTrue(context.retired)
            self.assertEqual(context.poll(), 0)
            self.assertFalse(context.registered)

    def test_foreign_or_replacement_context_is_rejected(self):
        world, component, _, _, context, _, _, _ = setup()
        with self.assertRaises(ValueError):
            world.set_voice_context(OWNER, voice.Context(component, clock=lambda: 0.0, lookup=lambda: None))
        with self.assertRaises(ValueError):
            world.set_voice_context(OWNER, voice.Context(runtime.Component(OWNER),
                clock=lambda: 0.0, lookup=lambda: None))
        self.assertIs(world.voice_context(OWNER), context)

    def test_unknown_part_consumer_stops_before_synthesizing_a_completion(self):
        _, component, active, state, context, manager, now, _ = setup()
        state.begin()
        context.poll()
        component.run_now()
        now[0] = state.voice_retry_at
        manager.part = NotImplemented
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'voice next part'):
            context.poll()
        self.assertEqual(state.voice_part, 1)
        self.assertFalse(active.queue)

    def test_unknown_duration_output_stops_before_following_the_custom_voice_plug(self):
        _, component, active, state, context, _, _, _ = setup(m_FC2F7AAA={'$': 'UnknownOutput'})
        state.begin()
        context.poll()
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'voice duration output'):
            component.run_now()
        self.assertIsNone(active.find_var(0, 80, create=False))

    def test_stale_component_failure_preserves_its_replacements_voice_context(self):
        world, component, _, _, old_context, _, _, _ = setup()
        world.unbind_script_component(OWNER)
        replacement = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, replacement)
        current = voice.Context(replacement, clock=lambda: 0.0, lookup=lambda: None)
        world.set_voice_context(OWNER, current)
        world.script_failed(component)
        self.assertTrue(old_context.retired)
        self.assertFalse(current.retired)
        self.assertIs(world.voice_context(OWNER), current)

    def test_legacy_voice_bark_keeps_passive_begin_and_end_policy(self):
        _, component, active, state, _, _, _, events = setup(strict=False, host='unknown')
        state.begin()
        component.run_frame(10)
        self.assertFalse(events)
        state.end(True)
        self.assertEqual(active.find_var(0, 80).value(), 7)

    def test_passive_vm_storage_observes_voice_fields_without_polling_or_callbacks(self):
        component = runtime.Component(OWNER)
        active = component.create(model(), init=False)
        state = active.state(0)
        with mock.patch.object(voice, 'update', side_effect=AssertionError('executed')):
            before = snapshot_digest(export_component_vm(component))
            state.voice_duration = 0.25
            self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))


if __name__ == '__main__':
    unittest.main()
