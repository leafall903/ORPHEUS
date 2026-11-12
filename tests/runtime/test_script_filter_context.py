"""Native lookup/cache choice, missing-target dependencies and host lifetime guards."""

import unittest
from unittest.mock import Mock

from test_game_graph_analysis import make_graph

from ow174.game.script import filter_context as filters
from ow174.game.script import filter_pipeline as pipeline
from ow174.game.script import runtime

CONFIG = {'$': 'STUConfigVarFilter'}
TARGET = 0xA0000123


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.context = NotImplemented

    def entity_filter_context(self, owner, *, component=None):
        return self.context


class FilterContextTests(unittest.TestCase):
    def fixture(self, *, watcher=None):
        world = World()
        instance = runtime.Component(123, world=world, strict=True).create(make_graph(1), init=False)
        subject = pipeline.Subject(TARGET, True, None)
        context = filters.Context(123, lookup=lambda _: subject,
                                  services=pipeline.Services(source=lambda: None, registry=lambda *_: 2))
        world.context = context
        return instance.context(watcher), context, world, subject

    def test_missing_filter_bypasses_owner_context_and_identifier(self):
        self.assertTrue(filters.accepts(None, None, object(), cache=object()))
        self.assertIs(filters.accepts(None, {'$': 'Other'}, TARGET), NotImplemented)

    def test_uninstalled_world_context_is_unhandled(self):
        evaluation, _context, world, _subject = self.fixture()
        world.context = NotImplemented
        self.assertIs(filters.accepts(evaluation, CONFIG, TARGET), NotImplemented)

    def test_direct_lookup_hit_does_not_require_environment_or_cache_services(self):
        evaluation, context, _world, subject = self.fixture()
        context.lookup = Mock(return_value=subject)
        self.assertTrue(filters.accepts(evaluation, CONFIG, TARGET))
        context.lookup.assert_called_once_with(TARGET)

    def test_direct_miss_queries_current_environment_even_without_a_watcher(self):
        evaluation, context, _world, _subject = self.fixture()
        context.lookup = lambda _: None
        context.environment = Mock(return_value=None)
        self.assertFalse(filters.accepts(evaluation, CONFIG, TARGET))
        context.environment.assert_called_once_with()

    def test_current_environment_miss_subscribes_kind_zero(self):
        watcher = object()
        evaluation, context, _world, _subject = self.fixture(watcher=watcher)
        environment, hub = object(), object()
        context.lookup = lambda _: None
        context.environment = lambda: environment
        context.dependency_hub = Mock(return_value=hub)
        context.watch_environment = Mock()
        self.assertFalse(filters.accepts(evaluation, CONFIG, TARGET))
        context.dependency_hub.assert_called_once_with(environment)
        context.watch_environment.assert_called_once_with(hub, watcher, 0)

    def test_absent_current_environment_uses_instance_environment_kind46(self):
        watcher = object()
        evaluation, context, _world, _subject = self.fixture(watcher=watcher)
        environment, hub = object(), object()
        context.lookup = lambda _: None
        context.environment = lambda: None
        context.instance_environment = Mock(return_value=environment)
        context.dependency_hub = lambda _: hub
        context.watch_environment = Mock()
        self.assertFalse(filters.accepts(evaluation, CONFIG, TARGET))
        context.instance_environment.assert_called_once_with(evaluation.instance)
        context.watch_environment.assert_called_once_with(hub, watcher, 46)

    def test_missing_fallback_environment_and_missing_hub_do_not_subscribe(self):
        evaluation, context, _world, _subject = self.fixture(watcher=object())
        context.lookup = lambda _: None
        context.environment = lambda: None
        context.instance_environment = lambda _: None
        self.assertFalse(filters.accepts(evaluation, CONFIG, TARGET))
        context.environment = lambda: object()
        context.dependency_hub = lambda _: None
        self.assertFalse(filters.accepts(evaluation, CONFIG, TARGET))

    def test_cached_miss_uses_cache_hub_without_namespace_fallback(self):
        watcher = object()
        evaluation, context, _world, _subject = self.fixture(watcher=watcher)
        cache, hub = filters.Cache({}), object()
        context.lookup = Mock(side_effect=AssertionError('direct lookup'))
        context.environment = Mock(side_effect=AssertionError('current environment'))
        context.dependency_hub = Mock(return_value=hub)
        context.watch_environment = Mock()
        self.assertFalse(filters.accepts(evaluation, CONFIG, TARGET, cache=cache))
        context.dependency_hub.assert_called_once_with(cache)
        context.watch_environment.assert_called_once_with(hub, watcher, 0)

    def test_cache_compares_full_keys_within_low12_bucket_and_preserves_first_null(self):
        evaluation, _context, _world, subject = self.fixture()
        collision = 0xC0000123
        other = pipeline.Subject(collision, True, None)
        cache = filters.Cache({0x123: ((collision, other), (TARGET, subject))})
        self.assertTrue(filters.accepts(evaluation, CONFIG, TARGET, cache=cache))
        cache.buckets[0x123] = ((TARGET, None), (TARGET, subject))
        self.assertIsNone(cache.lookup(TARGET))

    def test_cache_nested_lease_release_preserves_the_saved_acquisition_flag(self):
        subject = pipeline.Subject(TARGET, True, None)
        cache = filters.Cache({0x123: ((TARGET, subject),)}, True, 0x1025,
                              acquire=lambda value: setattr(value, 'word', 0x2025))
        self.assertIs(cache.lookup(TARGET), subject)
        self.assertEqual(cache.word, 0x1025)

        def acquire(value):
            value.word = 0x2025
            value.locked = False

        cache.acquire = acquire
        self.assertIs(cache.lookup(TARGET), subject)
        self.assertEqual(cache.word, 0x1025)

    def test_cache_release_runs_if_owner_validation_fails_after_acquisition(self):
        evaluation, context, _world, _subject = self.fixture()

        def acquire(cache):
            cache.word = 0x1025
            context.retire()

        cache = filters.Cache({}, True, 0, acquire)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired'):
            filters.accepts(evaluation, CONFIG, TARGET, cache=cache)
        self.assertEqual(cache.word, 0)

    def test_namespace_callback_retirement_and_context_replacement_fail_owner(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                evaluation, context, world, subject = self.fixture()

                def lookup(_, *, replace=replace, world=world, context=context, subject=subject):
                    if replace:
                        world.context = filters.Context(123)
                    else:
                        context.retire()
                    return subject

                context.lookup = lookup
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired'):
                    filters.accepts(evaluation, CONFIG, TARGET)
                self.assertTrue(evaluation.instance.stopped)

    def test_resolved_source_callback_is_checked_for_owner_retirement(self):
        evaluation, context, _world, _subject = self.fixture()

        def source():
            context.retire()
            return None

        context.services.source = source
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired'):
            filters.accepts(evaluation, CONFIG, TARGET)

    def test_component_guard_rejection_does_not_require_resolved_source_services(self):
        evaluation, context, _world, _subject = self.fixture()
        context.lookup = lambda _: pipeline.Subject(TARGET, False)
        context.services = pipeline.Services()
        self.assertFalse(filters.accepts(evaluation, {'$': 'STUConfigVarFilter', 'm_84065FD1': 1}, TARGET))

    def test_mismatched_lookup_and_unknown_services_fail_instead_of_passing(self):
        evaluation, context, _world, _subject = self.fixture()
        context.lookup = lambda _: pipeline.Subject(TARGET + 1, True, None)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'namespace result'):
            filters.accepts(evaluation, CONFIG, TARGET)
        evaluation, context, _world, _subject = self.fixture()
        context.services.registry = NotImplemented
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'registry'):
            filters.accepts(evaluation, CONFIG, TARGET)

    def test_unknown_cache_acquisition_does_not_become_unlocked_lookup(self):
        evaluation, _context, _world, _subject = self.fixture()
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'acquisition'):
            filters.accepts(evaluation, CONFIG, TARGET, cache=filters.Cache({}, True))

    def test_nested_inventory_callbacks_are_owner_checked(self):
        evaluation, context, _world, _subject = self.fixture()

        def count():
            context.retire()
            return 1

        context.services.inventory = lambda _: pipeline.Inventory(count=count)
        context.services.integer = lambda *_: 7
        config = {'$': 'STUConfigVarFilter', 'm_F61726FE': {'$': 'STUConfigVarBool', 'm_value': True}}
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired'):
            filters.accepts(evaluation, config, TARGET)


if __name__ == '__main__':
    unittest.main()
