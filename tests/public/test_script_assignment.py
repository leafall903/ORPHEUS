"""Authored array destinations use the same storage path as explicit element writes."""

import unittest

from ow174.game.script import nodes, runtime
from ow174.game.script.expr import INSTANCE, Vec3
from tests.script_fixtures import graph


def destination(index):
    return {
        "$": "STU_919FD47C",
        "m_expression": {
            "m_opcodes": [8, 0, 6, 0, 17, 0],
            "m_dynamicVars": [{"$": "STUConfigVarDynamic", "m_identifier": 12320}],
            "m_D99EF254": [index],
        },
    }


class AssignmentTests(unittest.TestCase):
    def test_indexed_destination_updates_one_element_and_preserves_the_other(self):
        instance = runtime.Component(0x80000101).create(graph(), init=False)
        previous = Vec3(0, 4, 0)
        current = Vec3(0, 3, 0)
        nodes.store(instance, destination(0), previous)
        nodes.store(instance, destination(1), current)
        self.assertEqual(instance.find_var(INSTANCE, 12320).value(), (previous, current))
        nodes.store(instance, destination(0), current)
        self.assertEqual(instance.find_var(INSTANCE, 12320).value(), (current, current))

    def test_invalid_or_negative_index_does_not_replace_the_array(self):
        instance = runtime.Component(0x80000101).create(graph(), init=False)
        instance.write((INSTANCE, 12320), (1, 2))
        for index in (-1, float("nan"), float("inf")):
            nodes.store(instance, destination(index), 3)
        self.assertEqual(instance.find_var(INSTANCE, 12320).value(), (1, 2))
