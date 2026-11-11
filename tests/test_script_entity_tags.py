"""Native target-tag membership and query context/lifetime boundaries."""

import unittest

from test_game_graph_analysis import make_graph, node
from test_script_array_item import group
from test_script_hostility import entity

from ow174.game.script import entity_tags as tags
from ow174.game.script import expr, runtime

OWNER, TARGET = 0xA0000101, 0xA0000201
TAG = 0x3C3


def config(target=None, key=TAG):
    return {'$': tags.CLASS, 'm_entity': target, 'm_88B0050A': f'0x{0x0D60000000000000 | key:016X}'}


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.actor = tags.Target(TARGET, tags.Tags((TAG,)))
        self.calls = []
        self.bound = tags.Context(OWNER, True, lookup=self.lookup)

    def entity_tag_context(self, owner, *, component=None):
        return self.bound

    def lookup(self, identifier):
        self.calls.append(('lookup', identifier))
        return self.actor if identifier == TARGET else None

    def native(self, cfg, instance):
        if cfg.get('$') == 'STU_TEST_TYPED_ENTITY':
            self.calls.append(('config', cfg['m_value']))
            return expr.Entity(cfg['m_value'])
        return super().native(cfg, instance)


class EntityTagTests(unittest.TestCase):
    def instance(self):
        self.world = World()
        return runtime.Component(OWNER, world=self.world, strict=True).create(make_graph(1), init=False)

    def watcher(self, instance):
        definition = make_graph(2, [node(0, 'STUStatescriptState', state=0)], states=[0])
        return runtime.State(instance, definition.nodes[0], 0)

    def test_base_removal_and_nonbase_addition_have_distinct_precedence(self):
        for base, added, removed, expected in (
            ((), (), (), False), ((TAG,), (), (), True), ((TAG,), (), (TAG,), False),
            ((), (TAG,), (), True), ((), (TAG,), (TAG,), True),
            ((TAG,), (TAG,), (TAG,), False), ((TAG, TAG), (), (), True),
        ):
            with self.subTest(base=base, added=added, removed=removed):
                self.assertEqual(tags.Tags(base, added, removed).contains(TAG), expected)

    def test_query_scalar_group_and_resource_low32_key(self):
        instance = self.instance()
        bad = {'$': 'STU_UNKNOWN_UNUSED_GROUP_MEMBER'}
        self.assertTrue(instance.evaluate(config(group(entity(TARGET), bad))))
        self.assertFalse(instance.evaluate(config(entity(TARGET), 0x2ED)))
        self.assertIsNone(instance.component.fault)

    def test_zero_tag_is_a_normal_native_membership_key(self):
        instance = self.instance()
        self.world.actor.tags = tags.Tags((0,))
        self.assertTrue(instance.evaluate(config(entity(TARGET), 0)))

    def test_missing_entity_and_known_absent_component_are_false(self):
        instance = self.instance()
        self.assertFalse(instance.evaluate(config(entity(999))))
        self.world.actor.tags = None
        self.assertFalse(instance.evaluate(config(entity(TARGET))))

    def test_failed_or_missing_entity_config_defaults_to_invalid_and_looks_up(self):
        instance = self.instance()
        for source in (None, {'$': 'STU_7BD2CA19', 'm_resourceKey': None}):
            self.world.calls.clear()
            self.assertFalse(instance.evaluate(config(source)))
            self.assertEqual(self.world.calls, [('lookup', 0)])

    def test_subscription_precedes_fresh_component_read(self):
        instance = self.instance()
        def watch(state, actor):
            self.world.calls.append(('watch', actor.entity))
            actor.tags = tags.Tags(removed=(TAG,))
        self.world.bound.watch_tags = watch
        self.assertFalse(instance.evaluate(config(entity(TARGET)), self.watcher(instance)))
        self.assertEqual(self.world.calls, [('config', TARGET), ('lookup', TARGET), ('watch', TARGET)])

    def test_component_can_disappear_during_subscription(self):
        instance = self.instance()
        self.world.bound.watch_tags = lambda state, actor: setattr(actor, 'tags', None)
        self.assertFalse(instance.evaluate(config(entity(TARGET)), self.watcher(instance)))

    def test_unknown_component_is_not_silently_empty(self):
        instance = self.instance()
        self.world.actor.tags = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(entity(TARGET)))

    def test_missing_namespace_is_unsupported_after_input_evaluation(self):
        instance = self.instance()
        self.world.bound.namespace_present = False
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config(entity(TARGET)))
        self.assertEqual(self.world.calls, [('config', TARGET)])

    def test_retirement_and_wrong_identity_after_subscription_reject(self):
        for mutation in ('retire', 'identity'):
            with self.subTest(mutation=mutation):
                instance = self.instance()
                def watch(state, actor, action=mutation):
                    if action == 'retire':
                        self.world.bound.retire()
                    else:
                        actor.entity = 999
                self.world.bound.watch_tags = watch
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate(config(entity(TARGET)), self.watcher(instance))

    def test_malformed_tag_lists_reject_before_native_query(self):
        for value in ([-1], (True,), (-1,), (1 << 32,)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tags.Tags(base=value)


if __name__ == '__main__':
    unittest.main()
