"""Native-backed pool getters, explicit bindings, real damage and passive storage."""

import struct
import unittest

import test_pharah_combat as primary
from test_game_graph_analysis import make_graph

from ow174.game import world
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import heroes
from ow174.game.script import expr, runtime
from ow174.game.script.driver import ReplicatedBody
from ow174.game.script.health_queries import GETTERS, pool_sum


def records(*rows):
    result = [None] * 48
    for slot, identifier, maximum, current, normal, temporary in rows:
        result[slot] = struct.pack("<IffI4B", identifier, maximum, current, slot // 16,
                                   0, normal, temporary, 1)
    return tuple(result)


class HealthPoolQueryTests(unittest.TestCase):
    def test_normal_flags_ids_and_current_maximum_select_different_pools(self):
        pools = records((0, 1, 250, 150, 1, 0), (16, 2, 100, 20, 1, 0),
                        (32, 3, 100, 30, 2, 0), (1, 4, 50, 40, 0, 0),
                        (17, 5, 80, 60, 1, 2), (33, 0, 9999, 9999, 1, 0))
        self.assertEqual(pool_sum(pools), 300)
        self.assertEqual(pool_sum(pools, normal_only=True), 200)
        self.assertEqual(pool_sum(pools, maximum=True, normal_only=True), 450)

    def test_each_pool_addition_rounds_float32_in_native_array_order(self):
        pools = records((0, 1, 0, 2**24, 1, 0), (1, 2, 0, 1, 1, 0),
                        (2, 3, 0, -(2**24), 1, 0))
        self.assertEqual(pool_sum(pools), 0)
        reordered = list(pools)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        self.assertEqual(pool_sum(reordered), 1)

    def test_malformed_layout_refuses_without_an_aggregate_substitute(self):
        with self.assertRaisesRegex(ValueError, "all48"):
            pool_sum(())
        with self.assertRaisesRegex(ValueError, "twenty-byte"):
            pool_sum((b"bad",) + (None,) * 47)

    def test_only_explicit_current_owner_binding_resolves_strict_queries(self):
        for cls, expected in zip(GETTERS, (240, 200, 450), strict=True):
            with self.subTest(query=cls):
                component = runtime.Component(0xA0000101, strict=True)
                instance = component.create(make_graph(1))
                component.world.health_pools = records(
                    (0, 1, 250, 150, 1, 0), (16, 2, 100, 20, 1, 0),
                    (32, 3, 100, 30, 1, 0), (1, 4, 50, 40, 0, 0))
                self.assertEqual(instance.evaluate({"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}}),
                                 expected)
                component.set(17, expr.Entity(0xA0000102))
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.evaluate({"$": cls, "m_entity": {
                        "$": "STUConfigVarDynamic", "m_identifier": 17, "m_60DB8F99": 1}})

    def test_aggregate_only_update_removes_unknown_pool_binding(self):
        component = runtime.Component(0xA0000101)
        component.create(make_graph(1))
        script = ReplicatedBody()
        script.component = component
        script.set_health(100, 200, health_pools=records((0, 1, 200, 100, 1, 0)))
        self.assertIsNotNone(component.world.health_pools)
        script.set_health(90, 200)
        self.assertIsNone(component.world.health_pools)
        self.assertEqual(component.entity_var(253).value(), 90)

    def test_passive_snapshot_stores_pool_values_and_flags_without_getter_execution(self):
        component = runtime.Component(0xA0000101)
        component.create(make_graph(1))
        absent = snapshot_digest(export_component_vm(component))
        component.world.health_pools = records((0, 1, 200, 100, 1, 0))
        ordinary = snapshot_digest(export_component_vm(component))
        self.assertNotEqual(absent, ordinary)
        component.world.health_pools = records((0, 1, 200, 100, 0, 0))
        self.assertNotEqual(ordinary, snapshot_digest(export_component_vm(component)))

    def test_actual_player_initialization_and_primary_damage_use_full_armour_pools(self):
        case = primary.PharahCombatTests()
        run, pharah, reinhardt = case.encounter()
        run.match.switch_hero(reinhardt, heroes()[0x02E0000000000007])
        reinhardt.mover.position = [0, pharah.mover.position[1], 8]
        reinhardt.mover.body = None
        component = reinhardt.body_script.component
        instance = next(iter(component.instances.values()))

        def query(cls):
            return instance.evaluate({"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}})

        self.assertEqual(component.entity_var(253).value(), 500)
        self.assertEqual(query("STU_9B7648C3"), 500)
        self.assertEqual(query("STU_32E6E4D8"), 500)
        self.assertEqual(query("STU_7D7D2470"), 500)
        case.fire_once(run, pharah)
        self.assertEqual(case.health(run, reinhardt).total, 390)
        self.assertEqual(query("STU_9B7648C3"), 390)
        self.assertEqual(query("STU_32E6E4D8"), 390)
        self.assertEqual(query("STU_7D7D2470"), 500)
        # Recreating the script on the same injured body must retain390/500,
        # rather than treating its current value as the new maximum.
        run.match.start_body_script(reinhardt, world.EntityUpdate(reinhardt.body, world.OP_CREATE))
        component = reinhardt.body_script.component
        instance = next(iter(component.instances.values()))
        self.assertEqual(component.entity_var(253).value(), 390)
        self.assertEqual(component.entity_var(1737).value(), 500)
        self.assertEqual(query("STU_9B7648C3"), 390)
        self.assertEqual(query("STU_7D7D2470"), 500)


if __name__ == "__main__":
    unittest.main()
