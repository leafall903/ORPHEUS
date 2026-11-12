"""Original91EF timer order with explicit component87; no scheduler or targets invented."""
import unittest
from unittest.mock import patch

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import ai_behavior, runtime
from tests.test_script_runtime_budget import graph, node, plug, setter


class BehaviorWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.context = NotImplemented

    def ai_behavior_context(self, entity, *, component=None):
        return self.context


class AIBehaviorTests(unittest.TestCase):
    def fixture(self, *, strict=True):
        world = BehaviorWorld()
        component = runtime.Component(123, world=world, strict=strict)
        world.context = ai_behavior.Context(component)
        definition = graph([
            node(0, ai_behavior.CLASS, state=0,
                 m_6F92F4BD=plug((1, 'm_inPlug')), m_AFA8C3AE=plug((2, 'm_inPlug'))),
            setter(1, 120), setter(2, 128),
        ])
        first = component.create(definition, init=False)
        second = component.create(definition, init=False)
        first.init()
        second.init()
        return component, first.state(0), second.state(0), world.context

    def test_enter_resets_saved_handle_without_selecting_or_following(self):
        component, state, other, owner = self.fixture()
        owner.current, owner.pending = other, state
        state.ai_previous = 0x10001
        state.begin()
        self.assertEqual(state.ai_previous, 0)
        self.assertIs(owner.current, other)
        self.assertIs(owner.pending, state)
        self.assertNotIn(7, state.instance.vars)
        component.run_now()
        self.assertNotIn(7, state.instance.vars)

    def test_timer2_traverses_selected_plug_before_current_and_pending_write(self):
        _, state, other, owner = self.fixture()
        state.begin()
        owner.current = owner.pending = other
        follow = state.instance.follow
        seen = []

        def observe(*args):
            if args[1] == 'm_6F92F4BD':
                seen.append((owner.current, owner.pending))
            return follow(*args)

        with patch.object(state.instance, 'follow', side_effect=observe):
            state.timer(2)
        self.assertEqual(seen, [(other, other)])
        self.assertEqual(state.instance.vars[7].value(), 120)
        self.assertIs(owner.current, state)
        self.assertIsNone(owner.pending)

    def test_timer1_exits_then_follows_output_then_resolves_and_restores(self):
        _, state, other, owner = self.fixture()
        state.begin()
        other.begin()
        state.ai_previous = 0x10001
        owner.current, owner.pending = state, other
        seen = []

        def resolve(token):
            seen.append((token, state.instance.vars[7].value(), owner.current))
            return other

        owner.resolve = resolve
        state.timer(1)
        self.assertEqual(seen, [(0x10001, 128, None)])
        self.assertEqual(other.instance.vars[7].value(), 120)
        self.assertIs(owner.current, other)
        self.assertIsNone(owner.pending)
        self.assertEqual(state.ai_previous, 0)

    def test_clear_without_saved_handle_preserves_opaque_pending(self):
        _, state, other, owner = self.fixture()
        state.begin()
        owner.current, owner.pending = other, other
        state.timer(1)
        self.assertIsNone(owner.current)
        self.assertIs(owner.pending, other)
        self.assertEqual(state.instance.vars[7].value(), 128)

    def test_missing_saved_state_still_clears_handle_and_pending(self):
        _, state, other, owner = self.fixture()
        state.begin()
        owner.pending = other
        owner.resolve = lambda token: None
        state.ai_previous = 0x10001
        state.timer(1)
        self.assertIsNone(owner.current)
        self.assertIsNone(owner.pending)
        self.assertEqual(state.ai_previous, 0)

    def test_zero_low_word_handle_skips_lookup_and_preserves_pending(self):
        _, state, other, owner = self.fixture()
        state.begin()
        state.ai_previous = 0x10000
        owner.pending = other
        owner.resolve = lambda token: self.fail('invalid low-word handle must not resolve')
        state.timer(1)
        self.assertEqual(state.ai_previous, 0x10000)
        self.assertIs(owner.pending, other)

    def test_cleanup_only_clears_matching_current_and_always_exits_custom_subgraph(self):
        component, state, other, owner = self.fixture()
        state.begin()
        state.timer(2)
        owner.current, owner.pending = other, other
        state.end(False)
        self.assertIs(owner.current, other)
        self.assertIs(owner.pending, other)
        component.destroy(state.instance)
        self.assertIs(owner.current, other)

    def test_known_absence_allows_cleanup_but_cannot_accept_selection(self):
        component, state, _, _ = self.fixture()
        state.begin()
        component.world.context = None
        state.end(False)
        state.begin()
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            state.timer(2)
        self.assertEqual(raised.exception.diagnostic['feature'], 'AI behavior missing component87')

    def test_unknown_backend_and_replaced_context_cannot_succeed(self):
        for unknown in (NotImplemented, object()):
            with self.subTest(unknown=unknown):
                component, state, _, _ = self.fixture()
                state.begin()
                component.world.context = unknown
                with self.assertRaises(runtime.UnsupportedScriptError):
                    state.timer(2)
        component, state, other, owner = self.fixture()
        state.begin()
        state.ai_previous = 0x10001

        def replace(token):
            component.world.context = ai_behavior.Context(component)
            return other

        owner.resolve = replace
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            state.timer(1)
        self.assertEqual(raised.exception.diagnostic['feature'], 'AI behavior component87 replacement')
        self.assertIsNone(component.world.context.current)

    def test_failure_detaches_current_and_pending_without_following_plugs(self):
        component, state, _, owner = self.fixture()
        state.begin()
        owner.current = owner.pending = state
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(state.instance, 'test failure', ai_behavior.CLASS)
        self.assertIsNone(owner.current)
        self.assertIsNone(owner.pending)
        self.assertNotIn(7, state.instance.vars)

    def test_unrecognized_timer_does_not_query_unknown_backend(self):
        component, state, _, _ = self.fixture()
        component.world.context = NotImplemented
        state.begin()
        with patch.object(component.world, 'ai_behavior_context', side_effect=AssertionError('lookup')):
            state.timer(0)
            state.timer(3)
        self.assertNotIn(7, state.instance.vars)

    def test_unimplemented_strict_target_search_cannot_return_empty_success(self):
        component, state, _, owner = self.fixture()
        instance = component.create(graph([
            node(0, ai_behavior.CLASS, state=0, m_6F92F4BD=plug((1, 'm_beginPlug'))),
            node(1, 'STUStatescriptStateTrackTargets', state=1),
        ]), init=False)
        state = instance.state(0)
        instance.init()
        state.begin()
        state.timer(2)
        self.assertIs(owner.current, state)
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_now()
        self.assertEqual(raised.exception.diagnostic['feature'], 'native target search')
        self.assertIsNone(owner.current)

    def test_saved_native_handle_is_passive_vm_storage(self):
        component = runtime.Component(123)
        instance = component.create(graph([node(0, ai_behavior.CLASS, state=0)]), init=False)
        state = instance.state(0)
        before = snapshot_digest(export_component_vm(component))
        state.ai_previous = 0x10001
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
