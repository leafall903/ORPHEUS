"""Native hostility decisions and ordered query/lifecycle boundaries."""

import unittest

from test_game_graph_analysis import make_graph, node
from test_script_array_item import group, integer

from ow174.game.script import entity_hostility as hostility
from ow174.game.script import expr, runtime

OWNER, OTHER, THIRD = 0xA0000101, 0xA0000201, 0xA0000301


def entity(value):
    return {'$': 'STU_TEST_TYPED_ENTITY', 'm_value': value}


def config(target=None, source=None, option=None):
    return {'$': hostility.CLASS, 'm_target': target, 'm_source': source, 'm_AFBBE9C3': option}


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.actors = {
            OWNER: hostility.Target(OWNER, 0, False, None),
            OTHER: hostility.Target(OTHER, 1, False, None),
        }
        self.calls = []
        self.bound = hostility.Context(OWNER, True, lookup=self.lookup, watch_environment=None)

    def lookup(self, identity):
        self.calls.append(('lookup', identity))
        return self.actors.get(identity)

    def entity_hostility_context(self, owner, *, component=None):
        return self.bound

    def native(self, cfg, instance):
        if cfg.get('$') == 'STU_TEST_TYPED_ENTITY':
            return expr.Entity(cfg['m_value'])
        return super().native(cfg, instance)


class HostilityTests(unittest.TestCase):
    def watcher(self, instance):
        definition = make_graph(2, [node(0, 'STUStatescriptState', state=0)], states=[0])
        return runtime.State(instance, definition.nodes[0], 0)

    def instance(self):
        self.world = World()
        return runtime.Component(OWNER, world=self.world, strict=True).create(make_graph(1), init=False)

    def query(self, instance, target=OTHER, source=OWNER):
        return instance.evaluate(config(entity(target), entity(source)))

    def test_typed_entity_inputs_query_enemy_and_reject_self_or_ally(self):
        instance = self.instance()
        self.assertTrue(self.query(instance))
        self.assertFalse(self.query(instance, OWNER))
        self.world.actors[OTHER] = hostility.Target(OTHER, 0, False, None)
        self.assertFalse(self.query(instance))

    def test_missing_entities_use_native_pointer_presence_rule(self):
        instance = self.instance()
        self.assertTrue(self.query(instance, 999))
        self.assertTrue(self.query(instance, OTHER, 999))
        self.assertFalse(self.query(instance, 999, 998))

    def test_option_branch_uses_target_team_before_identity_or_spectator_rejection(self):
        instance = self.instance()
        self.world.actors[OTHER] = hostility.Target(OTHER, 1, True)
        cfg = config(entity(OTHER), entity(OTHER), integer(1))
        self.assertTrue(instance.evaluate(cfg))
        self.world.actors[OTHER] = hostility.Target(OTHER, 0, True)
        self.assertFalse(instance.evaluate(cfg))

    def test_known_spectator_on_either_side_rejects(self):
        instance = self.instance()
        for actor, team in ((OWNER, 0), (OTHER, 1)):
            before = self.world.actors[actor]
            self.world.actors[actor] = hostility.Target(actor, team, True, None)
            self.assertFalse(self.query(instance))
            self.world.actors[actor] = before

    def test_neutral_group_dependency_and_unresolved_component_marker(self):
        instance = self.instance()
        self.world.actors.update({key: hostility.Target(key, -1, False, None) for key in (OWNER, OTHER)})
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.query(instance)
        instance = self.instance()
        self.world.bound.same_group = lambda source, target: True
        self.world.actors.update({key: hostility.Target(key, -1, False, None) for key in (OWNER, OTHER)})
        self.assertFalse(self.query(instance))
        self.world.actors[OTHER] = hostility.Target(OTHER, -1, False,
                                                  hostility.Relation(999, 0, OTHER, 0))
        self.assertTrue(self.query(instance))  # unresolved component50 skips neutral group check

    def test_component_identity_equality_rejects_before_team_check(self):
        instance = self.instance()
        self.world.actors[OTHER] = hostility.Target(OTHER, 1, False,
                                                  hostility.Relation(OWNER, 0, OTHER, 0))
        self.assertFalse(self.query(instance))
        self.assertEqual(self.world.calls, [('lookup', OWNER), ('lookup', OTHER), ('lookup', OWNER)])

    def test_fallback_guard_and_resolved_owner_feed_group_query(self):
        instance = self.instance()
        self.world.bound.fallback_owner = lambda actor: self.world.actors[OWNER]
        seen = []
        self.world.bound.same_group = lambda source, target: (
            seen.append((source.entity, target.entity)) or True)
        self.world.actors[OWNER] = hostility.Target(OWNER, 4, False, None)
        self.world.actors[THIRD] = hostility.Target(THIRD, 4, False, None)
        self.world.actors[OTHER] = hostility.Target(OTHER, 4, False,
                                                  hostility.Relation(999, THIRD, OTHER, OTHER))
        self.assertFalse(self.query(instance))
        self.assertEqual(seen, [(OWNER, OWNER)])
        seen.clear()
        self.world.actors[OTHER] = hostility.Target(OTHER, 4, False,
                                                  hostility.Relation(999, THIRD, OTHER, THIRD))
        self.assertTrue(self.query(instance))
        self.assertEqual(seen, [])

    def test_unknown_component_presence_is_not_assumed_absent(self):
        instance = self.instance()
        self.world.actors[OTHER] = hostility.Target(OTHER, 1, False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.query(instance)

    def test_no_context_preserves_caller_destination_without_reading_configs(self):
        instance = self.instance()
        self.world.bound.present = False
        bad = {'$': 'STU_UNKNOWN_HOSTILITY_INPUT'}
        self.assertEqual(instance.evaluate(config(bad, bad, bad), initial=83), 83)
        self.assertEqual(self.world.calls, [])

    def test_scalar_group_selection_and_failed_entity_input(self):
        instance = self.instance()
        failed = {'$': 'STU_7BD2CA19', 'm_resourceKey': None}
        cfg = config(group(entity(OTHER), failed), group(entity(OWNER), failed))
        self.assertTrue(instance.evaluate(cfg))
        self.assertTrue(instance.evaluate(config(failed, entity(OWNER))))

    def test_context_retirement_during_lookup_fails_before_result(self):
        instance = self.instance()
        def lookup(identity):
            self.world.bound.retire()
            return self.world.actors.get(identity)
        self.world.bound.lookup = lookup
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.query(instance)

    def test_dependency_subscription_precedes_configs_then_source_target_lookup(self):
        instance = self.instance()
        calls = []
        original = self.world.native
        def native(cfg, current):
            calls.append(('config', cfg['m_value']))
            return original(cfg, current)
        self.world.native = native
        def watch(state, kind):
            calls.append(('watch', kind))
        def lookup(identity):
            calls.append(('lookup', identity))
            return self.world.actors.get(identity)
        self.world.bound.watch_environment = watch
        self.world.bound.lookup = lookup
        # This ordinary State supplies the observer role; no authored state runs.
        watcher = self.watcher(instance)
        self.assertTrue(instance.evaluate(config(entity(OTHER), entity(OWNER)), watcher))
        self.assertEqual(calls, [('watch', 2), ('config', OTHER), ('config', OWNER),
                                 ('lookup', OWNER), ('lookup', OTHER)])

    def test_retirement_from_environment_subscription_prevents_config_reads(self):
        instance = self.instance()
        self.world.bound.watch_environment = lambda state, kind: self.world.bound.retire()
        watcher = self.watcher(instance)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config({'$': 'STU_UNKNOWN_AFTER_RETIREMENT'}), watcher)
        self.assertEqual(self.world.calls, [])

    def test_malformed_lookup_result_and_unknown_neutral_result_fail(self):
        instance = self.instance()
        self.world.bound.lookup = lambda identity: True
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.query(instance)
        instance = self.instance()
        self.world.actors.update({key: hostility.Target(key, 4, False, None) for key in (OWNER, OTHER)})
        self.world.bound.same_group = lambda source, target: None
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.query(instance)


if __name__ == '__main__':
    unittest.main()
