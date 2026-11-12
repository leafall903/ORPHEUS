"""Search primitives; no entity acquisition or NPC attack acceptance implied."""

import math
import unittest

from ow174.game.script import expr, runtime, target_search
from tests.runtime.test_script_runtime_budget import graph, node


def config(first=0, second=0):
    return {"$": "STUConfigVarFilter", "m_84065FD1": first, "m_1E14CCA8": second}


def parameter(identifier=8):
    return {"$": "STUConfigVarExpression", "m_expression": {
        "m_opcodes": [13, 0, 0], "m_dynamicVars": [{"m_identifier": identifier}]}}


class ConditionWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.components = {}
        self.callback = None

    def entity_var_slot(self, entity, var, *, create=False):
        owner = self.components.get(entity)
        return owner.entity_var(var, create) if owner is not None else None

    def native(self, cfg, instance):
        if cfg.get('$') == 'TestConditionCallback':
            return self.callback(instance)
        return super().native(cfg, instance)


class TargetSearchTests(unittest.TestCase):
    def test_game_zero_masks_bypass_the_direct_mask_helpers_rejection(self):
        self.assertFalse(target_search.mask_matches(config(), 0, relationship=None))
        self.assertTrue(target_search.candidate_masks(config(), None,
                        component97=False, relationship=None))

    def test_game_component97_gate_and_exact_universal_exception(self):
        relation = target_search.Relationship(False, True)
        self.assertFalse(target_search.candidate_masks(config(0x40000), 0x40000,
                         component97=False, relationship=relation))
        self.assertTrue(target_search.candidate_masks(config(0x40000), 0x40000,
                        component97=True, relationship=relation))
        self.assertTrue(target_search.candidate_masks(config(0x3FFFFFFF), None,
                        component97=False, relationship=None))
        self.assertFalse(target_search.candidate_masks(
            {**config(), 'm_89831602': 1}, 0, component97=False, relationship=None))

    def test_game_component_presence_cannot_default_from_unknown(self):
        for presence in (None, NotImplemented, 0, 1):
            with self.assertRaises(ValueError):
                target_search.candidate_masks(config(), 0,
                                              component97=presence, relationship=None)

    def test_assassin_mask_excludes_authored_tags_and_requires_query_result_when_reference_exists(self):
        cfg = {**config(0x40000), 'm_4EA686DB': 312, 'm_89831602': 1}
        other = target_search.Relationship(False, True)
        self.assertTrue(target_search.mask_matches(cfg, 0x40000, relationship=other))
        for relationship in (target_search.Relationship(False, False),
                             target_search.Relationship(True, True),
                             target_search.Relationship(True, False)):
            self.assertFalse(target_search.mask_matches(cfg, 0x40000, relationship=relationship))
        for bit in (8, 16, 32, 256):
            self.assertFalse(target_search.mask_matches(cfg, 0x40000 | bit, relationship=other))
        self.assertFalse(target_search.mask_matches(cfg, 0, relationship=other))

    def test_known_absent_reference_ignores_all_relation_masks(self):
        cfg = {**config(0x40000), 'm_66747E7D': 7, 'm_89831602': 7}
        self.assertTrue(target_search.mask_matches(cfg, 0x40000, relationship=None))
        self.assertFalse(target_search.mask_matches(cfg, 0, relationship=None))

    def test_relation_any_cannot_override_required_or_excluded_tags(self):
        cfg = {**config(), 'm_27CF71D5': 2, 'm_1E14CCA8': 0x80, 'm_4EA686DB': 0x10}
        other = target_search.Relationship(False, True)
        self.assertTrue(target_search.mask_matches(cfg, 0x80, relationship=other))
        self.assertFalse(target_search.mask_matches(cfg, 0, relationship=other))
        self.assertFalse(target_search.mask_matches(cfg, 0x90, relationship=other))

    def test_all_relation_bits_intersect_and_self_is_independent_of_query_result(self):
        cfg = {**config(), 'm_27CF71D5': 4, 'm_66747E7D': 5}
        for result in (False, True):
            self.assertTrue(target_search.mask_matches(cfg, 0,
                            relationship=target_search.Relationship(True, result)))
            self.assertFalse(target_search.mask_matches(cfg, 0,
                             relationship=target_search.Relationship(False, result)))
        cfg['m_66747E7D'] = 3
        self.assertFalse(target_search.mask_matches(cfg, 0,
                         relationship=target_search.Relationship(True, True)))

    def test_exact_universal_tag_sentinel_and_high_relation_bits(self):
        self.assertTrue(target_search.mask_matches(config(0x3FFFFFFF), None, relationship=None))
        self.assertFalse(target_search.mask_matches(config(0xFFFFFFFF), None, relationship=None))
        cfg = {**config(1), 'm_27CF71D5': 1 << 63, 'm_66747E7D': 1 << 63,
               'm_89831602': 1 << 63}
        self.assertTrue(target_search.mask_matches(cfg, 1,
                        relationship=target_search.Relationship(False, False)))
        self.assertFalse(target_search.mask_matches(cfg, 0,
                         relationship=target_search.Relationship(False, False)))

    def test_unknown_tags_relationships_and_malformed_masks_fail_explicitly(self):
        for tags, relationship in ((NotImplemented, None), (0, NotImplemented), (0, True)):
            with self.assertRaises(ValueError):
                target_search.mask_matches(config(), tags, relationship=relationship)
        for value in (-1, 1 << 64, True, None):
            with self.assertRaises(ValueError):
                target_search.mask_matches({**config(), 'm_4EA686DB': value}, 0, relationship=None)
        with self.assertRaises(ValueError):
            target_search.Relationship(0, False)

    def condition_fixture(self):
        world = ConditionWorld()
        component = runtime.Component(123, world=world, strict=True)
        world.components[123] = component
        first = component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
        second = component.create(graph(), init=False)
        first.initialized = second.initialized = True
        return world, component, first, second

    def test_candidate_provider_uses_exact_full_key_and_preserves_failed_destination(self):
        for identifier in (0, 1, 0xFFFFFFFF, 0xC000100F):
            provider = target_search.CandidateParameter(identifier)
            self.assertEqual(provider.lookup_guid(0x0A40000000000008), (True, expr.Entity(identifier)))
            for key in (8, 0x0A50000000000008, 0x0A40000000000009):
                self.assertEqual(provider.lookup_guid(key, initial=99), (False, 99))
            self.assertEqual(provider.lookup_parameter(8), (True, expr.Entity(identifier)))
            self.assertEqual(provider.lookup_parameter(True, initial=99), (False, 99))
        for identifier in (-1, 1 << 32, True, None):
            with self.assertRaises(ValueError):
                target_search.CandidateParameter(identifier)

    def test_absent_condition_succeeds_without_instance_but_authored_condition_requires_one(self):
        self.assertTrue(target_search.condition(config(), None, 123))
        cfg = {**config(), 'm_condition': {'$': 'STUConfigVarBool', 'm_value': True}}
        self.assertFalse(target_search.condition(cfg, None, 123))

    def test_assassin_condition_reads_candidate_member_and_registers_receiver_dependency(self):
        _, component, instance, _ = self.condition_fixture()
        # Derived 13C2 condition: !param(8).id(14695), with actual VM opcodes.
        condition = {'$': 'STUConfigVarExpression', 'm_expression': {
            'm_opcodes': [13, 0, 7, 1, 15, 30, 0],
            'm_dynamicVars': [{'m_identifier': 8}, {'m_identifier': 14695}]}}
        cfg = {**config(0x40000), 'm_condition': condition}
        candidate = runtime.Component(456, world=component.world, strict=True)
        component.world.components[456] = candidate
        target = candidate.entity_var(14695)
        target.base = True
        watcher = instance.state(0)
        watcher.begin()
        with component.parameter_scope({8: expr.Entity(123)}):
            self.assertFalse(target_search.condition(cfg, instance, 456, watcher=watcher))
            target.base = False
            self.assertTrue(target_search.condition(cfg, instance, 456, watcher=watcher))
            self.assertEqual(instance.context().param(8), expr.Entity(123))
        self.assertIn(watcher, target.watchers)
        self.assertFalse(component.parameter_scopes)

    def test_invalid_candidate_leaves_outer_parameter_visible(self):
        _, component, instance, _ = self.condition_fixture()
        cfg = {**config(), 'm_condition': parameter()}
        with component.parameter_scope({8: False}):
            self.assertTrue(target_search.condition(cfg, instance, 456))
            for identifier in (0, 0x1FFFFFFF, 0x3FFFFFFF, 0xFFFFFFFF):
                self.assertFalse(target_search.condition(cfg, instance, identifier))
            self.assertEqual(instance.context().param(8), False)

    def test_nested_queries_share_component_scope_and_restore_it_after_failure(self):
        world, component, first, second = self.condition_fixture()
        callback = {'$': 'TestConditionCallback'}
        inner = {**config(), 'm_condition': parameter()}
        outer = {**config(), 'm_condition': callback}
        seen = []

        def evaluate(instance):
            seen.append((instance.context().param(8), second.context().param(8)))
            self.assertTrue(target_search.condition(inner, second, 789))
            seen.append(second.context().param(8))
            raise RuntimeError('condition service failed')

        world.callback = evaluate
        with component.parameter_scope({8: 42}):
            with self.assertRaisesRegex(RuntimeError, 'condition service failed'):
                target_search.condition(outer, first, 456)
            self.assertEqual(first.context().param(8), 42)
        self.assertEqual(seen, [(expr.Entity(456), expr.Entity(456)), expr.Entity(456)])
        self.assertFalse(component.parameter_scopes)

    def test_boolean_evaluator_failure_default_and_null_are_distinct(self):
        world, _, instance, _ = self.condition_fixture()
        cfg = {**config(), 'm_condition': {'$': 'TestConditionCallback'}}
        for value, expected in ((expr.EVALUATION_FAILED, True), (None, False),
                                (0, False), (2, True), (255, True)):
            with self.subTest(value=value):
                world.callback = lambda instance, value=value: value
                self.assertEqual(target_search.condition(cfg, instance, 456), expected)

    def test_category_priority_and_primary_secondary_asymmetry(self):
        self.assertEqual(target_search.category(0, 0x500C1), 1)
        self.assertEqual(target_search.category(0, 0x500C0), 0x40000)
        self.assertEqual(target_search.category(0, 0x10080), 0x40)
        self.assertEqual(target_search.category(0, 0x10000), 0x10000)
        self.assertEqual(target_search.category(0x10000, 0), 0)
        self.assertEqual(target_search.category(0x40001, 0), 0)
        self.assertEqual(target_search.category(0x80, 0), 0x40)

    def test_direct_mixed_and_absent_routes_preserve_dispatch_order(self):
        self.assertEqual(target_search.route(config(0x40000)), target_search.Route((0x40000,), False))
        self.assertEqual(target_search.route(config(0x40001)), target_search.Route((0x150041,), False))
        self.assertEqual(target_search.route(config(0x50001)), target_search.Route((0x150041,), True))
        self.assertEqual(target_search.route(config(0x10000)), target_search.Route((), True))
        self.assertEqual(target_search.route(None), target_search.Route((), True))

    def test_masks_and_filter_class_are_validated(self):
        for invalid in (-1, 1 << 64, True, 1.0, None):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                target_search.category(invalid, 0)
        with self.assertRaises(ValueError):
            target_search.route({"$": "UnknownFilter"})

    def test_radial_outer_contact_is_inclusive_and_outside_misses(self):
        hit = target_search.radial_capsule
        self.assertTrue(hit((0, 1, 0), 5, 0, (0, 0, 5.5), 0.5, 1.5))
        self.assertFalse(hit((0, 1, 0), 5, 0, (0, 0, 5.5001), 0.5, 1.5))

    def test_vertical_capsule_and_horizontal_policy_are_distinct(self):
        hit = target_search.radial_capsule
        self.assertTrue(hit((0, 1, 0), 0, 0, (0, 0, 0), 0.5, 2))
        self.assertFalse(hit((0, 4, 0), 0, 0, (0, 0, 0), 0.5, 2))
        self.assertTrue(hit((0, 4, 0), 0, 0, (0, 0, 0), 0.5, 2, horizontal=True))

    def test_inner_hole_accounts_for_capsule_extent_and_keeps_contact(self):
        hit = target_search.radial_capsule
        self.assertFalse(hit((0, 0, 0), 10, 4, (0, -1, 2.9), 0.5, 2))
        self.assertTrue(hit((0, 0, 0), 10, 4, (0, -1, 3), 0.5, 2))

    def test_invalid_geometry_does_not_acquire_a_target(self):
        for invalid in (math.nan, math.inf, -1, True, 1e100):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                target_search.radial_capsule((0, 0, 0), invalid, 0, (0, 0, 0), 0.5, 2)
