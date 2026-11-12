"""CreateEntity preserves output when the native definition selector fails."""

import unittest

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import expr, runtime
from tests.test_script_runtime_budget import graph, node, plug, setter


def dynamic(identifier):
    return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": 0}


class CreateEntityAdmissionTests(unittest.TestCase):
    def create(self, value):
        component = runtime.Component(123, strict=True)
        definition = graph([
            node(0, "STUStatescriptStateCreateEntity", state=0, m_entityDef=dynamic(9684),
                 m_E95B313B=dynamic(38), m_onBeginPlug=plug((1, "m_inPlug"))),
            setter(1, 17),
        ])
        original = expr.Entity(456)
        instance = component.create(definition, values={(expr.INSTANCE, 9684): value,
                                                         (expr.INSTANCE, 38): original})
        return component, instance, instance.node_state(definition.node(0)), original

    def test_empty_or_invalid_definition_preserves_output_and_follows_begin(self):
        for value in (None, (), (0,), expr.Asset(0x0580000000000E5E),
                      (0, expr.Asset(0x0400000000001E7D))):
            with self.subTest(value=value):
                component, instance, state, original = self.create(value)
                state.begin()
                component.run_frame(1)
                self.assertTrue(state.active)
                self.assertEqual(instance.vars[38].value(), original)
                self.assertEqual(instance.vars[7].value(), 17)
                self.assertTrue(export_component_vm(component))

    def test_valid_definition_requires_an_explicit_creation_service(self):
        component, instance, state, original = self.create((expr.Asset(0x0400000000001E7D),))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "entity creation service"):
            state.begin()
        self.assertIsNotNone(component.fault)
        self.assertEqual(instance.vars[38].value(), original)
        self.assertNotIn(7, instance.vars)
