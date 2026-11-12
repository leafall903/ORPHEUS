"""Resolved native filter stage ordering; actual engine bindings remain required."""

import unittest
from unittest.mock import Mock

from test_game_graph_analysis import make_graph

from ow174.game.script import filter_pipeline as pipeline
from ow174.game.script import filter_tags, graph, runtime
from ow174.game.script.entity_team import FilterBits


def config(**fields):
    return {'$': 'STUConfigVarFilter', **fields}


def boolean(value):
    return {'$': 'STUConfigVarBool', 'm_value': value}


class FilterPipelineTests(unittest.TestCase):
    def instance(self):
        return runtime.Component(123, strict=True).create(make_graph(1), init=False)

    def subject(self, bits=1, **fields):
        return pipeline.Subject(0xA0000123, True, FilterBits(bits), **fields)

    def services(self, **fields):
        return pipeline.Services(registry=lambda *_: 2, **fields)

    def test_missing_filter_precedes_all_service_and_object_requirements(self):
        self.assertTrue(pipeline.resolved(None, None, None, None, None))
        self.assertTrue(pipeline.resolved(object(), None, None, None, None))

    def test_all_zero_masks_skip_the_bit_predicate_and_component97_guard(self):
        target = pipeline.Subject(123, False, None)
        self.assertTrue(pipeline.resolved(None, config(), target, None, self.services()))

    def test_nonzero_masks_require_component97_before_component_lookup(self):
        target = pipeline.Subject(123, False)
        self.assertFalse(pipeline.resolved(None, config(m_84065FD1=1), target, None, pipeline.Services()))

    def test_wildcard_mask_bypasses_component97_but_preserves_native_mask_matching(self):
        target = pipeline.Subject(123, False, None)
        self.assertTrue(pipeline.resolved(None, config(m_84065FD1=0x3FFFFFFF),
                                          target, None, self.services()))

    def test_actual_flux_primary_filter_uses_declared_relationship_and_not_entity_id_equality(self):
        cfg = graph.graph(0x1E41).node(37).field('m_filter')
        target = self.subject()
        other = self.subject()
        services = self.services(relationship=lambda _: True)
        self.assertTrue(pipeline.resolved(None, cfg, target, other, services))
        self.assertFalse(pipeline.resolved(None, cfg, target, target, services))
        services.relationship = lambda _: False
        self.assertFalse(pipeline.resolved(None, cfg, target, other, services))

    def test_actual_flux_secondary_tags_are_composed_without_a_zero_any_mask_rejection(self):
        cfg = graph.graph(0x1E41).node(37).field('m_3D667CE3')
        instance = self.instance()
        target = self.subject(tags=filter_tags.Tags())
        self.assertTrue(pipeline.resolved(instance, cfg, target, None, self.services()))
        target.tags = filter_tags.Tags((0x3A,))
        self.assertFalse(pipeline.resolved(instance, cfg, target, None, self.services()))

    def test_bit_subscription_order_and_callback_mutation_precede_the_predicate(self):
        target, source = self.subject(), self.subject()
        calls = []

        def watch(watcher, subject, bits):
            calls.append(subject)
            if subject is target:
                bits.bits = 0

        services = self.services(watch_bits=watch)
        self.assertFalse(pipeline.resolved(None, config(m_84065FD1=1), target, source,
                                           services, watcher=object()))
        self.assertEqual(calls, [target, source])

    def test_saved_filter_component_pointer_survives_callback_replacement(self):
        target = self.subject()
        services = self.services(watch_bits=lambda *_: setattr(target, 'bits', FilterBits(0)))
        self.assertTrue(pipeline.resolved(None, config(m_84065FD1=1), target, None,
                                          services, watcher=object()))

    def test_source_component_is_resolved_after_target_callback(self):
        target, source = self.subject(), self.subject()
        replacement, seen = FilterBits(9), []

        def watch(watcher, subject, bits):
            if subject is target:
                source.bits = replacement
            else:
                seen.append(bits)

        services = self.services(watch_bits=watch, source=lambda: source)
        self.assertTrue(pipeline.resolved(None, config(), target, NotImplemented,
                                          services, watcher=object()))
        self.assertEqual(seen, [replacement])

    def test_rejected_component_guard_does_not_require_source_resolution(self):
        target = pipeline.Subject(123, False)
        self.assertFalse(pipeline.resolved(None, config(m_84065FD1=1), target, NotImplemented,
                                           pipeline.Services()))

    def test_false_condition_skips_tag_subscription_and_registry(self):
        target = self.subject()
        services = pipeline.Services(registry=Mock(side_effect=AssertionError('registry called')))
        cfg = config(m_condition=boolean(False), m_8B2A4DF3={'$': 'UnknownTagConfig'})
        self.assertFalse(pipeline.resolved(self.instance(), cfg, target, None, services))
        services.registry.assert_not_called()

    def test_tag_rejection_precedes_registry(self):
        target = self.subject(tags=filter_tags.Tags((0x3A,)))
        cfg = graph.graph(0x1E41).node(37).field('m_3D667CE3')
        self.assertFalse(pipeline.resolved(self.instance(), cfg, target, None, pipeline.Services()))

    def test_registry_only_one_rejects_and_unknown_service_is_not_empty_registry(self):
        target = self.subject()
        for result in (0, 1, 2, 3, -1):
            with self.subTest(result=result):
                services = pipeline.Services(registry=lambda *_, result=result: result)
                self.assertEqual(pipeline.resolved(None, config(), target, None, services), result != 1)
        with self.assertRaisesRegex(ValueError, 'registry'):
            pipeline.resolved(None, config(), target, None, pipeline.Services())

    def test_inventory_absence_passes_before_identifier_evaluation(self):
        services = self.services(inventory=lambda _: None)
        self.assertTrue(pipeline.resolved(self.instance(), config(m_F61726FE=boolean(True)),
                                          self.subject(), None, services))

    def test_invalid_identifier_precedes_inventory_count(self):
        inventory = pipeline.Inventory(count=Mock(side_effect=AssertionError('count queried')))
        services = self.services(inventory=lambda _: inventory, integer=lambda *_: -(1 << 31))
        self.assertFalse(pipeline.resolved(self.instance(), config(m_F61726FE=boolean(True)),
                                           self.subject(), None, services))
        inventory.count.assert_not_called()

    def test_inventory_ignores_nonpositive_entries_and_observes_count_changes(self):
        counts, calls = [0, -1, 1], []

        def contains(index, identifier):
            calls.append((index, identifier))
            if index == 2:
                counts.append(1)
            return index == 3

        inventory = pipeline.Inventory(count=lambda: len(counts), value=lambda index: counts[index],
                                       contains=contains)
        services = self.services(inventory=lambda _: inventory, integer=lambda *_: 7)
        self.assertTrue(pipeline.resolved(self.instance(), config(m_F61726FE=boolean(True)),
                                          self.subject(), None, services))
        self.assertEqual(calls, [(2, 7), (3, 7)])

    def test_unknown_components_and_required_callbacks_raise(self):
        with self.assertRaisesRegex(ValueError, 'component'):
            pipeline.resolved(None, config(), pipeline.Subject(123, True), None, self.services())
        with self.assertRaisesRegex(ValueError, 'subscription'):
            pipeline.resolved(None, config(), self.subject(), None, self.services(), watcher=object())
        with self.assertRaisesRegex(ValueError, 'inventory lookup'):
            pipeline.resolved(self.instance(), config(m_F61726FE=boolean(True)),
                              self.subject(), None, self.services())

    def test_strict_unknown_fallback_query_remains_a_script_fault(self):
        with self.assertRaises(runtime.UnsupportedScriptError):
            pipeline.resolved(self.instance(), config(m_F61726FE={'$': 'UnknownFilterQuery'}),
                              self.subject(), None, self.services())


if __name__ == '__main__':
    unittest.main()
