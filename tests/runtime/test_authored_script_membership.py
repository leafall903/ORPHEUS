"""Build 104319 authored membership regressions."""

import unittest
from unittest.mock import patch

from ow174.game.script import graph, nodes, runtime
from ow174.game.script.expr import INSTANCE, Asset


def instance(index):
    return runtime.Component(0x80000101).create(graph.graph(index), init=False)


@unittest.skipUnless(graph.GRAPHS_PATH.is_file(), "requires local 104319 graph data")
class AuthoredMembershipTests(unittest.TestCase):
    def test_authored_017b_condition_and_config_read_the_same_members(self):
        authored = instance(0x017B)
        expression = authored.graph.by_rid(18)
        condition = authored.graph.by_rid(44)
        config = expression.field("m_condition")
        for field in ("m_814F8EC3", "m_key", "m_value"):
            self.assertEqual(config[field], condition.field(field))
        target = Asset(0x0D800000000001FD)
        for container, expected in (((Asset(1), target), True), ((Asset(1),), False), (target, True)):
            with self.subTest(container=container):
                authored.write((INSTANCE, 659), container)
                for node in (expression, condition):
                    with patch.object(nodes, "branch", return_value=True) as branch:
                        self.assertTrue(authored.run_node(node, None))
                    branch.assert_called_once_with(authored, node, None, expected)

    def test_0254_authored_rifle_and_helix_tags_select_distinct_stat_conditions(self):
        authored = instance(0x0254)
        conditions = [authored.graph.by_rid(rid).field("m_condition") for rid in (118, 122)]
        for volley, expected in ((23, [True, False]), (35, [False, True])):
            with self.subTest(volley=volley):
                hit = authored.graph.by_rid(volley).field("m_6396149F")
                tags = tuple(
                    authored.evaluate(tag["m_EE3055BD"]) for tag in hit["m_modifyHealth"]["m_148F3152"]
                )
                authored.write((INSTANCE, 510), tags)
                self.assertEqual([authored.evaluate(config) for config in conditions], expected)

    def test_01c7_gain_uses_the_first_matching_authored_damage_tag(self):
        authored = instance(0x01C7)
        selector = authored.graph.by_rid(101)
        for tags, gain in (((0x4910,), -6), ((0x2D2F,), 0), ((0x4910, 0x490F), -3)):
            with self.subTest(tags=tags):
                authored.write((INSTANCE, 510), tuple(Asset(0x0D80000000000000 | tag) for tag in tags))
                self.assertTrue(authored.run_node(selector, None))
                self.assertEqual(authored.vars[18702].value(), gain)
