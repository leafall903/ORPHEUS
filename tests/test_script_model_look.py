"""Native model-look scope timing, cache ordering and explicit service lifetimes."""

import unittest
from unittest import mock

from ow174.game.script import expr, model_look, nodes, runtime, scene, scopes
from tests.test_script_runtime_budget import graph, node

OWNER, SELECTED, PREVIOUS = 0x80000A14, 0x80000020, 0x80000021


def literal(value):
    return {"$": "STUConfigVarLiteral", "value": value}


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'STUConfigVarLiteral':
            return cfg['value']
        return super().native(cfg, instance)


def setup_state(*, strict=True, host=False, **fields):
    world = ValueWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=strict)
    registry = scopes.ForwardHost(component) if host else scopes.Registry()
    if host:
        registry.set_frame(20)
    world.set_subgraph_scopes(OWNER, registry)
    instance = component.create(graph([node(0, model_look.CLASS, state=0, **fields)]), init=False)
    instance.initialized = True
    state = instance.state(0)
    events = []
    selected, previous = model_look.Target(SELECTED, True), model_look.Target(PREVIOUS, True)
    context = model_look.Context(OWNER, True,
        lookup=lambda identifier: events.append(('lookup', identifier)) or selected,
        fallback_policy=lambda state: events.append(('policy',)) or True,
        fallback=lambda state, handle, policy: events.append(('fallback', handle, policy)) or SELECTED,
        resolve_cached=lambda identifier: events.append(('cached', identifier)) or previous,
        apply=lambda state, target, look: events.append(
            ('apply', target.entity, look, state.look_target)) or True,
        remove=lambda state, target, look: events.append(('remove', target.entity, look, state.look_target)),
        exit_suppressed=lambda state: False)
    world.set_model_look_context(OWNER, context)
    return world, component, instance, state, registry, context, events


class ModelLookTests(unittest.TestCase):
    def test_unknown_scope_registration_is_not_silently_skipped(self):
        world = scene.SceneWorld()
        world.bind(OWNER)
        component = runtime.Component(OWNER, world=world, strict=True)
        instance = component.create(graph([node(0, model_look.CLASS, state=0)]), init=False)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            instance.state(0)
        self.assertEqual(error.exception.diagnostic['feature'], 'subgraph scope registration')

    def test_scope_service_is_required_before_begin_and_context_is_required_at_callback(self):
        world, component, instance, state, registry, _, _ = setup_state()
        world.look_contexts.clear()
        state.begin()
        self.assertTrue(state.active)
        self.assertNotIn(state, instance.ticking)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.enter(state)
        self.assertEqual(error.exception.diagnostic['feature'], 'model look context')
        self.assertFalse(state.active)
        self.assertIs(component.fault, error.exception)
        self.assertFalse(registry.states)

    def test_forward_host_dispatches_enter_and_exit_separately_from_begin_end(self):
        _, _, instance, state, host, _, events = setup_state(host=True,
            m_BBBF2499=literal(expr.Entity(SELECTED)), m_CC4BEAB2=literal(expr.Asset(19)))
        state.begin()
        self.assertEqual(events, [])
        host.drain()
        self.assertEqual(events, [('lookup', SELECTED), ('apply', SELECTED, 19, SELECTED)])
        self.assertEqual((state.look, state.look_target), (19, SELECTED))
        self.assertIn(state, instance.ticking)
        events.clear()
        state.end(False)
        self.assertEqual(events, [])
        host.drain()
        self.assertEqual(events, [('cached', SELECTED), ('remove', PREVIOUS, 19, SELECTED)])
        self.assertEqual((state.look, state.look_target), (19, model_look.INVALID_TARGET))

    def test_enter_evaluates_target_twice_only_for_fallback_then_evaluates_look(self):
        _, _, instance, state, registry, _, events = setup_state(
            m_BBBF2499=literal(None), m_CC4BEAB2=literal(None))
        handle = expr.Handle(1, 2, 3)
        values = iter((None, (handle,), expr.Asset(23)))

        def evaluate(cfg, *args, **kwargs):
            events.append(('evaluate', 'target' if cfg is state.node.field('m_BBBF2499') else 'look'))
            return next(values)

        with mock.patch.object(instance, 'evaluate', side_effect=evaluate):
            registry.enter(state)
        self.assertEqual(events, [('evaluate', 'target'), ('policy',), ('evaluate', 'target'),
            ('fallback', handle, True), ('lookup', SELECTED), ('evaluate', 'look'),
            ('apply', SELECTED, 23, SELECTED)])

    def test_failed_apply_commits_target_before_call_but_retains_the_old_look(self):
        _, _, _, state, registry, context, events = setup_state(
            m_BBBF2499=literal(expr.Entity(SELECTED)), m_CC4BEAB2=literal(expr.Asset(23)))
        state.look = 11
        context.apply = lambda state, target, look: events.append(
            ('apply-failed', state.look_target, look)) or False
        registry.enter(state)
        self.assertEqual((state.look, state.look_target), (11, SELECTED))
        self.assertEqual(events[-1], ('apply-failed', SELECTED, 23))

    def test_missing_namespace_target_or_capability_queues_abort_without_immediate_cleanup(self):
        for missing in ('namespace', 'target', 'capability'):
            with self.subTest(missing=missing):
                _, _, instance, state, registry, context, events = setup_state(
                    m_BBBF2499=literal(expr.Entity(SELECTED)), m_CC4BEAB2={'$': 'Unknown'})
                state.begin()
                if missing == 'namespace':
                    context.namespace_present = False
                else:
                    context.lookup = lambda identifier, missing=missing: (
                        None if missing == 'target' else model_look.Target(SELECTED, False))
                registry.enter(state)
                self.assertTrue(state.active)
                self.assertEqual([(event.kind, event.op) for event in instance.queue],
                                 [(runtime.STATE_OP, runtime.ABORT)])
                self.assertEqual(state.look_target, model_look.INVALID_TARGET)
                self.assertFalse(any(row[0] == 'apply' for row in events))

    def test_nonzero_invalid_entity_is_sent_to_lookup_without_fallback(self):
        for identifier in (0xFFFFFFFF, 0x1FFFFFFF):
            with self.subTest(identifier=identifier):
                _, _, _, state, registry, _, events = setup_state(
                    m_BBBF2499=literal((expr.Entity(identifier),)))
                registry.enter(state)
                self.assertEqual(events[0], ('lookup', identifier))
                self.assertFalse(any(row[0] == 'fallback' for row in events))

    def test_tick_samples_look_first_and_skips_unchanged_or_unresolved_previous_target(self):
        for previous in ('same', 'missing', 'unsupported'):
            with self.subTest(previous=previous):
                _, _, instance, state, _, context, events = setup_state(
                    m_BBBF2499=literal(expr.Entity(SELECTED)), m_CC4BEAB2=literal(expr.Asset(17)))
                state.look, state.look_target = (17, SELECTED) if previous == 'same' else (9, PREVIOUS)
                if previous != 'same':
                    context.resolve_cached = lambda identifier, previous=previous: (
                        None if previous == 'missing' else model_look.Target(PREVIOUS, False))
                original = instance.evaluate

                def evaluate(cfg, *args, events=events, state=state, original=original, **kwargs):
                    events.append(('evaluate', 'target' if cfg is state.node.field('m_BBBF2499') else 'look'))
                    return original(cfg, *args, **kwargs)

                with mock.patch.object(instance, 'evaluate', side_effect=evaluate):
                    state.tick()
                self.assertEqual(events[:2], [('evaluate', 'look'), ('evaluate', 'target')])
                self.assertFalse(any(row[0] in ('apply', 'remove') for row in events))
                self.assertEqual(state.look_target, SELECTED if previous == 'same' else PREVIOUS)

    def test_tick_removes_previous_then_applies_and_keeps_look_if_apply_fails(self):
        _, _, _, state, _, context, events = setup_state(
            m_BBBF2499=literal(expr.Entity(SELECTED)), m_CC4BEAB2=literal(expr.Asset(23)))
        state.look, state.look_target = 11, PREVIOUS
        context.apply = lambda state, target, look: events.append(
            ('apply-failed', target.entity, look)) or False
        state.tick()
        self.assertEqual(events, [('lookup', SELECTED), ('cached', PREVIOUS),
            ('remove', PREVIOUS, 11, SELECTED), ('apply-failed', SELECTED, 23)])
        self.assertEqual((state.look, state.look_target), (11, SELECTED))

    def test_exit_bit6_skips_namespace_and_reset_and_exit_needs_no_target_capability(self):
        _, _, _, state, registry, context, events = setup_state()
        state.look, state.look_target = 9, PREVIOUS
        context.exit_suppressed = lambda state: True
        registry.exit(state)
        self.assertEqual(events, [])
        self.assertEqual(state.look_target, PREVIOUS)
        context.exit_suppressed = lambda state: False
        context.resolve_cached = lambda identifier: model_look.Target(PREVIOUS, False)
        registry.exit(state)
        self.assertEqual(events, [('remove', PREVIOUS, 9, PREVIOUS)])
        self.assertEqual((state.look, state.look_target), (9, model_look.INVALID_TARGET))

    def test_missing_namespace_exit_resets_identity_without_requiring_lookup(self):
        _, _, _, state, registry, context, events = setup_state()
        state.look, state.look_target = 19, PREVIOUS
        context.namespace_present, context.resolve_cached = False, NotImplemented
        registry.exit(state)
        self.assertEqual(events, [])
        self.assertEqual((state.look, state.look_target), (19, model_look.INVALID_TARGET))

    def test_entity_retirement_and_reuse_require_a_fresh_context(self):
        world, _, _, state, _, context, _ = setup_state()
        world.unbind(OWNER)
        self.assertTrue(context.retired)
        world.bind(OWNER)
        self.assertIs(world.model_look_context(OWNER), NotImplemented)
        with self.assertRaises(ValueError):
            world.set_model_look_context(OWNER, context)
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.tick()

    def test_context_binding_rejects_replacement_wrong_owner_and_unknown_namespace(self):
        world, _, _, _, _, context, _ = setup_state()
        world.set_model_look_context(OWNER, context)
        for invalid in (None, model_look.Context(OWNER, True), model_look.Context(SELECTED, True)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                world.set_model_look_context(OWNER, invalid)
        with self.assertRaises(ValueError):
            model_look.Context(OWNER, NotImplemented)

    def test_nonboolean_apply_result_faults_and_retires_the_context(self):
        _, component, _, state, registry, context, _ = setup_state(
            m_BBBF2499=literal(expr.Entity(SELECTED)))
        context.apply = lambda *args: 1
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.enter(state)
        self.assertEqual(error.exception.diagnostic['feature'], 'model look apply')
        self.assertIs(component.fault, error.exception)
        self.assertTrue(context.retired)

    def test_missing_exit_flags_fault_before_lookup_and_do_not_reset_identity(self):
        _, _, _, state, registry, context, events = setup_state()
        state.look_target = PREVIOUS
        context.exit_suppressed = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            registry.exit(state)
        self.assertEqual(error.exception.diagnostic['feature'], 'model look exit flags')
        self.assertEqual(events, [])
        self.assertEqual(state.look_target, PREVIOUS)

    def test_retirement_inside_callback_stops_before_subsequent_engine_writes(self):
        world, component, _, state, registry, context, events = setup_state(
            m_BBBF2499=literal(expr.Entity(SELECTED)))
        context.lookup = lambda identifier: world.unbind(OWNER) and model_look.Target(SELECTED, True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            registry.enter(state)
        self.assertIsNotNone(component.fault)
        self.assertTrue(context.retired)
        self.assertFalse(any(row[0] == 'apply' for row in events))

    def test_dependency_changes_do_not_invent_a_native_C8_refresh(self):
        _, component, instance, state, registry, _, events = setup_state(
            m_BBBF2499=literal(expr.Entity(SELECTED)),
            m_CC4BEAB2={'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0})
        instance.find_var(0, 7).base = expr.Asset(11)
        state.begin()
        registry.enter(state)
        instance.ticking.clear()  # Isolate CHANGED from the independent native +70 tick.
        events.clear()
        instance.write((0, 7), expr.Asset(19))
        component.run_now()
        self.assertEqual(events, [])
        self.assertEqual(state.look, 11)

    def test_model_look_literal_is_typed_and_nonzero_key_uses_explicit_availability(self):
        for value in (None, 0, '0x09800000000080D0'):
            cfg = {'$': 'STUConfigVarModelLook', 'm_modelLook': value, 'm_resourceKey': None}
            self.assertEqual(expr.evaluate(cfg, expr.Context()), expr.Asset(expr.guid_of(value)))
        cfg['m_resourceKey'] = '0x123'
        with mock.patch.object(expr.Context, 'native', return_value=expr.Asset(7)) as native:
            self.assertEqual(expr.evaluate(cfg, expr.Context()), expr.Asset(7))
            native.assert_called_once_with(cfg)

    def test_legacy_permissive_model_look_stays_passive(self):
        _, _, _, state, _, _, events = setup_state(strict=False)
        state.begin()
        state.scope_enter()
        state.tick()
        state.scope_exit()
        state.end(False)
        self.assertEqual(events, [])
        self.assertEqual((state.look, state.look_target), (0, model_look.INVALID_TARGET))
        self.assertIs(type(state), nodes.ModelLook)
