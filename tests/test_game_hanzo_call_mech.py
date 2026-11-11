"""Command-driven Call Mech checks with Hanzo and normal D.Va selection."""

import unittest
from unittest.mock import patch

import test_game_hanzo_self_destruct as bomb_tests

from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, dva_owned, zarya_barrier
from ow174.game.collision import World
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class HanzoCallMechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bomb_tests.HanzoSelfDestructTests.setUpClass()

    def arena(self, distance, health=500):
        fixture = bomb_tests.HanzoSelfDestructTests()
        run, pilot, old, hanzo, vitals = fixture.arena(25, health)
        fixture.cast(run, pilot, old)
        for _ in range(211):
            run.step({})
        self.assertNotIn(old, run.match.bots)
        self.assertIsNone(pilot.controlled_mech)
        hanzo.mover.position[:] = (0, 0, distance)
        hanzo.mover.body = None
        for _ in range(5):
            run.step({})
        component = pilot.body_script.component
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), component.entity_var(ULT_COST).value())
        return run, pilot, old, hanzo, vitals

    @staticmethod
    def call(run, pilot):
        run.step({pilot: (0, 0, 32)})
        for _ in range(100):
            run.step({})

    def test_missing_owned_resources_rejects_before_spending_charge(self):
        run, pilot, _, _, _ = self.arena(8)
        component = pilot.body_script.component
        cost = component.entity_var(ULT_COST).value()
        with patch.object(dva_owned, "available", return_value=False):
            self.call(run, pilot)
        self.assertFalse(run.match.bots)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)

    def test_landing_damage_knockback_and_owned_replacement(self):
        run, pilot, old, hanzo, vitals = self.arena(3.5)
        body = pilot.body
        fight = run.match.combat
        run.step({pilot: (0, 0, 32)})
        for _ in range(12):
            run.step({})
        replacement, = run.match.bots
        self.assertIsNot(replacement, old)
        self.assertIs(replacement.control_owner, pilot)
        self.assertEqual(replacement.position, (0, 0, 1.5))
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(vitals.total, 500)
        for _ in range(43):
            run.step({})
        self.assertEqual(vitals.total, 500)
        run.step({})
        self.assertEqual(vitals.total, 250)
        self.assertGreater(hanzo.mover.velocity[1], 0)
        self.assertGreater(hanzo.mover.velocity[2], 20)
        self.assertIsNone(pilot.controlled_mech)
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, 250)
        self.assertEqual(pilot.body, body)
        self.assertIs(pilot.controlled_mech, replacement)
        self.assertEqual(fight.vitals[replacement.entity].total, 600)
        self.assertEqual(pilot.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in pilot.client.messages(combat.HIT)], [250])

    def test_occupied_landing_retains_charge_and_accepts_fresh_press(self):
        run, pilot, _, hanzo, vitals = self.arena(1.5)
        component = pilot.body_script.component
        cost = component.entity_var(ULT_COST).value()
        self.call(run, pilot)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(run.match.bots)
        self.assertEqual(vitals.total, 500)
        hanzo.mover.position[:] = (0, 0, 4.6)
        hanzo.mover.body = None
        self.call(run, pilot)
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertEqual(vitals.total, 500)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)

    def test_charge_gate_and_allied_landing(self):
        run, pilot, _, hanzo, vitals = self.arena(3.5)
        pilot.body_script.ult_seconds = 0
        component = pilot.body_script.component
        cost = component.entity_var(ULT_COST).value()
        component.set(ULT_CHARGE, cost - 1)
        self.call(run, pilot)
        self.assertFalse(run.match.bots)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost - 1)
        component.set(ULT_CHARGE, cost)
        hanzo.team = pilot.team
        self.call(run, pilot)
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertEqual(vitals.total, 500)
        self.assertFalse(pilot.client.messages(combat.HIT))

    def test_wall_between_landing_and_hanzo_stops_damage(self):
        run, pilot, _, _, vitals = self.arena(3.5)
        run.match.collision_world = World(box(-20, 20, -1, 0, -20, 20) + wall(-2, 2.7, 2, 2.7))
        self.call(run, pilot)
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertEqual(vitals.total, 500)

    def test_projected_shield_blocks_landing_including_the_breaking_hit(self):
        run, pilot, _, hanzo, vitals = self.arena(3.5)
        fixture = bomb_tests.HanzoSelfDestructTests()
        zarya = fixture.add_player(run, hanzo, zarya_barrier.HERO, 4, 5)
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        angles = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({pilot: (0, 0, 32)})
        for _ in range(40):
            run.step({})
        run.step({zarya: (*angles, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, hanzo.body))
        for _ in range(65):
            run.step({})
        self.assertEqual(vitals.total, 500)
        self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
        self.assertIsNotNone(pilot.controlled_mech)

    def test_replaces_the_defeated_original_and_can_repeat(self):
        import test_game_hanzo_owned_mech as mech_tests

        fixture = mech_tests.HanzoOwnedMechTests()
        run, pilot, old, hanzo, _, _ = fixture.arena(8)
        fight = run.match.combat
        current = old
        for _ in range(2):
            vitals = fight.vitals[current.entity] = combat.Vitals(current.entity, 1)
            angles = fixture.draw(run, pilot, current, hanzo)
            run.step({hanzo: (*angles, 0)})
            for _ in range(25):
                run.step({})
            self.assertTrue(vitals.dead)
            self.assertIsNone(pilot.controlled_mech)
            self.assertIn(current, run.match.bots)
            component = pilot.body_script.component
            cost = component.entity_var(ULT_COST).value()
            component.set(ULT_CHARGE, cost)
            self.call(run, pilot)
            self.assertIsNone(pilot.controlled_mech)
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
            self.assertEqual(run.match.bots, [current])
            self.call(run, pilot)
            self.assertIsNotNone(pilot.controlled_mech)
            self.assertNotIn(current, run.match.bots)
            current = pilot.controlled_mech

    def test_bow_kills_pilot_before_handoff_and_retires_replacement(self):
        run, pilot, _, hanzo, vitals = self.arena(8)
        fight = run.match.combat
        hanzo.mover.position[:] = (6, 0, 0)
        hanzo.mover.body = None
        angles = aim(fight._eye(hanzo), (0, 1.05, 0))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        run.step({pilot: (0, 0, 32), hanzo: (*angles, 1)})
        for _ in range(15):
            run.step({hanzo: (*angles, 1)})
        replacement, = run.match.bots
        self.assertIsNone(pilot.controlled_mech)
        health = fight.vitals[pilot.body] = combat.Vitals(pilot.body, 1)
        run.step({hanzo: (*angles, 0)})
        for _ in range(90):
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, hanzo.body)
        self.assertNotIn(replacement, run.match.bots)
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(vitals.total, 500)

    def test_entry_sweeps_late_wall_without_possessing_through_it(self):
        run, pilot, _, _, _ = self.arena(8)
        component = pilot.body_script.component
        root = next(i for i in component.instances.values() if i.graph.index == 0x026A)
        run.step({pilot: (0, 0, 32)})
        for _ in range(90):
            if (entry := root.states.get(47)) is not None and entry.active:
                break
            run.step({})
        self.assertTrue(entry.active)
        replacement, = run.match.bots
        start = pilot.mover.position[2]
        target = entry.evaluate(entry.node.field("m_865F3D01"), watch=False)
        barrier = target.z + pilot.mover.data.stand_radius - .05
        run.match.collision_world = World(box(-20, 20, -1, 0, -20, 20) + wall(-2, barrier, 2, barrier))
        for _ in range(100):
            run.step({})
        self.assertFalse(entry.active)
        self.assertGreater(pilot.mover.position[2], start)
        self.assertLess(pilot.mover.position[2], target.z - .04)
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(replacement.control_owner, pilot)
