"""Unkeyed membership in authored conditions and config expressions."""

import unittest
from unittest.mock import patch

from ow174.game.script import expr
from ow174.game.script.expr import Asset


class MembershipTests(unittest.TestCase):
    def test_config_matches_members_and_singletons_with_vm_equality(self):
        ctx = expr.Context()
        for container, value, expected in (
            ((Asset(1), Asset(2)), Asset(2), True),
            ((Asset(1), Asset(2)), Asset(3), False),
            (Asset(2), Asset(2), True),
            ((1, 2), 2.0, True),
            ((), None, False),
            (None, False, False),
        ):
            with self.subTest(container=container, value=value):
                config = {"$": "STU_45EDF12D", "m_814F8EC3": container, "m_value": value}
                self.assertIs(expr.evaluate(config, ctx), expected)

    def test_keyed_config_leaves_its_operands_to_the_existing_native_query(self):
        ctx = expr.Context()
        config = {
            "$": "STU_45EDF12D",
            "m_key": 1,
            "m_814F8EC3": {"$": "UnresolvedContainer"},
            "m_value": 2,
        }
        with patch.object(ctx, "native", return_value=None) as native:
            self.assertIsNone(expr.evaluate(config, ctx))
        native.assert_called_once_with(config)
