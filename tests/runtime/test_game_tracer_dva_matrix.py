"""Actual Tracer attacks and selected D.Va 0407 Defense Matrix commands."""

import unittest

import test_game_tracer_dva_mech as mech_tests
from test_game_combat import aim

from ow174.game import combat, deflection
from ow174.game.commands import FIRE, Command
from ow174.game.script.driver import ULT_COST


class TracerDvaMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.TracerDvaMechTests.setUpClass()
        if deflection.TAGS_PATH is None:
            raise unittest.SkipTest("requires explicit build104319 projectile target tags")

    def setUp(self):
        self.fixture = mech_tests.TracerDvaMechTests()
        self.run, self.mech, self.tracer = self.fixture.scenario()
        self.matrix = self.matrix_for(self.mech)
        self.vitals = self.fixture.vitals(self.run, self.mech)

    def step(self, count, buttons=0, *, yaw=0):
        for _ in range(count):
            self.run.step({self.mech: (yaw, 0, buttons)})

    def shoot(self, matrix_buttons=0, *, matrix_yaw=0):
        x, y, z = self.mech.mover.position
        yaw, pitch = aim(self.run.match.combat._eye(self.tracer), (x, y + 1.0, z))
        self.run.step({self.mech: (matrix_yaw, 0, matrix_buttons),
                       self.tracer: (yaw, pitch, FIRE)})

    def test_actual_matrix_absorbs_both_pistol_bullets_until_released(self):
        self.assertEqual(self.matrix.vars[87].value(), 100)
        self.step(60, 2)
        self.assertTrue(self.matrix.state(3).active)
        self.assertAlmostEqual(self.matrix.vars[87].value(), 52, delta=0.1)
        self.shoot(2)
        self.assertEqual(self.vitals.total, 600)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
        self.step(1)
        self.assertFalse(self.matrix.state(3).active)
        self.step(5, 2)
        self.assertFalse(self.matrix.state(3).active)
        self.step(16)
        self.shoot()
        self.assertEqual(self.vitals.total, 594)
        self.step(490)
        self.assertEqual(self.matrix.vars[87].value(), 100)
        self.step(5, 2)
        self.assertTrue(self.matrix.state(3).active)
        self.shoot(2)
        self.assertEqual(self.vitals.total, 594)

    def test_depletion_ends_matrix_and_rejects_low_resource_reuse(self):
        self.step(150, 2)
        self.assertFalse(self.matrix.state(3).active)
        self.assertEqual(self.matrix.vars[87].value(), 0)
        self.step(41)
        self.assertEqual(self.matrix.vars[61].value(), 0)
        self.assertLess(self.matrix.vars[87].value(), 10)
        self.step(1, 2)
        self.assertFalse(self.matrix.state(3).active)
        self.step(80)
        self.assertGreater(self.matrix.vars[87].value(), 10)
        self.step(1, 2)
        self.assertTrue(self.matrix.state(3).active)

    def test_matrix_facing_away_does_not_absorb_actual_pistol_fire(self):
        self.step(5, 2, yaw=-32768)
        self.assertTrue(self.matrix.state(3).active)
        self.shoot(2, matrix_yaw=-32768)
        self.assertEqual(self.vitals.total, 594)

    def test_late_matrix_activation_cannot_absorb_an_already_attached_pulse(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                run, mech, tracer = self.fixture.scenario(distance=4)
                fight = run.match.combat
                script = tracer.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({tracer: (-32768, 0, 32), mech: (0, 0, 0)})
                for _ in range(20):
                    if fight.pulse_bombs.bombs:
                        break
                    run.step({tracer: (-32768, 0, 0)})
                matrix = self.matrix_for(mech)
                bomb, = fight.pulse_bombs.bombs
                for _ in range(3):
                    run.step({})
                self.assertFalse(bomb.stuck)
                commands = [Command(run.frame + offset, buttons=2 if offset >= 22 else 0)
                            for offset in range(1, 31)]
                if batched:
                    mech.take_commands(commands)
                    run.frame += len(commands)
                    poses = fight._impact_gap_poses(run.frame, fight._targets())
                    self.assertIn((mech.body, run.frame - 1), poses)
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for command in commands:
                        if command.buttons and not matrix.state(3).active:
                            self.assertEqual(bomb.attached, mech.body)
                        run.step({mech: (0, 0, command.buttons)})
                self.assertTrue(matrix.state(3).active)
                self.assertTrue(any(candidate is bomb for candidate in fight.pulse_bombs.bombs))
                self.assertEqual(bomb.attached, mech.body)
                self.assertLess(self.fixture.vitals(run, mech).total, 600)

    def test_matrix_turn_after_interception_preserves_pulse_absorption(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                run, mech, tracer = self.fixture.scenario(distance=4)
                fight = run.match.combat
                script = tracer.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({tracer: (-32768, 0, 32)})
                for _ in range(20):
                    if fight.pulse_bombs.bombs:
                        break
                    run.step({tracer: (-32768, 0, 0)})
                bomb, = fight.pulse_bombs.bombs
                for _ in range(3):
                    run.step({})
                self.assertFalse(bomb.stuck)
                commands = [Command(run.frame + offset, yaw=0 if offset < 22 else -32768, buttons=2)
                            for offset in range(1, 31)]
                if batched:
                    mech.take_commands(commands)
                    run.frame += len(commands)
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for command in commands:
                        run.step({mech: (command.yaw, 0, command.buttons)})
                self.assertTrue(self.matrix_for(mech).state(3).active)
                self.assertFalse(fight.pulse_bombs.bombs)
                self.assertFalse(bomb.stuck)
                self.assertNotIn(bomb.shot, fight.projectiles.shots)
                self.assertEqual(self.fixture.vitals(run, mech).total, 600)

    @staticmethod
    def matrix_for(mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)

    def test_matrix_intercepts_actual_pulse_before_contact_and_fuse(self):
        self.step(5, 2)
        self.assertTrue(self.matrix.state(3).active)
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (-32768, 0, 32), self.mech: (0, 0, 2)})
        manager = self.run.match.combat.pulse_bombs
        for _ in range(20):
            if manager.bombs:
                break
            self.step(1, 2)
        bomb, = manager.bombs
        self.assertTrue(deflection.absorbable(bomb.definition))
        projectiles = self.run.match.combat.projectiles
        self.assertIn(bomb.shot, projectiles.shots)
        self.step(20, 2)
        self.assertFalse(manager.bombs)
        self.assertNotIn(bomb.shot, projectiles.shots)
        self.assertFalse(bomb.stuck)
        self.assertIsNone(bomb.attached)
        self.step(90)
        self.assertEqual(self.vitals.total, 600)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
