"""CCFAC0 value-first/default selection, including creator member provenance."""

import unittest

from test_game_graph_analysis import make_graph

from ow174.game import body_vm_snapshot
from ow174.game.script import expr, runtime

KEY = 16349


def dynamic(scope, key=KEY):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': key, 'm_60DB8F99': scope}


def member(scope=0):
    return {'$': 'STU_919FD47C', 'm_expression': {
        'm_opcodes': [14, 0, 7, 0, 15, 0], 'm_dynamicVars': [dynamic(scope)],
    }, 'm_configVars': [{'$': 'STUConfigVarDynamic', 'm_identifier': 10, 'm_60DB8F99': 0}]}


class RulesetWorld(runtime.World):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.defaults = []
        self.available = {0xC0000051}

    def has_script_entity(self, entity):
        return entity in self.available

    def entity_var(self, entity, key):
        return self.values.get((entity, key))

    def ruleset(self, key):
        self.defaults.append((0, key))
        return 2.5

    def ruleset_for_entity(self, entity, key):
        self.defaults.append((entity, key))
        return self.values.get((entity, 'default'), 0.75)


class RulesetValueTests(unittest.TestCase):
    def scene(self):
        world = RulesetWorld()
        component = runtime.Component(0xA0000101, world=world)
        instance = component.create(make_graph(0x7F01), init=False)
        return component, instance, world

    def value(self, instance, child):
        return instance.evaluate({'$': 'STU_3832D36C', 'm_value': child})

    def test_nonnull_false_zero_fraction_and_negative_return_without_default_lookup(self):
        component, instance, world = self.scene()
        for value in (False, True, 0, -4, 0.0, 0.5, 12):
            with self.subTest(value=value):
                component.set(KEY, value)
                actual = self.value(instance, dynamic(1))
                self.assertIs(type(actual), type(value))
                self.assertEqual(actual, value)
        self.assertFalse(world.defaults)

    def test_literal_values_are_values_and_null_literal_has_no_identifier_default(self):
        _, instance, world = self.scene()
        self.assertEqual(self.value(instance, {'$': 'STUConfigVarFloat', 'm_value': 0.25}), 0.25)
        self.assertEqual(self.value(instance, {'$': 'STUConfigVarInt', 'm_value': 123}), 123)
        self.assertEqual(self.value(instance, None), 0)
        self.assertFalse(world.defaults)

    def test_direct_dynamic_null_fallback_requires_entity_scope(self):
        _, instance, world = self.scene()
        self.assertEqual(self.value(instance, dynamic(0)), 0)
        self.assertFalse(world.defaults)
        self.assertEqual(self.value(instance, dynamic(1)), 2.5)
        self.assertEqual(world.defaults, [(0, KEY)])

    def test_null_creator_member_reads_creator_defaults_and_zero_override_is_preserved(self):
        _, instance, world = self.scene()
        source = 0xC0000051
        instance.write((0, 10), expr.Entity(source))
        cfg = member()
        self.assertEqual(self.value(instance, cfg), 0.75)
        self.assertEqual(instance.ruleset_source_entity, source)
        self.assertEqual(world.defaults, [(source, KEY)])
        world.values[(source, KEY)] = 0
        self.assertEqual(self.value(instance, cfg), 0)
        self.assertEqual(len(world.defaults), 1)

    def test_expression_first_reference_scope_and_empty_reference_rules(self):
        _, instance, world = self.scene()
        for scope, expected in ((0, 0), (1, 2.5)):
            cfg = {'$': 'STU_919FD47C', 'm_expression': {
                'm_opcodes': [14, 0, 0], 'm_dynamicVars': [dynamic(scope), dynamic(1, 7)],
            }, 'm_configVars': [None]}
            self.assertEqual(self.value(instance, cfg), expected)
        cfg['m_expression']['m_dynamicVars'] = []
        self.assertEqual(self.value(instance, cfg), 0)
        self.assertEqual(world.defaults, [(0, KEY)])

    def test_missing_entity_or_script_does_not_record_member_source(self):
        _, instance, world = self.scene()
        instance.write((0, 10), expr.Entity(0xC0000051))
        world.available.clear()
        self.assertEqual(self.value(instance, member()), 0)
        self.assertEqual(instance.ruleset_source_entity, 0)
        self.assertFalse(world.defaults)
        world.available.add(0xC0000051)
        self.assertEqual(self.value(instance, member()), 0.75)
        self.assertEqual(instance.ruleset_source_entity, 0xC0000051)
        # The helper itself retains the last source across an unresolved read;
        # the enclosing ruleset wrapper resets it on its next evaluation.
        instance.write((0, 10), expr.Entity(0xC0000099))
        self.assertIsNone(instance.evaluate(member()))
        self.assertEqual(instance.ruleset_source_entity, 0xC0000051)
        self.assertEqual(self.value(instance, member()), 0)
        self.assertEqual(instance.ruleset_source_entity, 0)

    def test_self_missing_variable_still_records_resolved_script_source(self):
        component, instance, world = self.scene()
        instance.write((0, 10), expr.Entity(component.entity))
        self.assertEqual(self.value(instance, member()), 2.5)
        self.assertEqual(instance.ruleset_source_entity, component.entity)
        self.assertEqual(world.defaults, [(0, KEY)])

    def test_each_evaluation_resets_stale_member_source_and_snapshot_retains_field(self):
        _, instance, _ = self.scene()
        instance.write((0, 10), expr.Entity(0xC0000051))
        self.value(instance, member())
        # The custom world is outside the registered VM exporter, so use the
        # registered empty service for the causal-field assertion only.
        instance.component.world = runtime.World()
        before = body_vm_snapshot.snapshot_digest(body_vm_snapshot.export_component_vm(instance.component))
        self.assertEqual(self.value(instance, {'$': 'STUConfigVarInt', 'm_value': 0}), 0)
        self.assertEqual(instance.ruleset_source_entity, 0)
        after = body_vm_snapshot.snapshot_digest(body_vm_snapshot.export_component_vm(instance.component))
        self.assertNotEqual(before, after)


if __name__ == '__main__':
    unittest.main()
