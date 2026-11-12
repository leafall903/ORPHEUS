"""Caller-specific destination handling for the observed 104319 empty config."""

import unittest

from ow174.game.script import expr, runtime
from tests.public.test_script_for_code import KEY, make, row
from tests.script_fixtures import graph, node, plug, setter

EMPTY = {'$': 'STU_7BD2CA19', 'm_resourceKey': None, 'm_EE729DCB': 0}


def setting(position, value, var=7):
    result = setter(position, value)
    result['fields']['m_out_Var']['m_identifier'] = var
    return result


def fire(instance):
    instance.post(runtime.Event(0, 0, runtime.ENTRY, node=instance.graph.node(0)))
    instance.component.run_now()


class ConfigFailureTests(unittest.TestCase):
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
