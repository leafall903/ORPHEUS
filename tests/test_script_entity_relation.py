"""Component43 relation query order, known absence, observations and lifetimes."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import entity_relation as relation
from ow174.game.script import expr, runtime

OWNER, BODY = 0xA0000100, 0xA0000101
BODY_VALUE = expr.Entity(BODY)


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.target = relation.Target(BODY, relation.Relation(OWNER))
        self.context = relation.Context(OWNER, True, True, True,
            lookup=self.lookup, watch_environment=self.watch_environment,
            watch_relation=self.watch_relation)
        self.callback = None

    def entity_relation_context(self, owner, *, component=None):
        return self.context

    def lookup(self, identifier):
        self.calls.append(('lookup', identifier))
        return self.target if self.target is not None and self.target.entity == identifier else None

    def watch_environment(self, watcher, kind):
        self.calls.append(('environment', kind))

    def watch_relation(self, watcher, target, value):
        self.calls.append(('relation', target.entity))
        if self.callback is not None:
            self.callback(target, value)

    def native(self, cfg, instance):
        if cfg.get('$') == 'TEST_VALUE':
            self.calls.append(('selector', cfg['value']))
            return cfg['value']
        return super().native(cfg, instance)


def config(value=BODY_VALUE):
    return {'$': relation.CLASS, 'm_801B298F': {'$': 'TEST_VALUE', 'value': value}}


class EntityRelationTests(unittest.TestCase):
    def setup_query(self):
        self.world = World()
        self.component = runtime.Component(OWNER, world=self.world, strict=True)
        self.instance = self.component.create(make_graph(1, [node(0, 'STUStatescriptState', state=0)],
                                                         states=[0]), init=False)
        self.watcher = self.instance.state(0)

    def test_unknown_backend_remains_strict(self):
        instance = runtime.Component(OWNER, strict=True).create(make_graph(1), init=False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_missing_context_or_namespace_preserves_output_without_selector(self):
        for field in ('context_present', 'namespace_present'):
            self.setup_query()
            setattr(self.world.context, field, False)
            marker = object()
            self.assertIs(self.instance.evaluate(config(), self.watcher, initial=marker), marker)
            self.assertEqual(self.world.calls, [])

    def test_missing_entity_observes_environment_and_preserves_caller_output(self):
        for present, kind in ((False, 46), (True, 0)):
            self.setup_query()
            self.world.target = None
            self.world.context.environment_present = present
            marker = object()
            self.assertIs(self.instance.evaluate(config(), self.watcher, initial=marker), marker)
            self.assertEqual(self.world.calls, [('selector', expr.Entity(BODY)), ('lookup', BODY),
                                              ('environment', kind)])

    def test_missing_entity_without_watcher_does_not_require_subscription(self):
        self.setup_query()
        self.world.target = None
        self.world.context.watch_environment = NotImplemented
        self.assertEqual(self.instance.evaluate(config(), initial=17), 17)

    def test_known_missing_component_succeeds_typed_zero_without_observation(self):
        self.setup_query()
        self.world.target.relation = None
        self.world.context.watch_relation = NotImplemented
        self.assertEqual(self.instance.evaluate(config(), self.watcher), expr.Entity(0))
        self.assertEqual(self.world.calls, [('selector', expr.Entity(BODY)), ('lookup', BODY)])

    def test_scalar_first_group_and_nested_first_value_conversion(self):
        self.setup_query()
        cfg = config()
        cfg['m_801B298F'] = {'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'TEST_VALUE', 'value': ((expr.Entity(BODY),),)}, {'$': 'UNKNOWN_LATER'}]}
        self.assertEqual(self.instance.evaluate(cfg), expr.Entity(OWNER))
        self.assertEqual(self.world.calls[-1], ('lookup', BODY))

    def test_nonentity_failed_and_invalid_inputs_still_lookup_zero_or_identity(self):
        for value, identifier in ((None, 0), (True, 0), (BODY, 0), (expr.Asset(BODY), 0),
                                  (expr.Entity(0xFFFFFFFF), 0xFFFFFFFF),
                                  (expr.Entity(BODY+(1 << 32)), BODY)):
            self.setup_query()
            self.instance.evaluate(config(value))
            self.assertEqual(self.world.calls[-1], ('lookup', identifier))
        self.setup_query()
        self.instance.evaluate({'$': relation.CLASS, 'm_801B298F': {
            '$': 'STU_7BD2CA19', 'm_resourceKey': None}})
        self.assertEqual(self.world.calls, [('lookup', 0)])

    def test_observation_precedes_getter_and_uses_captured_component(self):
        self.setup_query()
        def callback(target, captured):
            captured.entity = BODY
            target.relation = relation.Relation(123)
        self.world.callback = callback
        self.assertEqual(self.instance.evaluate(config(), self.watcher), expr.Entity(BODY))
        self.assertEqual(self.world.calls[-1], ('relation', BODY))

    def test_unknown_component_is_not_known_absence(self):
        self.setup_query()
        self.world.target.relation = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.instance.evaluate(config())

    def test_malformed_lookup_identity_and_relation_are_rejected(self):
        for mode in ('identity', 'relation'):
            self.setup_query()
            if mode == 'identity':
                self.world.context.lookup = lambda identity: relation.Target(123, None)
            else:
                self.world.target.relation.entity = -1
            with self.assertRaises(runtime.UnsupportedScriptError):
                self.instance.evaluate(config())

    def test_callback_context_rebind_and_retirement_abort(self):
        for mode in ('context', 'target', 'instance', 'rebind'):
            self.setup_query()
            def callback(target, captured, mode=mode):
                if mode == 'context':
                    self.world.context.retire()
                elif mode == 'target':
                    target.retired = True
                elif mode == 'instance':
                    self.instance.stopped = True
                else:
                    self.world.context = relation.Context(OWNER, True, True, True)
            self.world.callback = callback
            with self.assertRaises(runtime.UnsupportedScriptError):
                self.instance.evaluate(config(), self.watcher)

    def test_query_has_no_persistent_component_fields(self):
        self.setup_query()
        before = set(vars(self.component))
        self.assertEqual(self.instance.evaluate(config()), expr.Entity(OWNER))
        self.assertEqual(set(vars(self.component)), before)


if __name__ == '__main__':
    unittest.main()
