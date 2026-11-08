"""Explicit pool math, owner lookup boundaries and passive snapshot causality."""

import struct
import unittest

from test_game_graph_analysis import make_graph

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, runtime
from ow174.game.script.health_queries import GETTERS, pool_sum


def records(*entries):
    slots = [None] * 48
    for slot, ident, maximum, current, normal, temporary in entries:
        slots[slot] = struct.pack("<IffI4B", ident, maximum, current, slot // 16, 0, normal, temporary, 1)
    return tuple(slots)


class HealthQueryTests(unittest.TestCase):
    def test_all_current_normal_current_and_normal_maximum_are_distinct(self):
        pools = records((0, 1, 250, 150, 1, 0), (16, 2, 100, 20, 1, 0),
                        (32, 3, 100, 30, 2, 0), (1, 4, 50, 40, 0, 0),
                        (17, 5, 80, 60, 1, 2), (33, 0, 9999, 9999, 1, 0))
        self.assertEqual(pool_sum(pools), 300)
        self.assertEqual(pool_sum(pools, normal_only=True), 200)
        self.assertEqual(pool_sum(pools, maximum=True, normal_only=True), 450)

    def test_float32_rounding_occurs_in_slot_order(self):
        pools = records((0, 1, 0, 2**24, 1, 0), (1, 2, 0, 1, 1, 0),
                        (2, 3, 0, -(2**24), 1, 0))
        self.assertEqual(pool_sum(pools), 0)
        reordered = list(pools)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        self.assertEqual(pool_sum(reordered), 1)

    def test_incomplete_raw_layout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "all 48"):
            pool_sum(())
        with self.assertRaisesRegex(ValueError, "twenty-byte"):
            pool_sum((b"bad",) + (None,) * 47)

    def test_explicit_owner_service_is_distinct_from_missing_and_foreign_services(self):
        for cls, expected in zip(GETTERS, (240, 200, 450), strict=True):
            with self.subTest(query=cls):
                component = runtime.Component(0xA0000101, strict=True)
                instance = component.create(make_graph(1))
                cfg = {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}}
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate(cfg)
                # A failed strict component is poisoned: use a fresh lifecycle.
                component = runtime.Component(0xA0000101, strict=True)
                instance = component.create(make_graph(1))
                component.world.health_pools = records(
                    (0, 1, 250, 150, 1, 0), (16, 2, 100, 20, 1, 0),
                    (32, 3, 100, 30, 1, 0), (1, 4, 50, 40, 0, 0))
                self.assertEqual(instance.evaluate(cfg), expected)
                component.set(17, expr.Entity(0xA0000102))
                cfg["m_entity"] = {"$": "STUConfigVarDynamic", "m_identifier": 17, "m_60DB8F99": 1}
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate(cfg)

    def test_pool_values_and_flags_are_causal_stored_inputs(self):
        component = runtime.Component(0xA0000101)
        component.create(make_graph(1))
        absent = snapshot_digest(export_component_vm(component))
        component.world.health_pools = records((0, 1, 200, 100, 1, 0))
        ordinary = snapshot_digest(export_component_vm(component))
        self.assertNotEqual(ordinary, absent)
        component.world.health_pools = records((0, 1, 200, 100, 0, 0))
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), ordinary)
        component.world.health_pools = records((0, 1, 200, 50, 1, 0))
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), ordinary)
