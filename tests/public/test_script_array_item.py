"""Array selection used by authored PvE difficulty damage and health graphs."""

import unittest

from ow174.game.script import (
    expr,
    nodes,  # noqa: F401 -- register executable state handlers
    runtime,
)
from tests.runtime.test_game_graph_analysis import make_graph, node, output


def integer(value):
    return {"$": "STUConfigVarInt", "m_value": value}


def dynamic(var):
    return {"$": "STUConfigVarDynamic", "m_identifier": var, "m_60DB8F99": 1}


def select(container, index=None):
    return {"$": "STU_4BD39CFD", "m_814F8EC3": container, "m_index": index}


def group(*values):
    return {"$": "STU_06610EBC", "m_configVars": list(values)}


class ArrayItemTests(unittest.TestCase):
    def instance(self):
        component = runtime.Component(123, strict=True)
        return component.create(make_graph(1), init=False)

    def test_authored_enforcer_difficulty_damage_uses_collecting_container(self):
        instance = self.instance()
        cfg = select(group(*(integer(x) for x in (2, 4, 6, 8))), dynamic(9948))
        for difficulty, damage in enumerate((2, 4, 6, 8)):
            instance.component.set(9948, difficulty)
            self.assertEqual(instance.evaluate(cfg), damage)
        self.assertIsNone(instance.component.fault)

    def test_failed_missing_or_null_index_defaults_to_zero(self):
        instance = self.instance()
        failed = {"$": "STU_7BD2CA19", "m_resourceKey": None}
        for index in (None, failed, dynamic(999), group(failed, integer(1))):
            with self.subTest(index=index):
                self.assertEqual(instance.evaluate(select(group(integer(4), integer(8)), index)), 4)

    def test_array_only_lookup_bounds_conversion_and_successful_null(self):
        instance = self.instance()
        for container in (None, integer(7), group()):
            self.assertIsNone(instance.evaluate(select(container), initial=expr.Asset(99)))
        instance.component.set(600, ((11, 12), None, expr.Asset(13)))
        for index, expected in ((0, (11, 12)), (1, None), (2, expr.Asset(13)), (-1, None), (3, None)):
            with self.subTest(index=index):
                self.assertEqual(instance.evaluate(select(dynamic(600), integer(index))), expected)
        self.assertEqual(instance.evaluate(select(dynamic(600),
            {"$": "STUConfigVarFloat", "m_value": 1.5})), expr.Asset(13))

    def test_watches_both_index_and_container_through_boolean_switch(self):
        cfg = select(dynamic(600), dynamic(601))
        flag = dynamic(602)
        definition = make_graph(1, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STUStatescriptStateBooleanSwitch", state=0, fields={
                "m_condition": cfg, "m_onTruePlug": output([2, "m_inPlug"]),
                "m_onFalsePlug": output([3, "m_inPlug"]),
            }),
            node(2, "STUStatescriptActionSetVar", fields={"m_out_Var": flag, "m_value": integer(1)}),
            node(3, "STUStatescriptActionSetVar", fields={"m_out_Var": flag, "m_value": integer(0)}),
        ], states=[1], entries=[0])
        component = runtime.Component(123, strict=True)
        component.set(600, (False, True))
        component.set(601, 0)
        instance = component.create(definition)
        component.run_now()
        self.assertEqual(component.vars[602].value(), 0)
        component.set(601, 1)
        component.run_now()
        self.assertEqual(component.vars[602].value(), 1)
        component.set(600, (False, False))
        component.run_now()
        self.assertEqual(component.vars[602].value(), 0)
        self.assertFalse(instance.state(0).natives)

    def test_unknown_container_keeps_strict_failure(self):
        instance = self.instance()
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(select({"$": "STU_UNKNOWN_ARRAY_SOURCE"}))
        self.assertIsInstance(instance.component.fault, runtime.UnsupportedScriptError)
