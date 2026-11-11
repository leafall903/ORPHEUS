"""Actual Blizzard throw and incoming Reinhardt combat."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
from test_game_combat import aim

from ow174.game import defenses, deflection, mei_frost, sigma_grasp
from ow174.game.content import heroes


class ReinhardtBlizzardTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    def arena(self):
        if fixtures.BARRIER_DATA.is_file():
            self.enterContext(patch.dict(os.environ, {
                "ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA),
            }))
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.place(self.enemy, (0, 1, 6))
        self.place(self.other, (20, 1, 0))
        self.fight = self.match.combat
        self.field = self.fight.blizzard
        self.angles = aim(self.fight._eye(self.enemy), (0, 1, 2))
        self.step(6)

    def step(self, count=1, *, mei=0, rein=0):
        for _ in range(count):
            self.run.step({self.enemy: (*self.angles, mei), self.rein: (0, 0, rein)})

    def launch(self, *, shield=0):
        script = self.enemy.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(mei=32, rein=shield)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.step(30, rein=shield)
        self.assertFalse(self.field.flights)
        self.step(rein=shield)
        [flight] = self.field.flights
        return flight

    def land(self, *, shield=0):
        for _ in range(110):
            self.step(rein=shield)
            if self.field.areas:
                break
        [area] = self.field.areas.values()
        return area

    def test_actual_throw_cost_gravity_area_delay_damage_and_expiry(self):
        self.arena()
        self.step(mei=32)
        self.step(40)
        self.assertFalse(self.field.flights)
        flight = self.launch()
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        self.assertEqual(flight.left, 60)
        area = self.land()
        self.assertEqual(self.health(self.rein), 500)
        self.assertAlmostEqual(area.position[1], 1)
        self.assertEqual(area.ends - area.starts, 4250)
        while self.run.frame * 16 <= area.starts:
            self.assertEqual(self.health(self.rein), 500)
            self.step()
        self.assertLess(self.health(self.rein), 500)
        while self.field.areas:
            self.step()
        self.assertAlmostEqual(self.health(self.rein), 457.5, places=4)
        self.step(90)
        self.assertFalse(mei_frost.frozen(self.fight, self.rein.body))
        self.assertAlmostEqual(self.health(self.rein), 457.5, places=4)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "authored Barrier Field descriptor required")
    def test_area_crosses_barrier_and_freeze_lowers_it(self):
        self.arena()
        self.step(18, rein=2)
        self.launch(shield=2)
        self.land(shield=2)
        barrier = self.fight.barriers[self.rein.body]
        self.assertTrue(barrier.active)
        self.step(60, rein=2)
        self.assertLess(self.health(self.rein), 500)
        self.assertEqual(barrier.current, 1600)
        self.assertTrue(barrier.active)
        for _ in range(155):
            self.step(rein=2)
            if mei_frost.frozen(self.fight, self.rein.body):
                break
        self.assertTrue(mei_frost.frozen(self.fight, self.rein.body))
        self.assertFalse(barrier.active)
        key = (self.rein.body, self.enemy.body, "frost")
        self.assertAlmostEqual(self.fight.controls[key] - self.fight.now, 1.3)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), "authored Barrier Field descriptor required")
    def test_throw_crosses_barrier_and_body_before_landing_without_direct_damage(self):
        self.arena()
        self.place(self.enemy, (0, 1, 3))
        self.angles = (-32768, 0)
        self.step(18, rein=2)
        flight = self.launch(shield=2)
        area = self.land(shield=2)
        self.assertNotIn(flight, self.field.flights)
        self.assertLess(area.position[2], 0)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.fight.barriers[self.rein.body].current, 1600)

    def test_static_cover_range_and_team_reject_area_damage(self):
        for case in ("wall", "range", "team"):
            with self.subTest(case=case):
                self.arena()
                if case == "wall":
                    fixtures.charge_wall(self, z=1)
                elif case == "range":
                    self.place(self.rein, (0, 1, -14))
                else:
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team = self.enemy.team
                self.launch()
                self.land()
                self.step(100)
                self.assertEqual(self.health(self.rein), 500)
                self.assertNotIn(self.rein.body, self.fight.frost)

    def test_body_replacement_retires_live_area(self):
        self.arena()
        self.launch()
        self.land()
        self.step(60)
        before = self.health(self.rein)
        self.assertLess(before, 500)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.assertFalse(self.field.areas)
        self.step(80)
        self.assertEqual(self.health(self.rein), before)

    def test_actual_earthshatter_interrupts_blizzard_before_release(self):
        self.arena()
        rein = self.rein.body_script
        rein.set_ult_charge(rein.component.entity_var(338).value())
        self.step(rein=32)
        self.step(12)
        mei = self.enemy.body_script
        mei.set_ult_charge(mei.component.entity_var(338).value())
        self.step(mei=32)
        self.assertEqual(mei.component.entity_var(342).value(), 0)
        self.step(45)
        self.assertTrue(self.fight.controlled(self.enemy.body))
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(self.field.flights)
        self.assertFalse(self.field.areas)

    def test_released_blizzard_survives_actual_caster_death(self):
        self.arena()
        self.place(self.enemy, (0, 1, 2.8))
        self.angles = aim(self.fight._eye(self.enemy), (0, 1, 1))
        self.step(4)
        self.launch()
        rein = self.rein.body_script
        rein.set_ult_charge(rein.component.entity_var(338).value())
        self.step(rein=32)
        self.step(45)
        self.assertEqual(self.health(self.enemy), 0)
        self.assertTrue(self.field.areas)
        before = self.health(self.rein)
        self.step(35)
        self.assertLess(self.health(self.rein), before)

    def test_actual_genji_reflects_throw_and_owns_resulting_area(self):
        self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.place(self.other, (0, 1, 4))
        self.step(5)
        self.run.step({self.other: (0, 0, 16)})
        flight = self.launch()
        area = self.land()
        self.assertIs(flight.owner, self.other)
        self.assertIs(area.owner, self.other)
        self.assertEqual(area.body, self.other.body)
        self.step(45)
        self.assertEqual(self.health(self.other), 200)
        self.assertLess(self.health(self.enemy), 250)

    def test_actual_ice_wall_occludes_area_until_cancelled(self):
        self.arena()
        angles = aim(self.fight._eye(self.enemy), (0, 1, 1))
        for button in (16, 0, 1, 0):
            self.run.step({self.enemy: (*angles, button)})
        self.assertEqual(len(self.fight.walls.pieces), 5)
        self.launch()
        self.land()
        self.step(65)
        self.assertEqual(self.health(self.rein), 500)
        self.assertNotIn(self.rein.body, self.fight.frost)
        self.step(mei=16)
        self.assertFalse(self.fight.walls.pieces)
        self.step(35)
        self.assertLess(self.health(self.rein), 500)
        self.assertIn(self.rein.body, self.fight.frost)

    def test_actual_grasp_consumes_throw_without_generating_shields(self):
        self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 4))
        self.step(5)
        self.run.step({self.other: (0, 0, 8)})
        flight = self.launch()
        self.step(125)
        self.assertNotIn(flight, self.field.flights)
        self.assertFalse(self.field.areas)
        self.assertEqual(self.health(self.other), 400)
        self.assertEqual(self.health(self.rein), 500)
