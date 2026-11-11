"""Caller-specific destination handling for the observed 104319 empty config."""

import unittest

from ow174.game.script import expr, runtime
from tests.test_script_for_code import KEY, make, row
from tests.test_script_runtime_budget import graph, node, plug, setter

EMPTY = {'$': 'STU_7BD2CA19', 'm_resourceKey': None, 'm_EE729DCB': 0}


def setting(position, value, var=7):
    result = setter(position, value)
    result['fields']['m_out_Var']['m_identifier'] = var
    return result


def fire(instance):
    instance.post(runtime.Event(0, 0, runtime.ENTRY, node=instance.graph.node(0)))
    instance.component.run_now()


class ConfigFailureTests(unittest.TestCase):
    def test_prepared_empty_controller_resource_preserves_destination(self):
        context = expr.Context()
        destination = expr.Asset(42)
        cfg = {"$": "STU_C3619A38", "m_resourceKey": None, "m_F52607FF": {"$": "STU_B5A0CAF0"}}
        self.assertIs(expr.evaluate(cfg, context, initial=destination), destination)

    def test_empty_resource_preserves_destination_but_null_dynamic_overwrites(self):
        context = expr.Context()
        self.assertIsNone(expr.evaluate(EMPTY, context))
        destination = expr.Asset(123)
        self.assertIs(expr.evaluate(EMPTY, context, initial=destination), destination)
        self.assertIsNone(expr.evaluate(
            {'$': 'STUConfigVarDynamic', 'm_identifier': 1, 'm_60DB8F99': 0},
            context, initial=destination,
        ))

    def test_set_var_uses_integer_zero_and_follows_output(self):
        component = runtime.Component(123, strict=True)
        action = setter(0, 99)
        action['fields']['m_value'] = EMPTY
        action['fields']['m_outPlug'] = plug((1, 'm_inPlug'))
        instance = component.create(graph([action, setting(1, 17, var=8)]), init=False)
        instance.initialized = True
        instance.write((0, 7), 88)
        fire(instance)
        value = instance.find_var(0, 7).value()
        self.assertEqual(value, 0)
        self.assertIs(type(value), int)
        self.assertEqual(instance.find_var(0, 8).value(), 17)
        self.assertIsNone(component.fault)

    def test_missing_value_skips_assignment_but_follows_output(self):
        for absent in ('m_value', 'm_out_Var'):
            with self.subTest(absent=absent):
                component = runtime.Component(123, strict=True)
                action = setter(0, 99)
                action['fields'][absent] = None
                action['fields']['m_outPlug'] = plug((1, 'm_inPlug'))
                instance = component.create(graph([action, setting(1, 17, var=8)]), init=False)
                instance.initialized = True
                instance.write((0, 7), 88)
                fire(instance)
                self.assertEqual(instance.find_var(0, 7).value(), 88)
                self.assertEqual(instance.find_var(0, 8).value(), 17)

    def test_base_action_fallback_follows_output_and_succeeds_with_no_link(self):
        for cls in ("STU_0B0BABC6", "STU_9F3FE778"):
            with self.subTest(cls=cls):
                component = runtime.Component(123, strict=True)
                relay = node(0, cls, m_outPlug=plug((1, "m_inPlug")))
                empty = node(2, cls)
                instance = component.create(graph([relay, setting(1, 17), empty]), init=False)
                self.assertTrue(instance.run_node(instance.graph.node(0), None))
                self.assertEqual(instance.find_var(0, 7).value(), 17)
                self.assertTrue(instance.run_node(instance.graph.node(2), None))

    def test_animation_alias_action_writes_typed_result_then_follows_output(self):
        alias = 0x0C80000000000561
        resolved = 0x0C8000000000BEEF

        class AnimationWorld(runtime.World):
            def __init__(self, result):
                super().__init__()
                self.result = result
                self.lookups = []

            def resolve_animation_alias(self, instance, alias):
                self.lookups.append((instance.component.entity, alias))
                return self.result

        for configured, result, expected in (
            ((expr.Asset(alias),), resolved, resolved),
            (expr.Asset(alias | 0x1000000000000000), resolved, resolved),
            (expr.Asset(alias), None, 0),
            (expr.Asset(0x0400000000000001), NotImplemented, 0),
        ):
            with self.subTest(configured=configured, result=result):
                world = AnimationWorld(result)
                component = runtime.Component(123, world=world, strict=True)
                action = node(0, "STU_DE66D3F5",
                              m_59FEECBB={"$": "STU_EAA20CD6", "m_animAlias": configured},
                              m_0A915A83=setting(0, 0)["fields"]["m_out_Var"],
                              m_outPlug=plug((1, "m_inPlug")))
                instance = component.create(graph([action, setting(1, 17, var=8)]), init=False)
                instance.write((0, 7), 99)
                self.assertTrue(instance.run_node(instance.graph.node(0), None))
                self.assertEqual(instance.find_var(0, 7).value(), expr.Asset(expected))
                self.assertEqual(instance.find_var(0, 8).value(), 17)
                supplied = configured[0] if isinstance(configured, tuple) else configured
                self.assertEqual(world.lookups, [] if result is NotImplemented else [(123, supplied.guid)])

    def test_unknown_animation_resolver_stops_before_output(self):
        component = runtime.Component(123, strict=True)
        action = node(0, "STU_DE66D3F5",
                      m_59FEECBB={"$": "STU_EAA20CD6", "m_animAlias": expr.Asset(0x0C80000000000561)},
                      m_0A915A83=setting(0, 0)["fields"]["m_out_Var"],
                      m_outPlug=plug((1, "m_inPlug")))
        instance = component.create(graph([action, setting(1, 17, var=8)]), init=False)
        instance.write((0, 7), 99)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "animation resource host"):
            instance.run_node(instance.graph.node(0), None)
        self.assertEqual(instance.find_var(0, 7).value(), 99)
        self.assertIsNone(instance.find_var(0, 8, create=False))

    def test_successful_null_is_assigned_as_null(self):
        component = runtime.Component(123, strict=True)
        action = setter(0, 99)
        action['fields']['m_value'] = {
            '$': 'STUConfigVarDynamic', 'm_identifier': 8, 'm_60DB8F99': 0,
        }
        instance = component.create(graph([action]), init=False)
        instance.initialized = True
        instance.write((0, 7), 88)
        fire(instance)
        self.assertIsNone(instance.find_var(0, 7).value())

    def test_for_code_keeps_destination_on_failed_evaluation(self):
        component, _ = make([[row(EMPTY)]])
        destination = expr.Asset(123)
        self.assertEqual(component.code_value(KEY, initial=destination), (True, destination))
        self.assertEqual(component.code_value(KEY), (True, None))

    def test_nonempty_resource_key_remains_unsupported(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(graph([node(0, 'STUStatescriptEntry')]), init=False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate({**EMPTY, 'm_resourceKey': '0x0D80000000000001'}, initial=0)
        self.assertIsInstance(component.fault, runtime.UnsupportedScriptError)


if __name__ == '__main__':
    unittest.main()
