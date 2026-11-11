"""CF4340 delegation protocol; these host adapters do not implement engine filters."""

import unittest

from ow174.game.script import entity_filter, expr, runtime
from tests.test_script_runtime_budget import graph, node


class FilterWorld(runtime.World):
    reply = True

    def __init__(self):
        super().__init__()
        self.calls = []

    def matches_entity_filter(self, entity, cfg, *, instance, watcher):
        self.calls.append((entity, cfg, instance, watcher))
        return self.reply


class AdmissionWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.condition = ()
        self.tag_set = entity_filter.TagSet()

    def entity_filter_host(self, instance):
        return self

    def lookup(self, entity):
        return entity_filter.Target(entity, True, 64)

    def source(self, instance):
        return None

    def tags(self, target):
        self.calls.append('tags')
        return self.tag_set

    def custom(self, target, cfg, instance):
        self.calls.append('custom')
        return 2

    def native(self, cfg, instance):
        if cfg.get('$') == 'ProbeCondition':
            self.calls.append(instance.context().param(8))
            return self.condition
        return super().native(cfg, instance)


class EntityFilterTests(unittest.TestCase):
    def scene(self, world=None):
        world = FilterWorld() if world is None else world
        component = runtime.Component(123, strict=True, world=world)
        instance = component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
        instance.initialized = True
        return component, instance, world

    def test_target_filter_instance_and_watcher_are_forwarded_without_rewriting(self):
        _, instance, world = self.scene()
        state = instance.state(0)
        instance.write((0, 41), (expr.Entity(77),))
        target_filter = {'$': 'STUConfigVarFilter', 'm_84065FD1': 64}
        cfg = {'$': 'STU_AC829876', 'm_entity': {
            '$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0}, 'm_filter': target_filter}
        self.assertIs(instance.evaluate(cfg, watcher=state), True)
        self.assertEqual(world.calls, [(77, target_filter, instance, state)])
        self.assertIs(world.calls[0][1], target_filter)

    def test_missing_invalid_and_failed_targets_still_call_the_host_with_zero(self):
        for provider in (None, {'$': 'STUConfigVarInt', 'm_value': 77}, {'$': 'FailedSelector'}):
            with self.subTest(provider=provider):
                class FailedWorld(FilterWorld):
                    def native(self, cfg, instance):
                        if cfg.get('$') == 'FailedSelector':
                            return expr.EVALUATION_FAILED
                        return super().native(cfg, instance)

                _, instance, world = self.scene(FailedWorld())
                self.assertIs(instance.evaluate({'$': 'STU_AC829876', 'm_entity': provider}), True)
                self.assertEqual(world.calls, [(0, None, instance, None)])

    def test_false_is_a_successful_result_and_nonbool_host_values_are_not_guessed(self):
        _, instance, world = self.scene()
        world.reply = False
        self.assertIs(instance.evaluate({'$': 'STU_AC829876'}), False)
        for reply in (None, 1, (), expr.Entity(1)):
            with self.subTest(reply=reply):
                _, instance, world = self.scene()
                world.reply = reply
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'host result must be bool'):
                    instance.evaluate({'$': 'STU_AC829876'})

    def test_unavailable_service_remains_an_explicit_strict_boundary(self):
        _, instance, _ = self.scene(runtime.World())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'STU_AC829876'):
            instance.evaluate({'$': 'STU_AC829876'})

    def test_admission_binds_target_for_condition_and_restores_outer_scope_before_post(self):
        component, instance, world = self.scene(AdmissionWorld())
        instance.write((0, 41), expr.Entity(77))
        probe = {'$': 'ProbeCondition'}
        cfg = {'$': entity_filter.CLASS, 'm_entity': {
            '$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0},
            'm_filter': {'m_84065FD1': 64, 'm_condition': probe, 'm_F61726FE': probe}}
        outer = {8: expr.Entity(99)}
        with component.parameter_scope(outer):
            self.assertTrue(instance.evaluate(cfg))
            self.assertEqual(component.parameter_scopes, [outer])
        self.assertEqual(world.calls, [expr.Entity(77), 'custom', expr.Entity(99)])
        self.assertEqual(component.parameter_scopes, [])

    def test_condition_rejection_skips_tags_and_custom(self):
        _, instance, world = self.scene(AdmissionWorld())
        world.condition = expr.Vec3(float('nan'), 0, 0)
        cfg = {'$': entity_filter.CLASS, 'm_filter': {
            'm_condition': {'$': 'ProbeCondition'},
            'm_8B2A4DF3': {'$': 'STU_357DABBB', 'm_7D7C86A1': 0x0D6000000000003A}}}
        self.assertFalse(instance.evaluate(cfg))
        self.assertEqual(world.calls, [None])

    def test_host_retirement_inside_condition_restores_parameter_scope(self):
        class RetiringWorld(AdmissionWorld):
            def native(self, cfg, instance):
                instance.component.destroy(instance)
                return False

        component, instance, _ = self.scene(RetiringWorld())
        instance.write((0, 41), expr.Entity(77))
        cfg = {'$': entity_filter.CLASS, 'm_entity': {
            '$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0},
            'm_filter': {'m_condition': {'$': 'ProbeCondition'}}}
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired during callback'):
            instance.evaluate(cfg)
        self.assertEqual(component.parameter_scopes, [])

    def test_retired_owner_during_host_callback_cannot_return_a_stale_result(self):
        class RetiringWorld(FilterWorld):
            def matches_entity_filter(self, entity, cfg, *, instance, watcher):
                instance.component.destroy(instance)
                return True

        _, instance, _ = self.scene(RetiringWorld())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired during callback'):
            instance.evaluate({'$': 'STU_AC829876'})

    def test_retired_owner_during_target_evaluation_never_calls_filter_service(self):
        class RetiringWorld(FilterWorld):
            def native(self, cfg, instance):
                if cfg.get('$') == 'RetiringSelector':
                    instance.component.destroy(instance)
                    return expr.Entity(77)
                return super().native(cfg, instance)

        _, instance, world = self.scene(RetiringWorld())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired during callback'):
            instance.evaluate({'$': 'STU_AC829876', 'm_entity': {'$': 'RetiringSelector'}})
        self.assertEqual(world.calls, [])
