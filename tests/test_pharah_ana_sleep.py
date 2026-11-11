"""Authored Sleep Dart input and Pharah interruption/recovery."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_control as control
import test_pharah_defenses as defense
from test_game_combat import aim

from ow174.game import combat
from ow174.game.collision import World


class PharahSleepTests(unittest.TestCase):
    step = control.PharahControlTests.step
    health = control.PharahControlTests.health

    def arena(self):
        run, pharah, ana, third = defense.PharahDefenseTests().encounter("Ana")
        return run, pharah, ana, third

    def dart(self, run, pharah, ana, *, buttons=0):
        self.step(run, pharah, ana, buttons=buttons, cast=8)
        fight = run.match.combat
        for _ in range(70):
            if fight.is_asleep(pharah):
                self.assertEqual(self.health(run, pharah).total, 195)
                return self.health(run, pharah).sleep_until
            self.step(run, pharah, ana, buttons=buttons)
        self.fail("Sleep Dart did not apply sleep")

    def test_sleep_rejects_flight_fire_and_melee_until_exclusive_expiry(self):
        run, pharah, ana, _ = self.arena()
        deadline = self.dart(run, pharah, ana)
        fight = run.match.combat
        self.assertEqual(deadline - fight.tick, 313)
        instances = {i.graph.index: i for i in pharah.body_script.component.instances.values()}
        position = tuple(pharah.mover.position)
        while run.frame < deadline - 1:
            self.step(run, pharah, ana, buttons=1 | 2 | 8 | 16 | 0x800, forward=127)
        self.assertEqual(tuple(pharah.mover.position), position)
        self.assertEqual(instances[0x23].vars[53].value(), 6)
        self.assertEqual(instances[0x27].vars[61].value(), 0)
        self.assertEqual(instances[0x7B].vars[61].value(), 0)
        self.step(run, pharah, ana, buttons=8)
        self.assertFalse(fight.is_asleep(pharah))
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_sleep_dart_during_jump_jet_preserves_ballistic_motion_and_cooldown(self):
        baseline, ground, ground_ana, _ = self.arena()
        initial_frame = baseline.frame
        self.dart(baseline, ground, ground_ana)
        hit_delay = baseline.frame - initial_frame
        self.assertGreater(hit_delay, 3)

        run, pharah, ana, _ = self.arena()
        control_run, free, free_ana, _ = self.arena()
        fight = run.match.combat
        jet = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x27)
        slept = False
        for frame in range(1, hit_delay + 19):
            buttons = 8 if frame == hit_delay - 1 else 0
            self.step(run, pharah, ana, buttons=buttons, cast=8 if frame == 1 else 0)
            self.step(control_run, free, free_ana, buttons=buttons)
            self.assertEqual(pharah.mover.position, free.mover.position)
            self.assertEqual(pharah.mover.velocity, free.mover.velocity)
            if not slept and fight.is_asleep(pharah):
                slept = True
                self.assertGreater(pharah.mover.velocity[1], 0)
                self.assertTrue(pharah.mover.airborne)
                self.assertEqual(self.health(run, pharah).total, 195)
        self.assertTrue(slept)
        cooldown = jet.vars[61].value()
        for frame in range(10):
            self.step(run, pharah, ana, buttons=8 if frame % 2 == 0 else 0)
            self.step(control_run, free, free_ana)
            self.assertTrue(fight.is_asleep(pharah))
            self.assertEqual(pharah.mover.position, free.mover.position)
            self.assertEqual(pharah.mover.velocity, free.mover.velocity)
        self.assertAlmostEqual(jet.vars[61].value(), cooldown - .16, places=3)

    def test_sleep_interrupts_barrage_and_released_volley_does_not_resume(self):
        run, pharah, ana, _ = self.arena()
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, pharah, ana, buttons=32)
        self.assertTrue(script.ultimate_running())
        deadline = self.dart(run, pharah, ana)
        self.assertFalse(script.ultimate_running())
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        count = volley.reported
        while run.frame <= deadline:
            self.step(run, pharah, ana, buttons=32)
        self.assertEqual(volley.reported, count)
        self.assertFalse(script.ultimate_running())

    def test_barrage_in_flight_still_hits_after_sleep_or_lethal_dart(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, pharah, ana, target = self.arena()
                fight = run.match.combat
                y = pharah.mover.position[1]
                ana.mover.position = [0, y, -8]
                target.mover.position = [30, y, 15]
                ana.mover.body = target.mover.body = None
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                if lethal:
                    self.health(run, pharah).damage(199)
                run.step({pharah: (0, 0, 32)})
                volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
                for _ in range(40):
                    if volley.reported >= 4:
                        break
                    run.step({})
                self.assertGreaterEqual(volley.reported, 4)
                direction = aim(fight._eye(ana), fight._eye(pharah))
                run.step({ana: (*direction, 8)})
                for _ in range(60):
                    if fight.is_dead(pharah) or fight.is_asleep(pharah):
                        break
                    run.step({ana: (*direction, 0)})
                self.assertEqual(fight.is_dead(pharah), lethal)
                self.assertEqual(fight.is_asleep(pharah), not lethal)
                self.assertFalse(script.ultimate_running())
                reports = volley.reported
                flying = [rocket for rocket in fight.rockets if rocket.shooter is pharah]
                self.assertTrue(flying)
                released = {id(rocket) for rocket in flying}

                points = [tuple(p + 12 * d for p, d in zip(rocket.position, rocket.direction, strict=True))
                          for rocket in flying]
                x, height, z = min(points, key=lambda p: abs(p[1] - (y + 1)))
                self.assertLess(abs(height - (y + 1)), .8)
                target.mover.position = [x, y, z]
                target.mover.body = None
                vitals = self.health(run, target)
                self.assertEqual(vitals.total, 200)
                for _ in range(40):
                    run.step({})
                    self.assertEqual(volley.reported, reports)
                    self.assertTrue(all(id(rocket) in released for rocket in fight.rockets
                                        if rocket.shooter is pharah))
                self.assertLess(vitals.total, 200)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_in_flight_concussion_keeps_impulse_after_sleep_or_lethal_dart(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, pharah, ana, enemy = self.arena()
                fight = run.match.combat
                y = pharah.mover.position[1]
                ana.mover.position = [4, y, 0]
                enemy.mover.position = [0, y, 24]
                ana.mover.body = enemy.mover.body = None
                vitals = self.health(run, pharah)
                if lethal:
                    vitals.damage(vitals.total - 1)

                run.step({pharah: (0, 0, 16)})
                for _ in range(40):
                    if fight.rockets:
                        break
                    run.step({})
                shot, = (rocket for rocket in fight.rockets if rocket.shooter is pharah)
                dart = aim(fight._eye(ana), fight._eye(pharah))
                run.step({ana: (*dart, 8)})
                for _ in range(38):
                    if fight.is_dead(pharah) or fight.is_asleep(pharah):
                        break
                    run.step({ana: (*dart, 0)})

                self.assertEqual(fight.is_dead(pharah), lethal)
                self.assertEqual(fight.is_asleep(pharah), not lethal)
                self.assertIn(shot, fight.rockets)
                self.assertEqual(enemy.mover.position[2], 24)
                for _ in range(80):
                    run.step({})
                    if shot not in fight.rockets:
                        break

                self.assertNotIn(shot, fight.rockets)
                self.assertGreater(enemy.mover.velocity[2], 0)
                self.assertGreater(enemy.mover.velocity[1], 0)
                self.assertEqual(self.health(run, enemy).total, 200)
                self.assertFalse(pharah.client.messages(combat.HIT))
                self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def test_airborne_sleep_stops_hover_thrust_and_preserves_gravity(self):
        run, pharah, ana, _ = self.arena()
        for _ in range(15):
            self.step(run, pharah, ana, buttons=2)
        self.assertGreater(pharah.mover.velocity[1], 0)
        self.dart(run, pharah, ana, buttons=2)
        hover = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x25)
        fuel = hover.vars[87].value()
        for _ in range(30):
            self.step(run, pharah, ana, buttons=2)
        self.assertGreaterEqual(hover.vars[87].value(), fuel)
        self.assertLess(pharah.mover.velocity[1], 0)

    def test_later_real_damage_wakes_and_hero_replacement_drops_old_status(self):
        run, pharah, ana, third = self.arena()
        self.dart(run, pharah, ana)
        fight = run.match.combat
        third.mover.position = [3, pharah.mover.position[1], 0]
        third.mover.body = None
        yaw, pitch = aim(fight._eye(third), fight._eye(pharah))
        run.step({third: (yaw, pitch, 1)})
        self.assertLess(self.health(run, pharah).total, 195)
        self.assertFalse(fight.is_asleep(pharah))

        run, pharah, ana, _ = self.arena()
        self.dart(run, pharah, ana)
        old = pharah.body
        self.assertTrue(run.match.switch_hero(pharah, pharah.hero))
        self.assertNotEqual(pharah.body, old)
        self.assertFalse(run.match.combat.is_asleep(pharah))

    def test_wall_and_friendly_body_reject_sleep(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, pharah, ana, _ = self.arena()
                fight = run.match.combat
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = ana.team
                y = pharah.mover.position[1]
                wall = World([(-4, y, 4, -4, y + 5, 4, 4, y + 5, 4),
                              (-4, y, 4, 4, y + 5, 4, 4, y, 4)])
                collision = fight._collision() if friendly else wall
                with patch.object(fight, "_collision", return_value=collision):
                    self.step(run, pharah, ana, cast=8)
                    for _ in range(70):
                        self.step(run, pharah, ana)
                self.assertEqual(self.health(run, pharah).total, 200)
                self.assertFalse(fight.is_asleep(pharah))
