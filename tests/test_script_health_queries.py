"""Native-backed pool math, explicit owner binding and actual combat updates."""

import struct
import unittest

from test_game_graph_analysis import make_graph

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import expr, runtime
from ow174.game.script.health_queries import GETTERS, pool_sum


def records(*entries):
    result = [None] * 48
    for slot, pool_id, maximum, current, normal, temporary in entries:
        result[slot] = struct.pack("<IffI4B", pool_id, maximum, current, slot // 16, 0, normal, temporary, 1)
    return tuple(result)


class HealthQueryTests(unittest.TestCase):
    def test_all_current_and_normal_current_maximum_are_distinct(self):
        pools = records((0, 1, 250, 150, 1, 0), (16, 2, 100, 20, 1, 0),
                        (32, 3, 100, 30, 2, 0), (1, 4, 50, 40, 0, 0),
                        (17, 5, 80, 60, 1, 2), (33, 0, 9999, 9999, 1, 0))
        self.assertEqual(pool_sum(pools), 300)
        self.assertEqual(pool_sum(pools, normal_only=True), 200)
        self.assertEqual(pool_sum(pools, maximum=True, normal_only=True), 450)

    def test_each_addition_rounds_float32_in_pool_order(self):
        pools = records((0, 1, 0, 2**24, 1, 0), (1, 2, 0, 1, 1, 0),
                        (2, 3, 0, -(2**24), 1, 0))
        self.assertEqual(pool_sum(pools), 0)
        reordered = list(pools)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        self.assertEqual(pool_sum(reordered), 1)

    def test_raw_records_require_complete_native_layout(self):
        with self.assertRaisesRegex(ValueError, "all 48"):
            pool_sum(())
        with self.assertRaisesRegex(ValueError, "twenty-byte"):
            pool_sum((b"bad",) + (None,) * 47)

    def test_installed_owner_queries_use_pools_and_foreign_service_stays_unimplemented(self):
        for cls, expected in zip(GETTERS, (240, 200, 450), strict=True):
            with self.subTest(query=cls):
                component = runtime.Component(0xA0000101, strict=True)
                instance = component.create(make_graph(1))
                component.world.health_pools = records(
                    (0, 1, 250, 150, 1, 0), (16, 2, 100, 20, 1, 0),
                    (32, 3, 100, 30, 1, 0), (1, 4, 50, 40, 0, 0))
                cfg = {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}}
                self.assertEqual(instance.evaluate(cfg), expected)
                component.set(17, expr.Entity(0xA0000102))
                cfg["m_entity"] = {"$": "STUConfigVarDynamic", "m_identifier": 17, "m_60DB8F99": 1}
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate(cfg)

    def test_explicit_pool_flags_and_values_are_causal_snapshot_inputs(self):
        component = runtime.Component(0xA0000101)
        component.create(make_graph(1))
        absent = snapshot_digest(export_component_vm(component))
        component.world.health_pools = records((0, 1, 200, 100, 1, 0))
        ordinary = snapshot_digest(export_component_vm(component))
        self.assertNotEqual(ordinary, absent)
        component.world.health_pools = records((0, 1, 200, 100, 0, 0))
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), ordinary)

    def test_actual_match_damage_and_repair_update_authoritative_pool_queries(self):
        from test_bastion_combat import BastionCombatTests

        case = BastionCombatTests()
        script = case.fixture()
        instance = next(iter(script.component.instances.values()))

        def current():
            return instance.evaluate({"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}})

        self.assertEqual(current(), 300)
        case.fight._damage(case.enemy, case.target(case.player), 100, False, None, case.run.frame)
        self.assertEqual(case.health(case.player).total, 205)
        self.assertEqual(current(), 205)
        case.step(2, 50)
        self.assertGreater(current(), 205)
        self.assertAlmostEqual(current(), case.health(case.player).total, places=4)


if __name__ == "__main__":
    unittest.main()
