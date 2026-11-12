"""Priority predicates must react even when stack edits preserve the value."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import expr, runtime


class StackDependencyTests(unittest.TestCase):
    def test_equal_value_push_and_pop_switch_priority_predicate_but_not_scalar(self):
        variable = {"$": "STUConfigVarDynamic", "m_identifier": 7, "m_60DB8F99": 0}
        definition = make_graph(0xF001, [
            node(0, "STUStatescriptStateStack", state=0),
            node(1, "STUStatescriptStateBooleanSwitch", state=1, fields={
                "m_condition": {"$": "STU_3ACE35FB", "m_E50CF556": variable},
            }),
            node(2, "STUStatescriptStateBooleanSwitch", state=2,
                 fields={"m_condition": variable}),
        ], states=[0, 1, 2])
        component = runtime.Component(123)
        instance = component.create(definition)
        blocker, predicate, scalar = (instance.state(i) for i in range(3))
        blocker.begin()
        var = blocker.link((expr.INSTANCE, 7), priority=10, stack=True)
        predicate.begin()
        scalar.begin()
        self.assertIsNone(var.value())
        self.assertFalse(predicate.current)
        self.assertFalse(scalar.current)
        blocker.unlink(var)
        component.run_now()
        self.assertIsNone(var.value())
        self.assertTrue(predicate.current)
        self.assertFalse(scalar.current)
        self.assertFalse(scalar.pending)
        blocker.link((expr.INSTANCE, 7), priority=10, stack=True)
        component.run_now()
        self.assertIsNone(var.value())
        self.assertFalse(predicate.current)
        predicate.end(False)
        scalar.end(False)
        self.assertFalse(var.watchers)


if __name__ == "__main__":
    unittest.main()
