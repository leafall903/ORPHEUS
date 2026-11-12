"""0A6BE10E array ordering, reactive replacement and output lifetime."""

import unittest

from ow174.game.script import expr, runtime
from tests.runtime.test_script_runtime_budget import graph, node


def dynamic(scope, identifier):
    return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": scope}


class ArrayFilterWorld(runtime.World):
    def array_filter_accepts(self, state, value):
        return value.id == 11


class ArrayFilterTests(unittest.TestCase):
    def create(self, world, *, source=None, limit=None):
        definition = graph([node(
            0, "STU_0A6BE10E", state=0,
            m_814F8EC3=source or dynamic(expr.INSTANCE, 14087),
            m_F2805C84=dynamic(expr.ENTITY, 11706),
            m_17A88AEF=limit,
            m_C485751C=1,
        )])
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(definition)
        return component, instance, instance.node_state(definition.node(0))

    def test_order_duplicates_replacement_and_clear(self):
        component, instance, state = self.create(ArrayFilterWorld())
        approved, rejected = expr.Entity(11), expr.Entity(12)
        source = (expr.INSTANCE, 14087)
        target = (expr.ENTITY, 11706)
        instance.write(source, (approved, rejected, 7, approved))
        state.begin()
        self.assertEqual(instance.context().var(*target), (approved, 7, approved))
        instance.write(source, (7, rejected, approved))
        component.run_frame(1)
        self.assertEqual(instance.context().var(*target), (7, approved))
        instance.write(source, ())
        component.run_frame(2)
        self.assertEqual(instance.context().var(*target), ())
        instance.write(source, (approved,))
        component.run_frame(3)
        state.end(False)
        self.assertEqual(instance.context().var(*target), ())
        instance.write(source, (7,))
        component.run_frame(4)
        self.assertEqual(instance.context().var(*target), ())

    def test_absent_source_leaves_unallocated_output_and_unknown_filter_fails(self):
        component, instance, state = self.create(runtime.World())
        state.begin()
        self.assertIsNone(instance.context().var(expr.ENTITY, 11706))
        instance.write((expr.INSTANCE, 14087), (expr.Entity(11),))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "array filter candidate service"):
            component.run_frame(1)

    def test_config_group_keeps_order_and_only_positive_limit_truncates(self):
        first, second = (expr.INSTANCE, 14087), (expr.ENTITY, 11186)
        limit, target = (expr.ENTITY, 11184), (expr.ENTITY, 11706)
        source = {"$": "STU_06610EBC", "m_configVars": [dynamic(*second), dynamic(*first)]}
        component, instance, state = self.create(ArrayFilterWorld(), source=source, limit=dynamic(*limit))
        approved, rejected = expr.Entity(11), expr.Entity(12)
        instance.write(first, (rejected, 7))
        instance.write(second, (approved,))
        instance.write(limit, 1)
        state.begin()
        self.assertEqual(instance.context().var(*target), (approved,))
        for frame, maximum in enumerate((0, -1, 2), 1):
            instance.write(limit, maximum)
            component.run_frame(frame)
            self.assertEqual(instance.context().var(*target), (approved, 7))
