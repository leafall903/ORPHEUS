"""Actual Mei primary spray slow and freeze against Pharah."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense

from ow174.game.collision import World
from ow174.game.commands import Command


class PharahFrostTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, mei, other = fixture.encounter("Mei")
        other.mover.position[0] = 20
        other.mover.body = None
        return fixture, run, pharah, mei

    def step(self, run, pharah, mei, *, spray=True, buttons=0, yaw=-32768, forward=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        pharah.take_commands([Command(run.frame, yaw=yaw, buttons=buttons, forward=forward)])
        mei.take_commands([Command(run.frame, yaw=-32768, buttons=int(spray))])

    def freeze(self, run, pharah, mei):
        for _ in range(160):
            self.step(run, pharah, mei)
            state = run.match.combat.frost.get(pharah.body)
            if state is not None and state.until:
                return state
        self.fail("Mei's spray did not freeze Pharah")

    def test_freeze_blocks_aim_movement_and_skills_until_exclusive_expiry(self):
        fixture, run, pharah, mei = self.arena()
        frost = self.freeze(run, pharah, mei)
        deadline = frost.until
        self.assertEqual(deadline - run.frame, 82)
        self.assertGreater(fixture.health(run, pharah).total, 0)
        start = tuple(pharah.mover.position)
        script = pharah.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x23)
        for _ in range(10):
            self.step(run, pharah, mei, buttons=1 | 2 | 8 | 16, yaw=0, forward=127)
        self.assertEqual(frost.until, deadline)
        while run.frame < deadline - 1:
            self.step(run, pharah, mei, spray=False, buttons=1 | 2 | 8 | 16, yaw=0, forward=127)
        self.assertEqual(tuple(pharah.mover.position), start)
        self.assertEqual(pharah.mover.yaw, frost.yaw)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.step(run, pharah, mei, spray=False, buttons=8, yaw=0)
        self.assertNotIn(pharah.body, run.match.combat.frost)
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_spray_freeze_preserves_concussion_and_barrage_until_a_fresh_press(self):
        for button in (16, 32):
            with self.subTest(button=button):
                _, run, pharah, mei = self.arena()
                fight = run.match.combat
                frost = self.freeze(run, pharah, mei)
                script = pharah.body_script
                charge = script.component.entity_var(342)
                full_charge = script.component.entity_var(338).value()
                script.set_ult_charge(full_charge)
                concussion = next(i for i in script.component.instances.values() if i.graph.index == 0x7B)
                cooldown = concussion.vars[61]

                while run.frame < frost.until - 1:
                    self.step(run, pharah, mei, spray=False, buttons=button)
                    self.assertFalse(script.ultimate_running())
                    self.assertFalse(any(rocket.shooter is pharah for rocket in fight.rockets))
                    self.assertEqual(charge.value(), full_charge)
                    self.assertEqual(cooldown.value(), 0)

                self.step(run, pharah, mei, spray=False)
                self.assertNotIn(pharah.body, fight.frost)
                for _ in range(30):
                    self.step(run, pharah, mei, spray=False)
                self.assertFalse(script.ultimate_running())
                self.assertFalse(any(rocket.shooter is pharah for rocket in fight.rockets))
                self.assertEqual(charge.value(), full_charge)
                self.assertEqual(cooldown.value(), 0)

                self.step(run, pharah, mei, spray=False, buttons=button)
                if button == 32:
                    self.assertTrue(script.ultimate_running())
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(cooldown.value(), 0)
                else:
                    for _ in range(30):
                        if any(rocket.shooter is pharah for rocket in fight.rockets):
                            break
                        self.step(run, pharah, mei, spray=False)
                    self.assertTrue(any(rocket.shooter is pharah for rocket in fight.rockets))
                    self.assertGreater(cooldown.value(), 8.5)
                    self.assertEqual(charge.value(), full_charge)

    def test_spray_freeze_interrupts_actual_barrage(self):
        _, run, pharah, mei = self.arena()
        for _ in range(55):
            self.step(run, pharah, mei)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, pharah, mei, buttons=32)
        self.assertTrue(script.ultimate_running())
        self.freeze(run, pharah, mei)
        self.assertFalse(script.ultimate_running())
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        count = volley.reported
        for _ in range(20):
            self.step(run, pharah, mei, spray=False)
        self.assertEqual(volley.reported, count)

    def test_spray_slow_reduces_throttle_and_expires_after_last_hit(self):
        _, run, pharah, mei = self.arena()
        fight = run.match.combat
        for _ in range(40):
            self.step(run, pharah, mei)
            if pharah.body in fight.frost:
                break
        frost = fight.frost[pharah.body]
        self.assertAlmostEqual(frost.progress(run.frame), .2)
        self.step(run, pharah, mei, spray=False, forward=127)
        self.assertGreater(pharah.mover.throttles[1], 0)
        self.assertLess(pharah.mover.throttles[1], 127)
        for _ in range(85):
            self.step(run, pharah, mei, spray=False)
        self.assertNotIn(pharah.body, fight.frost)
        self.step(run, pharah, mei, spray=False, forward=127)
        self.assertEqual(pharah.mover.throttles[1], 127)

    def test_wall_and_friendly_target_receive_no_frost(self):
        for reason in ("wall", "team"):
            with self.subTest(reason=reason):
                fixture, run, pharah, mei = self.arena()
                fight = run.match.combat
                if reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = mei.team = 0
                y = pharah.mover.position[1]
                wall = World([(-4, y, 4, -4, y + 5, 4, 4, y + 5, 4),
                              (-4, y, 4, 4, y + 5, 4, 4, y, 4)])
                with patch.object(fight, "_collision", return_value=wall if reason == "wall"
                                  else fight._collision()):
                    for _ in range(90):
                        self.step(run, pharah, mei)
                self.assertEqual(fixture.health(run, pharah).total, 200)
                self.assertNotIn(pharah.body, fight.frost)
