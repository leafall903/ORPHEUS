"""Actual Blizzard deployment, control and defenses against Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer_dva_matrix as matrix_tests
import test_game_tracer_mei_frost as frost_tests
import test_game_tracer_pulse_deflect as pulse_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, deflection, mei_blizzard, tracer
from ow174.game.collision import World
from ow174.game.content import find_hero, heroes


class TracerMeiBlizzardTests(unittest.TestCase):
    def setUp(self):
        self.case = frost_tests.TracerMeiFrostTests()
        self.case.setUp()
        self.case.aim = aim(self.case.fight._eye(self.case.mei), self.case.tracer.mover.position)
        self.fight = self.case.fight
        self.tracer = self.case.tracer
        self.mei = self.case.mei
        self.health = self.case.health
        self.areas = self.fight.blizzard.areas
        self.script = self.mei.body_script
        self.component = self.script.component
        self.cost = self.component.entity_var(338).value()

    def launch(self):
        self.script.set_ult_charge(self.cost)
        self.case.step(spray=32)
        self.assertEqual(self.component.entity_var(342).value(), 0)
        for _ in range(55):
            if self.fight.rockets:
                return self.fight.rockets[0]
            self.case.step()
        self.fail("Blizzard snowball did not launch")

    def land(self):
        for _ in range(50):
            if self.areas:
                return next(iter(self.areas.values()))
            self.case.step()
        self.fail("Blizzard did not land")

    def test_actual_charge_delay_freeze_damage_and_expiry(self):
        self.assertEqual(self.cost, 1610)
        self.case.step(spray=32)
        for _ in range(45):
            self.case.step()
        self.assertFalse(self.fight.rockets)
        rocket = self.launch()
        self.assertEqual(rocket.definition, mei_blizzard.DEFINITION)
        self.assertEqual(rocket.speed, 20)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        area = self.land()
        self.assertEqual(area.starts - self.fight.tick * 16, 200)
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertEqual(self.health.total, 150)
        self.assertIsNone(self.case.frost())
        for _ in range(185):
            self.case.step()
            if self.fight.is_stunned(self.tracer):
                break
        self.assertTrue(self.fight.is_stunned(self.tracer))
        progress, touched, deadline, *_ = self.case.frost()
        self.assertEqual(progress, 1)
        self.assertEqual(deadline - touched, 1300)
        self.assertEqual(self.component.entity_var(342).value(), 0)
        for _ in range(210):
            self.case.step()
            if not self.areas:
                break
        self.assertFalse(self.areas)
        self.assertAlmostEqual(self.health.total, 65)
        for _ in range(80):
            self.case.step()
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertAlmostEqual(self.health.total, 65)
        self.assertGreater(self.component.entity_var(342).value(), 0)

    def test_two_actual_blinks_escape_before_field_activation(self):
        self.launch()
        self.land()
        self.case.step(buttons=8, yaw=16384)
        for _ in range(10):
            self.case.step(yaw=16384)
        self.case.step(buttons=8, yaw=16384)
        self.assertGreater(self.tracer.mover.position[0] - next(iter(self.areas.values())).position[0], 14)
        for _ in range(80):
            self.case.step()
        self.assertEqual(self.health.total, 150)
        self.assertIsNone(self.case.frost())

    def test_recall_cleans_existing_field_frost_and_rejects_exposure(self):
        self.launch()
        self.land()
        for _ in range(28):
            self.case.step()
        self.assertIsNotNone(self.case.frost())
        health = self.health.total
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertIsNone(self.case.frost())
        for _ in range(20):
            self.case.step()
        self.assertGreaterEqual(self.health.total, health - 0.32)
        self.assertIsNone(self.case.frost())

    def test_wall_and_team_exclude_field_exposure(self):
        for gate in ("wall", "team"):
            with self.subTest(gate=gate):
                self.setUp()
                self.launch()
                area = self.land()
                if gate == "wall":
                    x, y, z = self.tracer.mover.position
                    floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                             (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
                    wall = (z + area.position[2]) / 2
                    triangles = floor + box(x - 4, x + 4, y, y + 4, wall, wall + 0.05)
                    self.case.run.match.collision_world = World(triangles)
                else:
                    match = self.case.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.tracer.team = self.mei.team
                for _ in range(65):
                    self.case.step()
                self.assertEqual(self.health.total, 150)
                self.assertIsNone(self.case.frost())

    def test_caster_replacement_removes_active_area(self):
        self.launch()
        self.land()
        for _ in range(26):
            self.case.step()
        health = self.health.total
        self.assertLess(health, 150)
        self.assertTrue(self.case.run.match.switch_hero(self.mei, heroes()[0x02E000000000006E]))
        self.assertFalse(self.areas)
        for _ in range(25):
            self.case.step()
        self.assertEqual(self.health.total, health)

    def defense(self, hero, defense_button):
        run = self.case.run
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.tracer.team, self.mei.team = 0, 1
        defender, _ = pulse_tests.PulseDeflectTests().support(run, self.tracer, hero=hero)
        if hero == 0x02E000000000007A:
            mech = replace(find_hero("dva"), body=0x0400000000000407)
            self.assertTrue(run.match.switch_hero(defender, mech))
        defender.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        defender.mover.position = [x, y, z + 1.7]
        defender.mover.body = None
        for _ in range(110 if hero == 0x02E000000000007A else 25):
            run.step({})
        facing = (0, 0) if defense_button == 2 else aim(self.fight._eye(defender), (x, y, z + 4))
        for _ in range(6):
            run.step({defender: (*facing, defense_button)})
        self.script.set_ult_charge(self.cost)
        rockets = {}
        for frame in range(115):
            run.step({self.mei: (*self.case.aim, 32 if frame == 0 else 0),
                      defender: (*facing, defense_button)})
            for rocket in self.fight.rockets:
                rockets[id(rocket)] = rocket
        self.assertFalse(self.fight.rockets)
        rocket, = rockets.values()
        self.assertEqual(self.health.total, 150)
        self.assertIsNone(self.case.frost())
        return defender, rocket

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_genji_deflect_transfers_snowball_and_area_to_ally(self):
        defender, rocket = self.defense(0x02E0000000000029, 16)
        area, = self.areas.values()
        self.assertIs(rocket.shooter, defender)
        self.assertEqual(rocket.blizzard_body, defender.body)
        self.assertIs(area.owner, defender)
        self.assertEqual(area.team, self.tracer.team)
        self.assertLess(self.case.case.health(self.mei).total, 250)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_matrix_absorption_prevents_blizzard_area(self):
        matrix_tests.TracerDvaMatrixTests.setUpClass()
        defender, rocket = self.defense(0x02E000000000007A, 2)
        self.assertIs(rocket.shooter, self.mei)
        self.assertFalse(self.areas)
        self.assertEqual(self.case.case.health(defender).total, 600)

    def kill_mei(self):
        health = self.case.case.health(self.mei)
        health.now[combat.HEALTH_POOL] = 6
        x, y, z = self.mei.mover.position
        angle = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        self.case.run.step({self.tracer: (*angle, 1)})
        self.assertTrue(health.dead)

    def test_actual_death_during_windup_prevents_snowball_release(self):
        self.script.set_ult_charge(self.cost)
        self.case.step(spray=32)
        self.assertEqual(self.component.entity_var(342).value(), 0)
        self.kill_mei()
        for _ in range(65):
            self.case.step()
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.areas)
        self.assertEqual(self.health.total, 150)

    def test_blizzard_freeze_cancels_unreleased_pulse_without_refund(self):
        self.launch()
        area = self.land()
        for _ in range(210):
            frost = self.case.frost()
            remaining = area.frost_due.get(self.tracer.body, 0) - self.fight.tick * 16
            if frost is not None and frost[0] >= 0.97 and 32 <= remaining <= 96:
                break
            self.case.step()
        self.assertIsNotNone(frost)
        self.assertGreaterEqual(frost[0], 0.97)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertGreaterEqual(remaining, 32)
        self.assertLessEqual(remaining, 96)

        script = self.tracer.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        pulse = self.fight.pulse_bombs
        self.case.step(buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        for _ in range(12):
            self.assertFalse(pulse.bombs)
            if self.fight.is_stunned(self.tracer):
                break
            self.case.step()
        self.assertTrue(self.fight.is_stunned(self.tracer))
        for _ in range(210):
            if not self.areas and not self.fight.is_stunned(self.tracer):
                break
            self.case.step()
            self.assertFalse(pulse.bombs)
        self.assertFalse(self.areas)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_lethal_pulse_during_blizzard_survives_tracer_freeze_and_caster_death(self):
        for late in (False, True):
            with self.subTest(freeze_during_fuse=late):
                self.setUp()
                self.check_lethal_pulse_during_blizzard(late)

    def check_lethal_pulse_during_blizzard(self, late):
        self.launch()
        area = self.land()
        minimum_frost = 0.84 if late else 0.5
        for _ in range(180):
            frost = self.case.frost()
            if frost is not None and frost[0] >= minimum_frost:
                break
            self.case.step()
        self.assertIsNotNone(frost)
        self.assertGreaterEqual(frost[0], minimum_frost)
        self.assertFalse(self.fight.is_stunned(self.tracer))

        script = self.tracer.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        target = next(t for t in self.fight._targets() if t.owner is self.mei)
        mei_health = self.fight.vitals_of(target)
        before = mei_health.total
        x, y, z = self.mei.mover.position
        facing = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        self.case.run.step({self.tracer: (*facing, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        pulse = self.fight.pulse_bombs
        for _ in range(55):
            if pulse.bombs and pulse.bombs[0].stuck:
                break
            self.case.run.step({self.tracer: (*facing, 0)})
        bomb, = pulse.bombs
        self.assertEqual(bomb.attached, self.mei.body)
        self.assertEqual(mei_health.total, before - 5)
        # Leave Pulse splash range while remaining within the wider Blizzard.
        x = self.tracer.mover.position[0]
        self.case.step(buttons=8, yaw=16384)
        self.assertAlmostEqual(self.tracer.mover.position[0] - x, 7.5, places=2)
        frozen_during_fuse = False
        for _ in range(75):
            if bomb not in pulse.bombs:
                break
            if self.fight.is_stunned(self.tracer):
                frozen_during_fuse = True
                self.assertEqual(bomb.attached, self.mei.body)
                self.assertFalse(mei_health.dead)
            self.case.step()
        self.assertEqual(frozen_during_fuse, late)
        self.assertEqual(self.fight.is_stunned(self.tracer), late)
        self.assertTrue(mei_health.dead)
        self.assertNotIn(bomb, pulse.bombs)
        self.assertIs(self.areas[area.entity], area)
        health_after_kill = self.health.total
        frozen_after_kill = False
        for _ in range(300):
            if not self.areas:
                break
            self.case.step()
            frozen_after_kill |= self.fight.is_stunned(self.tracer)
        self.assertTrue(frozen_after_kill)
        self.assertFalse(self.areas)
        self.assertLess(self.health.total, health_after_kill)
        self.assertAlmostEqual(self.health.total, 65)
        for _ in range(90):
            self.case.step()
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertAlmostEqual(self.health.total, 65)

    def test_released_snowball_lands_after_caster_death(self):
        self.launch()
        self.kill_mei()
        area = self.land()
        self.assertEqual(area.body, self.mei.body)
        for _ in range(45):
            self.case.step()
        self.assertLess(self.health.total, 150)
        self.assertIsNotNone(self.case.frost())
