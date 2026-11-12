"""Native tag precedence, resource collection and actual Flux exclusion config."""

import unittest

from test_game_graph_analysis import make_graph, node
from test_script_array_item import dynamic, group, integer

from ow174.game.script import expr, filter_tags, graph, runtime


class FilterTagsTests(unittest.TestCase):
    def instance(self):
        return runtime.Component(123, strict=True).create(make_graph(1), init=False)

    def test_removed_base_is_not_restored_by_an_addition(self):
        tags = filter_tags.Tags((1, 2), (1, 3), (1, 3))
        self.assertFalse(tags.contains(1))
        self.assertTrue(tags.contains(2))
        self.assertTrue(tags.contains(3))  # non-base additions do not consult removals

    def test_membership_uses_low32_bits_of_full_resource_keys(self):
        tags = filter_tags.Tags((0x3A,))
        self.assertTrue(tags.contains(0x0D6000000000003A))
        self.assertTrue(tags.contains(0xFFFFFFFF0000003A))
        self.assertFalse(tags.contains(0x0D600000000003A0))

    def test_empty_any_and_all_lists_have_distinct_identities(self):
        tags = filter_tags.Tags()
        self.assertFalse(tags.any(None))
        self.assertFalse(tags.any(()))
        self.assertTrue(tags.all(None))
        self.assertTrue(tags.all(()))
        self.assertTrue(filter_tags.matches(tags, (), (), ()))

    def test_any_all_and_excluded_requirements(self):
        tags = filter_tags.Tags((1, 2))
        self.assertTrue(filter_tags.matches(tags, (1, 3), (1, 2), (3,)))
        self.assertFalse(filter_tags.matches(tags, (3,), (1,), ()))
        self.assertFalse(filter_tags.matches(tags, (), (1, 3), ()))
        self.assertFalse(filter_tags.matches(tags, (), (), (2,)))

    def test_known_absence_passes_only_empty_requirements(self):
        self.assertTrue(filter_tags.matches(None, (), (), (1, 2)))
        self.assertFalse(filter_tags.matches(None, (1,), (), ()))
        self.assertFalse(filter_tags.matches(None, (), (1,), ()))
        with self.assertRaises(ValueError):
            filter_tags.matches(NotImplemented)

    def test_collection_deduplicates_full_guids_before_low32_membership(self):
        values = (expr.Asset(0x100000001), expr.Asset(0x200000001), expr.Asset(0x100000001),
                  expr.Asset(0), 7, True, expr.Entity(1), None, expr.Vec3(1, 2, 3))
        self.assertEqual(filter_tags.collect_values(values), (0x100000001, 0x200000001))

    def test_value_conversion_uses_first_array_item_and_does_not_search_later_items(self):
        values = ((None, expr.Asset(1)), (), ((expr.Asset(2),), expr.Asset(3)), (7, expr.Asset(4)))
        self.assertEqual(filter_tags.collect_values(values), (2,))

    def test_owned_config_iterator_collects_dynamic_arrays_and_stable_duplicates(self):
        instance = self.instance()
        instance.component.set(7, (expr.Asset(1), expr.Asset(2), expr.Asset(1), None, 7))
        self.assertEqual(filter_tags.collect(instance, group(dynamic(7), integer(4))), (1, 2))
        self.assertIsNone(instance.component.fault)

    def test_unknown_strict_config_query_is_not_converted_to_an_empty_tag_set(self):
        instance = self.instance()
        with self.assertRaises(runtime.UnsupportedScriptError):
            filter_tags.collect(instance, {'$': 'UnknownTagConfig'})

    def test_unconfigured_tag_stage_skips_unknown_component(self):
        self.assertTrue(filter_tags.evaluate(None, {'$': 'STUConfigVarFilter'}, NotImplemented))

    def test_configured_empty_result_does_not_declare_an_unknown_component_absent(self):
        with self.assertRaises(ValueError):
            filter_tags.evaluate(self.instance(), {'$': 'STUConfigVarFilter', 'm_8B2A4DF3': integer(7)},
                                 NotImplemented)

    def test_all_three_config_inputs_register_dependencies_before_predicate_failure(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(make_graph(1, [node(0, state=0)], states=[0]), init=False)
        state = instance.state(0)
        state.begin()
        for key in (7, 8, 9):
            component.set(key, (expr.Asset(key),))
        config = {'$': 'STUConfigVarFilter', 'm_9C7BD8B8': dynamic(7),
                  'm_B486603E': dynamic(8), 'm_8B2A4DF3': dynamic(9)}
        self.assertFalse(filter_tags.evaluate(instance, config, None, watcher=state))
        self.assertTrue(all(state in component.entity_var(key).watchers for key in (7, 8, 9)))

    def test_actual_flux_exclusion_group_blocks_each_authored_tag(self):
        instance = self.instance()
        config = graph.graph(0x1E41).node(37).field('m_3D667CE3')
        keys = filter_tags.collect(instance, config['m_8B2A4DF3'])
        self.assertEqual(keys, tuple(0x0D60000000000000 | key for key in (0x3A, 0x2A0, 0x2CB, 0x385)))
        self.assertTrue(filter_tags.evaluate(instance, config, filter_tags.Tags()))
        self.assertTrue(filter_tags.evaluate(instance, config, None))
        for key in keys:
            with self.subTest(key=key):
                self.assertFalse(filter_tags.evaluate(
                    instance, config, filter_tags.Tags((key & 0xFFFFFFFF,)),
                ))
        self.assertFalse(filter_tags.evaluate(instance, config, filter_tags.Tags((), (0x3A,))))
        self.assertTrue(filter_tags.evaluate(instance, config, filter_tags.Tags((0x3A,), (), (0x3A,))))

    def test_invalid_storage_and_request_domains_are_rejected(self):
        for bad in (-1, 1 << 32, True, 1.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                filter_tags.Tags((bad,))
        for bad in (-1, 1 << 64, True, '1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                filter_tags.Tags().contains(bad)
        with self.assertRaises(ValueError):
            filter_tags.Tags().any([1])


if __name__ == '__main__':
    unittest.main()
