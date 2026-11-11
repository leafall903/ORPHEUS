"""Actual Mei Q against Pharah's health, flight and team boundaries."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense

from ow174.game.collision import World
from ow174.game.content import heroes


class PharahBlizzardTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, mei, other = fixture.encounter("Mei")
        other.mover.position[0] = 30
        other.mover.body = None
        return fixture, run, pharah, mei

    def launch(self, run, mei):
        fight = run.match.combat
        script = mei.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({mei: (-32768, 3800, 32)})
        for _ in range(125):
            if fight.blizzards:
                break
            run.step({mei: (-32768, 3800, 0)})
        area, = fight.blizzards.values()
        self.assertEqual(script.component.entity_var(342).value(), 0)
        return area

    def test_actual_q_integrates_85_damage_and_freezes_pharah(self):
        fixture, run, pharah, mei = self.arena()
        area = self.launch(run, mei)
        vitals = fixture.health(run, pharah)
        fight = run.match.combat
        self.assertEqual(vitals.total, 200)
        self.assertFalse(fight.frost)
        frozen = False
        while area.entity in fight.blizzards:
            run.step({})
            frozen |= fight.is_incapacitated(pharah)
        self.assertTrue(frozen)
        self.assertAlmostEqual(vitals.total, 115, places=5)
        self.assertFalse(vitals.dead)
        for _ in range(100):
            run.step({})
        self.assertNotIn(pharah.body, fight.frost)

    def test_wall_range_and_team_exclude_landed_blizzard(self):
        for reason in ("wall", "range", "team"):
            with self.subTest(reason=reason):
                fixture, run, pharah, mei = self.arena()
                fight = run.match.combat
                area = self.launch(run, mei)
                if reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = mei.team = 0
                elif reason == "range":
                    pharah.mover.position[0] = 15
                    pharah.mover.body = None
                y = pharah.mover.position[1]
                mid = area.point[2] / 2
                wall = World([(-6, y - 1, mid, -6, y + 5, mid, 6, y + 5, mid),
                              (-6, y - 1, mid, 6, y + 5, mid, 6, y - 1, mid)])
                with patch.object(fight, "_collision", return_value=wall if reason == "wall"
                                  else fight._collision()):
                    while area.entity in fight.blizzards:
                        run.step({})
                self.assertEqual(fixture.health(run, pharah).total, 200)
                self.assertNotIn(pharah.body, fight.frost)

    def test_blizzard_interrupts_active_barrage(self):
        _, run, pharah, mei = self.arena()
        self.launch(run, mei)
        fight = run.match.combat
        for _ in range(100):
            run.step({})
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())
        for _ in range(125):
            if fight.is_incapacitated(pharah):
                break
            run.step({pharah: (-32768, 0, 0)})
        self.assertTrue(fight.is_incapacitated(pharah))
        self.assertFalse(script.ultimate_running())

    def test_blizzard_slow_preserves_hover_until_freeze_then_fresh_input_recovers(self):
        _, run, pharah, mei = self.arena()
        self.launch(run, mei)
        fight = run.match.combat
        hover = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x25)
        fuel = hover.vars[87]
        initial_fuel = fuel.value()
        ground = pharah.mover.position[1]
        for _ in range(170):
            run.step({})
            frost = fight.frost.get(pharah.body)
            if frost is not None and frost.progress(run.frame) >= .85:
                break
        self.assertIsNotNone(frost)
        self.assertGreaterEqual(frost.progress(run.frame), .85)
        self.assertFalse(frost.until)
        for _ in range(10):
            run.step({pharah: (0, 0, 2)})
        self.assertFalse(frost.until)
        self.assertLess(fuel.value(), initial_fuel)
        self.assertGreater(pharah.mover.position[1], ground)
        self.assertGreater(pharah.mover.velocity[1], 0)

        for _ in range(80):
            run.step({pharah: (0, 0, 2)})
            if frost.until:
                break
        self.assertTrue(fight.is_incapacitated(pharah))
        frozen_fuel = fuel.value()
        for _ in range(30):
            run.step({pharah: (0, 0, 2)})
            self.assertGreaterEqual(fuel.value(), frozen_fuel)
        self.assertLess(pharah.mover.velocity[1], 0)

        while run.frame < frost.until - 1:
            run.step({})
        self.assertTrue(fight.is_incapacitated(pharah))
        recovered_fuel = fuel.value()
        for _ in range(15):
            run.step({pharah: (0, 0, 2)})
        self.assertFalse(fight.is_incapacitated(pharah))
        self.assertLess(fuel.value(), recovered_fuel)
        self.assertGreater(pharah.mover.velocity[1], 0)

    def test_charge_gate_and_pharah_lethal_rocket_before_release(self):
        fixture, run, pharah, mei = self.arena()
        fight = run.match.combat
        run.step({mei: (-32768, 3800, 32)})
        for _ in range(45):
            run.step({})
        self.assertFalse(fight.blizzard_flights)
        self.assertFalse(fight.blizzards)
        script = mei.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        vitals = fixture.health(run, mei)
        vitals.damage(199)
        run.step({mei: (-32768, 3800, 32), pharah: (0, 0, 1)})
        for _ in range(85):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertFalse(fight.blizzard_flights)
        self.assertFalse(fight.blizzards)

    def test_released_blizzard_keeps_hurting_after_mei_dies(self):
        fixture, run, pharah, mei = self.arena()
        area = self.launch(run, mei)
        vitals = fixture.health(run, mei)
        vitals.damage(199)
        fixture.fire(run, pharah)
        self.assertTrue(vitals.dead)
        fight = run.match.combat
        self.assertIn(area.entity, fight.blizzards)
        while area.entity in fight.blizzards:
            run.step({})
        self.assertAlmostEqual(fixture.health(run, pharah).total, 115, places=5)

    def test_genji_redirects_the_airborne_ult_and_protects_friendly_pharah(self):
        fixture, run, pharah, mei = self.arena()
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = genji.team = 0
        mei.team = 1
        genji.mover.position = [0, pharah.mover.position[1], 5.5]
        genji.mover.body = None
        run.step({genji: (0, 0, 16)})
        area = self.launch(run, mei)
        self.assertIs(area.owner, genji)
        fight = run.match.combat
        while area.entity in fight.blizzards:
            run.step({})
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertLess(fixture.health(run, mei).total, 200)

    def test_grasp_consumes_airborne_blizzard_without_creating_an_area(self):
        fixture, run, pharah, mei = self.arena()
        sigma = run.players[2]
        self.assertTrue(run.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = sigma.team = 0
        mei.team = 1
        sigma.mover.position = [0, pharah.mover.position[1], 5.5]
        sigma.mover.body = None
        script = mei.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        released = False
        for frame in range(90):
            run.step({sigma: (0, 0, 8 if frame == 0 else 0), mei: (-32768, 3800, 32 if frame == 0 else 0)})
            released |= bool(fight.blizzard_flights)
            self.assertFalse(fight.blizzards)
        self.assertTrue(released)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.blizzard_flights)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertNotIn(pharah.body, fight.frost)
