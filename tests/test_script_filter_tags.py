"""Compiled native any/all/excluded tag clauses, not full filter admission."""

import unittest

from test_game_graph_analysis import make_graph
from test_script_array_item import dynamic, group, integer

from ow174.game.script import expr, graph, runtime
from ow174.game.script.entity_tags import Tags
from ow174.game.script.filter_tags import Clauses, collect


class FilterTagTests(unittest.TestCase):
    def instance(self, world=None):
        return runtime.Component(123, world=world, strict=True).create(make_graph(1), init=False)

    def test_empty_clauses_do_not_require_component_data(self):
        for tags in (None, Tags(), NotImplemented):
            self.assertTrue(Clauses().matches(tags))

    def test_missing_component_cannot_meet_positive_clauses(self):
        self.assertFalse(Clauses(any_tags=(1,)).matches(None))
        self.assertFalse(Clauses(all_tags=(1,)).matches(None))
        self.assertTrue(Clauses(no_tags=(1,)).matches(None))

    def test_any_all_and_excluded_groups_combine(self):
        tags = Tags((1, 2))
        self.assertTrue(Clauses((3, 1), (1, 2), (4,)).matches(tags))
        self.assertFalse(Clauses((3,), (1, 2), ()).matches(tags))
        self.assertFalse(Clauses((), (1, 3), ()).matches(tags))
        self.assertFalse(Clauses((), (1, 2), (2,)).matches(tags))

    def test_clauses_use_dynamic_tag_precedence(self):
        removed_base = Tags((1,), (1,), (1,))
        added_nonbase = Tags((), (1,), (1,))
        self.assertFalse(Clauses(all_tags=(1,)).matches(removed_base))
        self.assertTrue(Clauses(all_tags=(1,)).matches(added_nonbase))

    def test_resource_high_bits_and_duplicates_keep_low32_membership(self):
        tag = 0x0D600000000003C3
        tags = Tags((0x3C3,))
        self.assertTrue(Clauses((tag,), (tag, 0x3C3, tag), ()).matches(tags))
        self.assertFalse(Clauses(no_tags=(tag,)).matches(tags))

    def test_unknown_component_is_not_assumed_empty_for_nonempty_clauses(self):
        with self.assertRaises(ValueError):
            Clauses(no_tags=(1,)).matches(NotImplemented)

    def test_malformed_compiled_lists_fail(self):
        for values in ([1], (-1,), (True,), (1 << 64,)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Clauses(any_tags=values)

    def test_authored_charge_filter_collects_actual_target_tag_resource(self):
        definition = graph.graph(0x0B8B)
        node = next(node for node in definition.nodes if node.pos == 53)
        filter_cfg = node.fields['m_condition']['m_configVars'][2]['m_filter']
        clauses = collect(self.instance(), filter_cfg)
        self.assertEqual(clauses, Clauses(all_tags=(0x0D60000000000121,)))

    def test_collects_groups_arrays_typed_nonzero_resources_and_full_guid_deduplication(self):
        instance = self.instance()
        first, other_type = 0x0D60000000000121, 0x0D80000000000121
        instance.component.set(600, (expr.Asset(first), (expr.Asset(first), expr.Asset(other_type)),
                                     expr.Asset(0), expr.Entity(17), None, 5))
        result = collect(instance, {'m_9C7BD8B8': group(integer(9), dynamic(600)),
                                    'm_B486603E': dynamic(600)})
        self.assertEqual(result, Clauses((first, other_type), (first, other_type)))

    def test_all_three_configs_are_collected_before_membership_decision(self):
        calls = []
        class World(runtime.World):
            def native(self, cfg, instance):
                if cfg.get('$') == 'STU_TEST_TAG_LIST':
                    calls.append(cfg['index'])
                    return expr.Asset(cfg['index'])
                return super().native(cfg, instance)
        cfg = {field: {'$': 'STU_TEST_TAG_LIST', 'index': index}
               for index, field in enumerate(('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3'), 1)}
        self.assertEqual(collect(self.instance(World()), cfg), Clauses((1,), (2,), (3,)))
        self.assertEqual(calls, [1, 2, 3])

    def test_keyed_tag_config_cannot_skip_resource_availability_service(self):
        config = {'$': 'STU_357DABBB', 'm_resourceKey': '0x0480000000000001',
                  'm_7D7C86A1': '0x0D60000000000121'}
        instance = self.instance()
        with self.assertRaises(runtime.UnsupportedScriptError):
            collect(instance, {'m_B486603E': config})


if __name__ == '__main__':
    unittest.main()
