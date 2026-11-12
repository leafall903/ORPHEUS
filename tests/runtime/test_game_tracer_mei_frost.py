"""Actual Mei spray, frost and Tracer input recovery."""

import unittest
from dataclasses import replace

import test_game_tracer_body_protection as protection_tests
import test_game_tracer_pulse_deflect as pulse_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes


class TracerMeiFrostTests(unittest.TestCase):
    def setUp(self):
        self.case = protection_tests.TracerBodyProtectionTests()
        self.case.arena(protection_tests.MEI, behind=False, distance=3)
        self.run, self.fight = self.case.run, self.case.fight
        self.tracer, self.mei = self.case.tracer, self.case.enemy
        x, y, z = self.tracer.mover.position
        self.aim = aim(self.fight._eye(self.mei), (x, y + 1, z))
        self.health = self.case.health(self.tracer)

    def step(self, *, spray=0, buttons=0, yaw=0, forward=0):
        self.run.frame += 1
        frame = self.run.frame
        self.run.match.update(frame * 0.016, frame)
        self.mei.take_commands([Command(frame, yaw=self.aim[0], pitch=self.aim[1], buttons=spray)])
        self.tracer.take_commands([Command(frame, yaw=yaw, buttons=buttons, forward=forward)])

    def frost(self):
        return self.fight.frost_hits.get(self.tracer.body)

    def spray(self, frames):
        for _ in range(frames):
            self.step(spray=FIRE)

    def test_actual_freeze_locks_input_then_releases_at_authored_deadline(self):
        for _ in range(115):
            self.step(spray=FIRE)
            if self.fight.is_stunned(self.tracer):
                break
        progress, touched, deadline, yaw, _ = self.frost()
        self.assertEqual(progress, 1)
        self.assertEqual(deadline - touched, 1300)
        self.assertEqual(self.health.total, 67.5)
        position = tuple(self.tracer.mover.position)
        script = self.tracer.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
        while (self.fight.tick + 1) * 16 < deadline:
            self.step(buttons=FIRE | 8 | 16 | 32 | 0x800, forward=127, yaw=16384)
            self.assertEqual(tuple(self.tracer.mover.position), position)
            self.assertEqual(self.tracer.mover.yaw, yaw)
            self.assertFalse(script.component.held)
            self.assertEqual(blink.vars[11].value(), 3)
            self.assertEqual(script.component.entity_var(342).value(), cost)
        self.step()
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertIsNone(self.frost())
        self.assertEqual(script.component.entity_var(2427).value(), 0)
        self.step(buttons=8, yaw=16384)
        self.assertEqual(blink.vars[11].value(), 2)
        self.assertGreater(self.tracer.mover.position[0] - position[0], 6)

    def test_partial_frost_slows_and_expires_without_further_contact(self):
        self.spray(48)
        progress, _, deadline, _, _ = self.frost()
        self.assertGreater(progress, 0.4)
        self.assertEqual(deadline, 0)
        mover = self.tracer.mover
        base = mover.data.run_forward
        self.step(forward=127)
        self.assertAlmostEqual(mover.values.run_forward, base * (1 - progress), places=5)
        for _ in range(70):
            self.step()
        self.assertIsNone(self.frost())
        self.assertEqual(mover.values.run_forward, base)

    def test_recall_clears_partial_frost_and_rejects_new_contact(self):
        self.spray(30)
        self.assertIsNotNone(self.frost())
        before = self.health.total
        self.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertIsNone(self.frost())
        self.spray(24)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertIsNone(self.frost())
        self.assertGreaterEqual(self.health.total, before)

    def test_blink_leaves_spray_range_before_freeze(self):
        self.spray(30)
        before, health = self.frost(), self.health.total
        self.step(buttons=8, yaw=16384)
        self.spray(40)
        self.assertEqual(self.frost(), before)
        self.assertEqual(self.health.total, health)
        self.assertFalse(self.fight.is_stunned(self.tracer))

    def test_static_cover_and_allies_reject_damage_and_frost(self):
        for gate in ("wall", "team"):
            with self.subTest(gate=gate):
                self.setUp()
                if gate == "wall":
                    x, y, z = self.tracer.mover.position
                    floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                             (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
                    wall = box(x - 4, x + 4, y, y + 4, z + 1, z + 1.2)
                    self.run.match.collision_world = World(floor + wall)
                else:
                    self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
                    self.tracer.team = self.mei.team
                self.spray(60)
                self.assertEqual(self.health.total, 150)
                self.assertIsNone(self.frost())

    def test_freeze_interrupts_an_already_charged_pulse_windup(self):
        self.spray(92)
        script = self.tracer.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.step(spray=FIRE, buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(self.fight.pulse_bombs.bombs)
        for _ in range(10):
            if self.fight.is_stunned(self.tracer):
                break
            self.step(spray=FIRE)
        self.assertTrue(self.fight.is_stunned(self.tracer))
        for _ in range(95):
            self.step()
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertLess(script.component.entity_var(342).value(), cost)

    def test_switching_body_clears_frozen_status_and_allows_blink(self):
        for _ in range(115):
            self.step(spray=FIRE)
            if self.fight.is_stunned(self.tracer):
                break
        self.assertTrue(self.fight.is_stunned(self.tracer))
        old = self.tracer.body
        self.assertTrue(self.run.match.switch_hero(self.tracer, heroes()[self.tracer.hero.guid]))
        self.assertNotEqual(old, self.tracer.body)
        self.assertNotIn(old, self.fight.frost_hits)
        self.assertNotIn(old, self.fight.frost_sources)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.step()
        start = self.tracer.mover.position[0]
        self.step(buttons=8, yaw=16384)
        self.assertGreater(self.tracer.mover.position[0] - start, 6)

    def test_actual_spray_pierces_an_enemy_in_front_of_tracer(self):
        front, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer)
        x, y, z = self.tracer.mover.position
        front.mover.position = [x, y, z + 1.5]
        front.mover.body = None
        before = self.case.health(front)
        for _ in range(15):
            self.step()
        self.spray(35)
        for _ in range(15):
            self.step()
        self.assertLess(before.total, 200)
        self.assertAlmostEqual(200 - before.total, 150 - self.health.total)
        self.assertEqual(self.fight.frost_hits[front.body][0], self.frost()[0])

    def test_actual_winston_bubble_blocks_spray_and_frost_for_tracer(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.mei.team = 0, 1
        winston, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        winston.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        winston.mover.position = [x, y, z - 3.5]
        winston.mover.body = None
        for _ in range(25):
            self.run.step({})
        self.run.step({winston: (0, 0, 16)})
        for _ in range(3):
            self.run.step({})
        bubble = self.fight.winston_barriers.actors[winston.body]
        self.assertEqual(bubble.health, 700)
        self.spray(40)
        self.assertLess(bubble.health, 700)
        self.assertEqual(self.health.total, 150)
        self.assertIsNone(self.frost())
