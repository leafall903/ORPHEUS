"""Native primary-possessor queries and authoritative relationship lifetimes."""

import unittest

from ow174.game.script import expr, runtime, scene
from tests.test_script_play_loaded import dynamic
from tests.test_script_play_resource import EMPTY, array, integer
from tests.test_script_runtime_budget import graph, node, plug, setter
from tests.test_script_scene import ALPHA, reference

OWNER = {'$': 'STU_B5A0CAF0'}


def query(entity=OWNER):
    return {'$': 'STU_DB742151', 'm_entity': entity}


def world():
    result = scene.SceneWorld()
    for entity in (123, 456, 789):
        result.bind(entity)
    result.set_possessions(possessors=[123], primary=[(456, 123)])
    return result


def caller(result, entity=123):
    return runtime.Component(entity, world=result, strict=True).create(graph(), init=False)


class PossessionQueryTests(unittest.TestCase):
    def test_component44_body_query_uses_the_declared_field(self):
        result = world()
        config = {"$": "STU_00707E1E", "m_00903303": OWNER}
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(result).evaluate(config)
        self.assertEqual(caller(result, 456).evaluate(config), expr.Entity(0))
        result.set_component44_body_id(123, 0xB0102002)
        self.assertEqual(caller(result).evaluate(config), expr.Entity(0xB0102002))
        self.assertEqual(caller(result).evaluate(query()), expr.Entity(123))
        result.set_component44_body_id(123, 0)
        self.assertEqual(caller(result).evaluate(config), expr.Entity(0))
        result.set_possessor(123, False)
        self.assertEqual(caller(result).evaluate(config), expr.Entity(0))
        result.set_possessor(123, True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(result).evaluate(config)

    def test_direct_component_wins_and_reverse_lookup_returns_the_possessor_entity(self):
        result = world()
        active = caller(result)
        self.assertEqual(active.evaluate(query()), expr.Entity(123))
        active.write((0, 7), expr.Entity(456))
        self.assertEqual(active.evaluate(query(dynamic(7))), expr.Entity(123))
        result.set_possessor(789, True)
        result.set_primary_possessor(123, 789)
        self.assertEqual(active.evaluate(query()), expr.Entity(123))

    def test_missing_entities_relations_and_invalid_inputs_write_typed_invalid_entity(self):
        active = caller(world(), 789)
        for config in (query(), query(None), query(integer(123)), query(EMPTY),
                       query(reference(ALPHA)), query(dynamic(7))):
            with self.subTest(config=config):
                self.assertEqual(active.evaluate(config, initial=expr.Asset(17)), expr.Entity(0))
        active.write((0, 7), expr.Entity(999))
        self.assertEqual(active.evaluate(query(dynamic(7))), expr.Entity(0))

    def test_scalar_array_uses_first_config_including_its_failure(self):
        active = caller(world())
        self.assertEqual(active.evaluate(query(array(OWNER, EMPTY))), expr.Entity(123))
        self.assertEqual(active.evaluate(query(array(EMPTY, OWNER))), expr.Entity(0))
        self.assertEqual(active.evaluate(query(array(None, OWNER))), expr.Entity(0))
        active.write((0, 7), (expr.Entity(456), expr.Entity(789)))
        self.assertEqual(active.evaluate(query(dynamic(7))), expr.Entity(123))

    def test_switch_removes_old_body_relation_and_assigns_the_new_body(self):
        result = world()
        result.set_primary_possessor(456, None)
        result.set_primary_possessor(789, 123)
        self.assertEqual(result.primary_possessor(456), 0)
        self.assertEqual(result.primary_possessor(789), 123)
        self.assertEqual(result.primary_possessor(123), 123)

    def test_removing_possessor_component_clears_owned_relations(self):
        result = world()
        result.set_possessor(123, False)
        self.assertEqual(result.primary_possessor(123), 0)
        self.assertEqual(result.primary_possessor(456), 0)
        result.set_possessor(123, True)
        self.assertEqual(result.primary_possessor(123), 123)
        self.assertEqual(result.primary_possessor(456), 0)

    def test_unbind_cleans_both_sides_and_id_reuse_does_not_inherit_possession(self):
        result = world()
        result.unbind(456)
        result.bind(456)
        self.assertEqual(result.primary_possessor(456), 0)
        result.set_primary_possessor(456, 123)
        result.unbind(123)
        result.bind(123)
        self.assertEqual(result.primary_possessor(123), 0)
        self.assertEqual(result.primary_possessor(456), 0)
        self.assertFalse(result.primary_possessors)

    def test_unknown_catalog_and_inner_services_still_stop_strict_execution(self):
        result = scene.SceneWorld()
        result.bind(123)
        self.assertIs(result.primary_possessor(123), NotImplemented)
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(result).evaluate(query())
        result.set_possessions()
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(result).evaluate(query({'$': 'UnknownInput'}))
        self.assertEqual(caller(result).evaluate(query()), expr.Entity(0))

    def test_invalid_catalog_updates_are_atomic_and_require_bound_component_owners(self):
        result = world()
        for options in ({'possessors': [999]}, {'possessors': [True]},
                        {'possessors': [123], 'primary': [(456, 789)]},
                        {'possessors': [123], 'primary': [(456, 123), (456, 123)]},
                        {'possessors': [123], 'primary': [(999, 123)]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                result.set_possessions(**options)
            self.assertEqual(result.primary_possessor(456), 123)
        for operation in (lambda: result.set_possessor(123, 1),
                          lambda: result.set_primary_possessor(456, 789),
                          lambda: result.set_primary_possessor(True, 123)):
            with self.assertRaises(ValueError):
                operation()
        unknown = scene.SceneWorld()
        unknown.bind(123)
        for operation in (lambda: unknown.set_possessor(123, True),
                          lambda: unknown.set_primary_possessor(123, None)):
            with self.assertRaises(ValueError):
                operation()

    def test_watched_component44_body_query_polls_field_changes_and_removal(self):
        result = world()
        result.set_component44_body_id(123, 0)
        condition = {"$": "STU_00707E1E", "m_00903303": OWNER}
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'STUStatescriptStateBooleanSwitch', state=0, m_2BBEEAB8=1,
                            m_condition=condition, m_onTruePlug=plug((2, 'm_inPlug')),
                            m_onFalsePlug=plug((3, 'm_inPlug'))), setter(2, 1), setter(3, 0)],
                      entries=[0])
        component = runtime.Component(123, world=result, strict=True)
        active = component.create(model)
        component.run_now()
        self.assertEqual(active.find_var(0, 7).value(), 0)
        self.assertTrue(active.states[0].polled)
        result.set_component44_body_id(123, 456)
        component.run_frame(1)
        self.assertEqual(active.find_var(0, 7).value(), 1)
        result.set_possessor(123, False)
        component.run_frame(2)
        self.assertEqual(active.find_var(0, 7).value(), 0)

    def test_watched_query_polls_live_relation_changes(self):
        result = world()
        result.set_primary_possessor(456, None)
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'STUStatescriptStateBooleanSwitch', state=0, m_2BBEEAB8=1,
                            m_condition=query(OWNER), m_onTruePlug=plug((2, 'm_inPlug')),
                            m_onFalsePlug=plug((3, 'm_inPlug'))), setter(2, 1), setter(3, 0)],
                      entries=[0])
        component = runtime.Component(456, world=result, strict=True)
        active = component.create(model)
        component.run_now()
        self.assertEqual(active.find_var(0, 7).value(), 0)
        self.assertTrue(active.states[0].polled)
        result.set_primary_possessor(456, 123)
        component.run_frame(1)
        self.assertEqual(active.find_var(0, 7).value(), 1)
        result.unbind(123)
        component.run_frame(2)
        self.assertEqual(active.find_var(0, 7).value(), 0)


if __name__ == '__main__':
    unittest.main()
