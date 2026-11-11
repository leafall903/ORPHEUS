"""Ordered game-filter stages under explicit engine data, not production Match wiring."""

import unittest

from test_game_graph_analysis import make_graph, node
from test_script_array_item import dynamic, integer
from test_script_hostility import entity

from ow174.game.script import entity_filter as filters
from ow174.game.script import expr, runtime
from ow174.game.script.entity_tags import Tags
from ow174.game.script.entity_team import FilterBits

OWNER, TARGET = 0xA0000101, 0xA0000201


def query(definition):
    return {'$': filters.CLASS, 'm_entity': entity(TARGET), 'm_filter': definition}


def definition(**fields):
    return {'$': 'STUConfigVarFilter', 'm_84065FD1': 1, **fields}


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.source = filters.Target(OWNER, FilterBits(1), True, None, None, None)
        self.target = filters.Target(TARGET, FilterBits(1), True, Tags((1,)), None, None)
        self.bound = filters.Context(OWNER, True, lookup=self.lookup, source=self.source,
                                    relation_enemy=self.relation, watch_filter=self.watch_filter,
                                    watch_tags=self.watch_tags, watch_environment=self.watch_environment)
        self.config_callback = None

    def entity_filter_context(self, owner, *, component=None):
        return self.bound

    def lookup(self, identifier):
        self.calls.append(('lookup', identifier))
        return self.target if identifier == TARGET else None

    def relation(self, spectator, target, source):
        self.calls.append(('relation', spectator))
        return True  # declared ordinary-enemy engine result for this fixture

    def watch_filter(self, state, actor, bits):
        self.calls.append(('filter', actor.entity))

    def watch_tags(self, state, actor):
        self.calls.append(('tags', actor.entity))

    def watch_environment(self, state, kind):
        self.calls.append(('environment', kind))

    def native(self, cfg, instance):
        if cfg.get('$') == 'STU_TEST_TYPED_ENTITY':
            self.calls.append(('candidate', cfg['m_value']))
            return expr.Entity(cfg['m_value'])
        if cfg.get('$') == 'STU_TEST_FILTER_VALUE':
            self.calls.append(('value', cfg['name']))
            if self.config_callback is not None:
                return self.config_callback(cfg, instance)
            return cfg.get('value')
        return super().native(cfg, instance)


def value(name, data=None):
    return {'$': 'STU_TEST_FILTER_VALUE', 'name': name, 'value': data}


class EntityFilterTests(unittest.TestCase):
    def instance(self):
        self.world = World()
        return runtime.Component(OWNER, world=self.world, strict=True).create(make_graph(1), init=False)

    def watcher(self, instance):
        graph = make_graph(2, [node(0, 'STUStatescriptState', state=0)], states=[0])
        return runtime.State(instance, graph.nodes[0], 0)

    def test_null_filter_still_evaluates_candidate_then_accepts_without_engine_catalog(self):
        instance = self.instance()
        self.world.bound = NotImplemented
        self.assertTrue(instance.evaluate(query(None)))
        self.assertEqual(self.world.calls, [('candidate', TARGET)])

    def test_missing_target_observes_native_environment_kind(self):
        for present, kind in ((False, 46), (True, 0)):
            instance = self.instance()
            self.world.bound.environment_present = present
            self.world.bound.lookup = lambda identity: None
            self.assertFalse(instance.evaluate(query(definition()), self.watcher(instance)))
            self.assertEqual(self.world.calls, [('candidate', TARGET), ('environment', kind)])

    def test_component97_gate_precedes_unknown_filter_and_owner_data(self):
        instance = self.instance()
        self.world.target.has_component97 = False
        self.world.target.bits = NotImplemented
        self.world.bound.source = NotImplemented
        self.assertFalse(instance.evaluate(query(definition())))
        self.assertEqual(self.world.calls, [('candidate', TARGET), ('lookup', TARGET)])

    def test_all_marker_skips_component97_but_zero_masks_skip_mask_match(self):
        instance = self.instance()
        self.world.target.has_component97 = NotImplemented
        self.assertTrue(instance.evaluate(query(definition(m_84065FD1=filters.filter_masks.ALL))))
        self.world.target.has_component97 = False
        self.world.target.bits = None
        self.assertTrue(instance.evaluate(query(definition(m_84065FD1=0))))

    def test_target_then_owner_observation_precedes_relation_and_condition(self):
        instance = self.instance()
        cfg = definition(m_66747E7D=2, m_condition=value('condition', True))
        self.assertTrue(instance.evaluate(query(cfg), self.watcher(instance)))
        self.assertEqual(self.world.calls, [('candidate', TARGET), ('lookup', TARGET),
            ('filter', TARGET), ('filter', OWNER), ('relation', False), ('value', 'condition')])

    def test_mask_uses_captured_component_pointer_after_subscription(self):
        instance = self.instance()
        self.world.target.bits = FilterBits(0)
        def watch(state, actor, bits):
            if actor.entity == TARGET:
                actor.bits = FilterBits(1)
        self.world.bound.watch_filter = watch
        self.assertFalse(instance.evaluate(query(definition()), self.watcher(instance)))

    def test_base_candidate_scope_does_not_leak_into_tag_configs_or_extra_condition(self):
        instance = self.instance()
        seen = []
        def config_callback(cfg, current):
            seen.append((cfg['name'], current.context().param(8)))
            return {'condition': True, 'tags': expr.Asset(1), 'extra': False}[cfg['name']]
        self.world.config_callback = config_callback
        cfg = definition(m_condition=value('condition'), m_B486603E=value('tags'), m_F61726FE=value('extra'))
        with instance.component.parameter_scope({8: expr.Entity(777)}):
            self.assertTrue(instance.evaluate(query(cfg)))
        self.assertEqual(seen, [('condition', expr.Entity(TARGET)), ('tags', expr.Entity(777)),
                                ('extra', expr.Entity(777))])

    def test_tag_subscription_then_config_collection_then_fresh_membership(self):
        instance = self.instance()
        def config_callback(cfg, current):
            self.world.target.tags = Tags((2,))
            return expr.Asset(2)
        self.world.config_callback = config_callback
        self.assertTrue(instance.evaluate(query(definition(m_B486603E=value('tags'))),
                                          self.watcher(instance)))
        self.assertEqual(self.world.calls[-2:], [('tags', TARGET), ('value', 'tags')])

    def test_failed_tag_condition_short_circuits_registry_and_extra_checks(self):
        instance = self.instance()
        self.world.target.entity_result = lambda cfg, state: self.fail('registry must not run')
        self.assertFalse(instance.evaluate(query(definition(m_B486603E=value('tags', expr.Asset(2))))))

    def test_registry_rejects_only_code_one(self):
        instance = self.instance()
        for code, expected in ((0, True), (1, False), (2, True)):
            self.world.target.entity_result = lambda cfg, state, result=code: result
            self.assertEqual(instance.evaluate(query(definition())), expected)

    def test_unknown_registry_is_not_assumed_absent(self):
        instance = self.instance()
        self.world.target.entity_result = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(query(definition()))

    def test_false_extra_condition_does_not_require_optional_group_data(self):
        instance = self.instance()
        self.world.target.groups = NotImplemented
        self.assertTrue(instance.evaluate(query(definition(m_F61726FE=integer(0)))))

    def test_true_extra_condition_with_known_missing_optional_list_accepts_without_key(self):
        instance = self.instance()
        self.assertTrue(instance.evaluate(query(definition(m_F61726FE=integer(1),
            m_B40362FC={'$': 'STU_UNUSED_KEY'}))))

    def test_present_empty_list_evaluates_key_then_rejects(self):
        instance = self.instance()
        self.world.target.groups = filters.Groups(())
        self.assertFalse(instance.evaluate(query(definition(m_F61726FE=integer(1),
                                                            m_B40362FC=value('key', 7)))))
        self.assertEqual(self.world.calls[-1], ('value', 'key'))

    def test_optional_groups_scan_only_enabled_entries_and_stop_on_match(self):
        instance = self.instance()
        calls = []
        def match(index, key):
            calls.append((index, key))
            return index == 2
        self.world.target.groups = filters.Groups((0, -1, 2, 1), match)
        self.assertTrue(instance.evaluate(query(definition(m_F61726FE=integer(1), m_B40362FC=integer(7)))))
        self.assertEqual(calls, [(2, 7)])

    def test_failed_key_rejects_but_successful_null_converts_to_zero(self):
        instance = self.instance()
        self.world.target.groups = filters.Groups((1,), lambda index, key: key == 0)
        self.assertFalse(instance.evaluate(query(definition(m_F61726FE=integer(1)))))
        self.assertTrue(instance.evaluate(query(definition(m_F61726FE=integer(1), m_B40362FC=dynamic(999)))))

    def test_retirement_in_first_empty_tag_config_prevents_later_configs(self):
        instance = self.instance()
        def config_callback(cfg, current):
            self.world.bound.retire()
            return None
        self.world.config_callback = config_callback
        cfg = definition(m_9C7BD8B8=value('first'), m_B486603E=value('second'))
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(query(cfg))
        self.assertEqual([entry for entry in self.world.calls if entry[0] == 'value'], [('value', 'first')])

    def test_target_retirement_during_subscription_stops_owner_subscription(self):
        instance = self.instance()
        self.world.bound.watch_filter = lambda state, actor, bits: setattr(actor, 'retired', True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(query(definition()), self.watcher(instance))


if __name__ == '__main__':
    unittest.main()
