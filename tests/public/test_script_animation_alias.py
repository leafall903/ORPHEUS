"""Native alias handle ordering, queued abort and strict engine boundaries."""

import unittest

from ow174.game import body_vm_snapshot, replay_values
from ow174.game.script import animation_alias as aliases
from ow174.game.script import expr, nodes, runtime, scene
from tests.test_script_runtime_budget import graph, node

OWNER = 0x800001C6


def literal(value):
    return {"$": "AliasTestValue", "value": value}


class World(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get("$") == "AliasTestValue":
            return cfg["value"]
        return super().native(cfg, instance)


class AnimationAliasTests(unittest.TestCase):
    def setup_state(self, *, strict=True, **fields):
        world = World()
        world.bind(OWNER)
        component = runtime.Component(OWNER, world=world, strict=strict)
        instance = component.create(graph([node(0, aliases.CLASS, state=0, **fields)]), init=False)
        instance.initialized = True
        return world, component, instance, instance.state(0)

    def backend(self, world, **options):
        events = []
        backend = aliases.Aliases(OWNER, capacity=8, **options)
        backend.notify = lambda: events.append((tuple(backend.rows), backend.current, backend.mode))
        world.set_animation_aliases(OWNER, backend)
        return backend, events

    def test_unknown_component_faults_instead_of_silently_ignoring_the_request(self):
        _, component, _, state = self.setup_state()
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertEqual(component.fault.diagnostic["feature"], "animation alias component")
        self.assertFalse(state.active)

    def test_explicit_absence_skips_config_evaluation_and_preserves_cleanup_handle(self):
        world, _, _, state = self.setup_state(m_11E0A658={"$": "Unknown"})
        world.set_animation_aliases(OWNER, None)
        state.alias_handle = 19
        state.begin()
        self.assertEqual(state.alias_handle, 0)
        state.alias_handle = 7
        state.end(False)
        self.assertEqual(state.alias_handle, 7)

    def test_begin_pushes_typed_alias_and_cleanup_keeps_the_successful_handle(self):
        world, _, _, state = self.setup_state(m_11E0A658=literal(expr.Asset(0x0C80000000000015)))
        backend, events = self.backend(world)
        state.begin()
        self.assertEqual(backend.rows, [(1, 0x0C80000000000015)])
        self.assertEqual(state.alias_handle, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(backend.current, 0)  # Notification does not select an alias itself.
        state.end(False)
        self.assertEqual(backend.rows, [])
        self.assertEqual(state.alias_handle, 1)
        self.assertEqual(len(events), 1)  # No current alias changed in the supplied engine.
        state.on_cleanup()
        self.assertEqual(state.alias_handle, 0)

    def test_mode_true_notifies_then_queues_abort_without_immediate_cleanup(self):
        world, _, instance, state = self.setup_state(
            m_9507488A=literal(True), m_11E0A658={"$": "Unknown"},
        )
        backend, events = self.backend(world, current=17)
        state.begin()
        self.assertTrue(state.active)
        self.assertTrue(backend.mode)
        self.assertEqual(events, [((), 17, True)])
        self.assertEqual([(event.kind, event.op) for event in instance.queue],
                         [(runtime.STATE_OP, runtime.ABORT)])
        instance.component.run_now()
        self.assertFalse(state.active)
        self.assertEqual(backend.rows, [])

    def test_failed_and_successful_null_configs_keep_distinct_evaluation_paths(self):
        for value in (expr.EVALUATION_FAILED, None, False, 7, (), (expr.Asset(19),)):
            with self.subTest(value=value):
                world, _, _, state = self.setup_state(
                    m_9507488A=literal(expr.EVALUATION_FAILED), m_11E0A658=literal(value),
                )
                backend, _ = self.backend(world)
                state.begin()
                self.assertFalse(backend.mode)
                self.assertEqual(backend.rows, [(1, 19 if type(value) is tuple and value else 0)])

    def test_variable_changes_do_not_invent_a_dependency_refresh_for_this_class(self):
        dynamic = {"$": "STUConfigVarDynamic", "m_identifier": 7, "m_60DB8F99": 0}
        world, component, instance, state = self.setup_state(m_11E0A658=dynamic)
        backend, events = self.backend(world)
        instance.find_var(0, 7).base = expr.Asset(11)
        state.begin()
        instance.write((0, 7), expr.Asset(15))
        component.run_now()
        self.assertEqual(backend.rows, [(1, 11)])
        self.assertEqual(len(events), 1)

    def test_duplicate_handles_remove_the_last_match_and_compact_in_order(self):
        world, _, _, state = self.setup_state()
        backend, events = self.backend(world, current=25)
        backend.rows = [(7, 11), (9, 13), (7, 25), (10, 31)]
        state.alias_handle = 7
        state.on_cleanup()
        self.assertEqual(backend.rows, [(7, 11), (9, 13), (10, 31)])
        self.assertEqual(state.alias_handle, 7)
        self.assertEqual(events, [])

    def test_removing_last_notifies_only_when_new_last_differs_from_current(self):
        for rows, current, expected in (
            ([(1, 11), (2, 25)], 25, 1), ([(1, 11), (2, 25)], 11, 0),
            ([(2, 25)], 25, 1), ([(2, 25)], 0, 0),
        ):
            with self.subTest(rows=rows, current=current):
                world, _, _, state = self.setup_state()
                backend, events = self.backend(world, current=current)
                backend.rows = rows.copy()
                state.alias_handle = 2
                state.on_cleanup()
                self.assertEqual(backend.rows, rows[:-1])
                self.assertEqual(len(events), expected)
                self.assertEqual(state.alias_handle, 2)

    def test_allocation_failure_still_consumes_handle_and_notifies(self):
        world, _, _, state = self.setup_state(m_11E0A658=literal(expr.Asset(11)))
        backend = aliases.Aliases(OWNER, capacity=0, counter=0xFFFFFFFF, reserve=lambda needed: None)
        events = []
        backend.notify = lambda: events.append((backend.counter, state.alias_handle))
        world.set_animation_aliases(OWNER, backend)
        state.begin()
        self.assertEqual((backend.counter, state.alias_handle, backend.rows), (0, 0, []))
        self.assertEqual(events, [(0, 0)])

    def test_growth_and_notification_callbacks_keep_the_sampled_handle(self):
        world, _, _, state = self.setup_state(m_11E0A658=literal(expr.Asset(11)))
        backend = aliases.Aliases(OWNER, capacity=0)

        def reserve(needed):
            backend.counter = 17
            return needed + 1

        def notify():
            backend.counter = 25
            state.alias_handle = 91

        backend.reserve, backend.notify = reserve, notify
        world.set_animation_aliases(OWNER, backend)
        state.begin()
        self.assertEqual(backend.rows, [(1, 11)])
        self.assertEqual((backend.capacity, backend.counter, state.alias_handle), (2, 25, 1))

    def test_unknown_allocator_and_notification_are_strict_services(self):
        for capacity in (0, 8):
            with self.subTest(capacity=capacity):
                world, component, _, state = self.setup_state(m_11E0A658=literal(expr.Asset(11)))
                backend = aliases.Aliases(OWNER, capacity=capacity)
                world.set_animation_aliases(OWNER, backend)
                with self.assertRaises(runtime.UnsupportedScriptError):
                    state.begin()
                expected = "animation alias allocation" if not capacity else "animation alias notification"
                self.assertEqual(component.fault.diagnostic["feature"], expected)
                self.assertTrue(backend.retired)
                self.assertFalse(backend.rows)

    def test_retirement_during_config_or_consumer_stops_further_work(self):
        for during in ("config", "notify"):
            with self.subTest(during=during):
                world, component, _, state = self.setup_state(m_11E0A658=literal(expr.Asset(11)))
                backend, _ = self.backend(world)
                if during == "config":
                    def native(cfg, instance, world=world):
                        world.unbind(OWNER)
                        return expr.Asset(11)
                    world.native = native
                else:
                    backend.notify = lambda world=world: world.unbind(OWNER)
                with self.assertRaises(runtime.UnsupportedScriptError):
                    state.begin()
                self.assertIn("retired", component.fault.diagnostic["feature"])
                self.assertTrue(backend.retired)
                self.assertFalse(backend.rows)
                self.assertEqual(state.alias_handle, 0)

    def test_callback_registry_mutation_is_gated_and_cleared_on_failure(self):
        world, _, _, state = self.setup_state(m_11E0A658=literal(expr.Asset(11)))
        backend, _ = self.backend(world)
        backend.notify = lambda: backend.rows.append((91, 17))
        with self.assertRaises(runtime.UnsupportedScriptError):
            state.begin()
        self.assertTrue(backend.retired)
        self.assertFalse(backend.rows)

    def test_binding_replacement_wrong_owner_and_reusing_retired_backend_are_rejected(self):
        world, _, _, _ = self.setup_state()
        backend, _ = self.backend(world)
        with self.assertRaises(ValueError):
            world.set_animation_aliases(OWNER, None)
        with self.assertRaises(ValueError):
            world.set_animation_aliases(OWNER + 1, backend)
        world.unbind(OWNER)
        world.bind(OWNER)
        with self.assertRaises(ValueError):
            world.set_animation_aliases(OWNER, backend)
        self.assertIs(world.animation_aliases(OWNER), NotImplemented)

    def test_legacy_permissive_body_policy_stays_passive(self):
        _, _, _, state = self.setup_state(strict=False, m_11E0A658={"$": "Unknown"})
        state.begin()
        self.assertTrue(state.active)
        state.end(False)
        self.assertFalse(state.active)

    def test_literal_alias_is_typed_even_at_zero_and_keyed_resolution_stays_explicit(self):
        for value in (None, "0x0C80000000000015"):
            cfg = {"$": "STUConfigVarAnimAlias", "m_resourceKey": None, "m_animAlias": value}
            self.assertEqual(expr.evaluate(cfg, expr.Context()), expr.Asset(expr.guid_of(value)))
        world, _, instance, _ = self.setup_state()
        cfg = {"$": "STUConfigVarAnimAlias", "m_resourceKey": "0x123", "m_animAlias": "0x15"}
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(cfg)
        self.assertFalse(world.alias_components)

    def test_storage_observes_handle_without_querying_external_backend(self):
        _, component, _, state = self.setup_state(strict=False)
        self.assertIsInstance(state, nodes.AnimationAlias)
        state.alias_handle = 17
        component.world = runtime.World()  # External SceneWorld is outside this observation format.
        exported = body_vm_snapshot.export_component_vm(component)
        raw = replay_values.decode(exported["value"])
        stored = next(item for item in raw["objects"]
                      if item.get("class") == "ow174.game.script.nodes.AnimationAlias")
        self.assertEqual(dict(stored["fields"])["alias_handle"], 17)


if __name__ == "__main__":
    unittest.main()
