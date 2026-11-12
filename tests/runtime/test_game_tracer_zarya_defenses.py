"""Actual Genji, Matrix and Grasp against Zarya projectiles near Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer_dva_matrix as matrix_tests
import test_game_tracer_pulse_deflect as pulse_tests
import test_game_tracer_zarya_graviton as surge_tests

from ow174.game import combat, deflection
from ow174.game.content import find_hero


@unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile definition tags")
class TracerZaryaDefensesTests(unittest.TestCase):
    def arena(self, hero):
        self.case = surge_tests.TracerZaryaGravitonTests()
        self.case.setUp()
        self.run = self.case.run
        self.fight = self.case.fight
        self.caster = self.case.caster
        self.tracer = self.case.tracer
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.caster.team = 0, 1
        self.defender, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer, hero=hero)
        if hero == 0x02E000000000007A:
            mech = replace(find_hero("dva"), body=0x0400000000000407)
            self.assertTrue(self.run.match.switch_hero(self.defender, mech))
        self.defender.team = self.tracer.team
        self.case.case.case.place(self.defender, 0, 2)
        for _ in range(110 if hero == 0x02E000000000007A else 25):
            self.run.step({})

    def step(self, defense, shot=0, yaw=0):
        self.run.step({self.caster: (*self.case.case.aim, shot), self.defender: (yaw, 0, defense)})

    def fire(self, defense, shot, frames=85, yaw=0):
        for _ in range(6):
            self.step(defense, yaw=yaw)
        self.case.script.set_ult_charge(self.case.cost)
        self.step(defense, shot, yaw=yaw)
        if shot == 32:
            self.assertEqual(self.case.charge(), 0)
        returned = []
        for _ in range(frames):
            for rocket in self.fight.rockets:
                if rocket.shooter is self.defender and all(rocket is not item for item in returned):
                    returned.append(rocket)
                    self.assertIsNone(rocket.shot)
                    self.assertEqual(rocket.team, self.defender.team)
                    self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
            self.step(defense, yaw=yaw)
        return returned

    def test_actual_deflect_returns_secondary_damage_to_zarya(self):
        self.arena(0x02E0000000000029)
        reflected, = self.fire(16, 2)
        self.assertGreater(reflected.flown, 0)
        self.assertEqual(self.case.case.case.health(self.caster).total, 353)
        self.assertEqual(self.case.health.total, 150)
        self.assertTrue(self.defender.client.messages(combat.HIT))
        self.assertFalse(self.caster.client.messages(combat.HIT))

    def test_actual_deflect_changes_graviton_field_owner_and_team(self):
        self.arena(0x02E0000000000029)
        reflected, = self.fire(16, 32)
        field, = self.case.manager.fields
        self.assertIs(field.caster, self.defender)
        self.assertEqual(field.source, self.defender.body)
        self.assertEqual(reflected.graviton_body, self.defender.body)
        self.assertEqual(field.team, self.defender.team)
        self.assertLess(self.case.case.case.health(self.caster).total, 395)
        self.assertEqual(self.case.health.total, 150)
        self.assertNotIn(self.tracer.body, self.case.manager.player_pulls)

    def test_actual_matrix_absorbs_secondary_and_graviton_before_field(self):
        matrix_tests.TracerDvaMatrixTests.setUpClass()
        for shot in (2, 32):
            with self.subTest(shot=shot):
                self.arena(0x02E000000000007A)
                self.assertFalse(self.fire(2, shot, frames=65))
                self.assertFalse(self.case.manager.fields)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.case.health.total, 150)
                self.assertEqual(self.case.case.case.health(self.defender).total, 600)
                self.assertFalse(self.caster.client.messages(combat.HIT))

    def test_actual_grasp_credits_secondary_and_stops_graviton_deploy(self):
        for shot, expected in ((2, 47), (32, 5)):
            with self.subTest(shot=shot):
                self.arena(0x02E000000000023B)
                self.step(8)
                self.assertFalse(self.fire(0, shot, frames=58))
                grasp = next(i for i in self.defender.body_script.component.instances.values()
                             if i.graph.index == 0x1D02)
                self.assertEqual(grasp.vars[21312].value(), expected)
                self.assertFalse(self.case.manager.fields)
                self.assertEqual(self.case.health.total, 150)

    def test_rear_approach_is_not_reflected(self):
        self.arena(0x02E0000000000029)
        self.assertFalse(self.fire(16, 32, yaw=-32768))
        field, = self.case.manager.fields
        self.assertIs(field.caster, self.caster)
        self.assertLess(self.case.health.total, 150)

    def test_actual_winston_bubble_spends_secondary_without_blast_spill(self):
        self.arena(0x02E0000000000009)
        self.case.case.case.place(self.defender, 0, -4)
        self.step(16)
        for _ in range(5):
            self.step(0)
        bubble = self.fight.winston_barriers.actors[self.defender.body]
        self.assertEqual(bubble.health, 700)
        bubble.health = 30
        self.assertFalse(self.fire(0, 2, frames=65))
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.case.health.total, 150)
        self.assertFalse(self.caster.client.messages(combat.HIT))

    def test_graviton_contact_spends_winston_bubble_and_deploys_on_approach_side(self):
        self.arena(0x02E0000000000009)
        self.case.case.case.place(self.defender, 0, -4)
        self.step(16)
        for _ in range(5):
            self.step(0)
        bubble = self.fight.winston_barriers.actors[self.defender.body]
        self.case.script.set_ult_charge(self.case.cost)
        self.step(0, 32)
        field = self.case.field()
        self.assertEqual(bubble.health, 695)
        self.assertGreater(field.position[2], self.tracer.mover.position[2])
        self.assertEqual(self.case.health.total, 150)
