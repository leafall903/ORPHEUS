"""Actual shield graph inputs against the reconstructed Reinhardt barrier."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_pharah_defenses as defense

from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import expr, graph, health_queries
from ow174.game.script.runtime import UnsupportedScriptError


class ReinhardtResourceTests(unittest.TestCase):
    def use_pet_bundle(self):
        candidate = Path(os.environ.get("ORPHEUS_BARRIER_GRAPH_BUNDLE", ""))
        if not candidate.is_file():
            self.skipTest("Supply the prepared 104319 barrier graph closure")
        self.addCleanup(self.clear_graphs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", candidate))
        self.clear_graphs()

    def test_primary_hits_raised_mesh_and_debits_owned_pool(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, _ = fixture.encounter("Reinhardt")
        before = fixture.health(run, owner).total
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        run.step({shooter: (0, 0, 1), owner: (-32768, 0, 2)})
        for _ in range(45):
            run.step({owner: (-32768, 0, 2)})
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1480)
        self.assertEqual(fixture.health(run, owner).total, before)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        position = tuple(owner.mover.position)
        run.step({shooter: (0, 0, 16), owner: (-32768, 0, 2)})
        for _ in range(40):
            run.step({owner: (-32768, 0, 2)})
        self.assertEqual(tuple(owner.mover.position), position)
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1480)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        for _ in range(10):
            run.step({})
        fixture.fire(run, shooter)
        self.assertLess(fixture.health(run, owner).total, before)
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1480)
        for _ in range(210):
            run.step({})
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1600)

    def test_primary_breaks_shield_without_damaging_owner_on_break_impact(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, _ = fixture.encounter("Reinhardt")
        before = fixture.health(run, owner).total
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        combat = run.match.combat
        for _ in range(1400):
            run.step({shooter: (0, 0, 1), owner: (-32768, 0, 2)})
            if owner.body not in combat.barrier_pets:
                break
        self.assertNotIn(owner.body, combat.barrier_pets)
        self.assertEqual(fixture.health(run, owner).total, before)
        shield = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x95)
        self.assertEqual(shield.vars[586].value(), 0)
        self.assertFalse(shield.states[17].active)
        for _ in range(120):
            run.step({})
        for _ in range(10):
            run.step({owner: (-32768, 0, 2)})
            self.assertNotIn(owner.body, combat.barrier_pets)
        for _ in range(230):
            run.step({})
            self.assertNotIn(owner.body, combat.barrier_pets)
        self.assertEqual(shield.vars[61].value(), 0)
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        self.assertGreater(health_queries.pool_sum(combat.barrier_pets[owner.body].world.health_pools), 0)

    def test_map_wall_wins_before_raised_shield(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, _ = fixture.encounter("Reinhardt")
        before = fixture.health(run, owner).total
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        wall = defense.primary.PharahCombatTests.wall(self, shooter.mover.position[1])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            run.step({shooter: (0, 0, 1), owner: (-32768, 0, 2)})
            for _ in range(45):
                run.step({owner: (-32768, 0, 2)})
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1600)
        self.assertEqual(fixture.health(run, owner).total, before)

    def test_rocket_from_behind_hits_body_before_shield(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, _ = fixture.encounter("Reinhardt")
        before = fixture.health(run, owner).total
        for _ in range(20):
            run.step({owner: (0, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        run.step({shooter: (0, 0, 1), owner: (0, 0, 2)})
        for _ in range(45):
            run.step({owner: (0, 0, 2)})
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1600)
        self.assertLess(fixture.health(run, owner).total, before)
        for _ in range(450):
            run.step({shooter: (0, 0, 1), owner: (0, 0, 2)})
            if fixture.health(run, owner).dead:
                break
        self.assertTrue(fixture.health(run, owner).dead)
        self.assertNotIn(owner.body, run.match.combat.barrier_pets)

    def test_rocket_outside_mesh_edge_reaches_enemy_behind(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, behind = fixture.encounter("Reinhardt")
        shooter.mover.position[0] = behind.mover.position[0] = 4.5
        shooter.mover.body = behind.mover.body = None
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        run.step({shooter: (0, 0, 1), owner: (-32768, 0, 2)})
        for _ in range(45):
            run.step({owner: (-32768, 0, 2)})
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1600)
        self.assertEqual(fixture.health(run, behind).total, 80)

    def test_same_barrage_hits_raised_shield_then_exposes_owner_on_release(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, _ = fixture.encounter("Reinhardt")
        before = fixture.health(run, owner).total
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        shooter.body_script.set_ult_charge(shooter.body_script.component.entity_var(338).value())
        run.step({shooter: (0, 0, 32), owner: (-32768, 0, 2)})
        for _ in range(35):
            run.step({owner: (-32768, 0, 2)})
        self.assertGreater(health_queries.pool_sum(pet.world.health_pools), 0)
        self.assertLess(health_queries.pool_sum(pet.world.health_pools), 1600)
        self.assertEqual(fixture.health(run, owner).total, before)

        for _ in range(50):
            run.step({})
            if fixture.health(run, owner).total < before:
                break
        self.assertNotIn(owner.body, run.match.combat.barrier_pets)
        self.assertLess(fixture.health(run, owner).total, before)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_friendly_rocket_passes_through_raised_shield(self):
        self.use_pet_bundle()
        if not Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", "")).is_file():
            self.skipTest("Supply the prepared 104319 barrier mesh")
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, behind = fixture.encounter("Reinhardt")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = shooter.team
        behind.team = 1 - shooter.team
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        run.step({shooter: (0, 0, 1), owner: (-32768, 0, 2)})
        for _ in range(45):
            run.step({owner: (-32768, 0, 2)})
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1600)
        self.assertEqual(fixture.health(run, behind).total, 80)

    def test_secondary_creates_authored_pet_and_release_retires_pool(self):
        self.use_pet_bundle()
        run, owner, instance = self.arena()
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        pet = run.match.combat.barrier_pets[owner.body]
        self.assertEqual(instance.vars[38].value(), expr.Entity(pet.entity))
        self.assertEqual([child.graph.index for child in pet.instances.values()], [0x25B4, 0x96])
        self.assertEqual(health_queries.pool_sum(pet.world.health_pools), 1600)
        export_component_vm(pet)
        for _ in range(20):
            run.step({})
        self.assertNotIn(owner.body, run.match.combat.barrier_pets)
        self.assertFalse(pet.instances)
        self.assertIsNone(pet.world.health_pools)
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        second = run.match.combat.barrier_pets[owner.body]
        self.assertNotEqual(second.entity, pet.entity)
        owner.body_script.retire()
        self.assertNotIn(owner.body, run.match.combat.barrier_pets)
        self.assertFalse(second.instances)

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()
        graph._data.cache_clear()

    def arena(self):
        run, _, owner, _ = defense.PharahDefenseTests().encounter("Reinhardt")
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x95)
        return run, owner, instance

    def test_actual_initializer_has_full1600_and_secondary_reaches_authored_pet_state(self):
        run, owner, instance = self.arena()
        self.assertEqual(instance.vars[587].value(), 1600)
        self.assertEqual(instance.vars[586].value(), 1600)
        self.assertEqual(instance.vars[1616].value(), 1600)
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        self.assertTrue(instance.states[17].active)
        self.assertEqual(instance.states[17].node.cls, "STUStatescriptStatePet")

    def test_local_cached_override_preserves_zero_and_native_float_defaults(self):
        _, owner, instance = self.arena()
        component = owner.body_script.component
        for value, expected in ((None, 1), (False, 0), (0, 0), (.5, .5), (2, 2),
                                (expr.Asset(42), 1), ((), 1), ((None,), 0), (((.25,),), .25)):
            with self.subTest(value=value):
                component.set(0x254B, value)
                self.assertEqual(instance.evaluate({"$": "STU_295F1CDE"}), expected)

    def test_lowered_resource_waits_before_recharge_and_caps_at_authored_maximum(self):
        run, _, instance = self.arena()
        # A controlled resource-loss fixture, not an actual shield impact: the
        # physical Pet and its damage routing remain separate open requirements.
        instance.write((0, 586), 1000)
        for _ in range(80):
            run.step({})
        self.assertEqual(instance.vars[586].value(), 1000)
        for _ in range(30):
            run.step({})
        self.assertGreater(instance.vars[586].value(), 1000)
        self.assertLess(instance.vars[586].value(), 1100)
        for _ in range(300):
            run.step({})
        self.assertEqual(instance.vars[586].value(), 1600)

    def test_strict_world_refuses_the_local_reconstruction(self):
        _, owner, instance = self.arena()
        owner.body_script.component.strict = True
        with self.assertRaises(UnsupportedScriptError):
            instance.evaluate({"$": "STU_295F1CDE"})


if __name__ == "__main__":
    unittest.main()
