"""Angelic Descent through actual owner input and collision-backed falling.

These prove the existing authored movement modifier's headless effect. Native
status/filter fidelity and original-client animation remain outside this scope.
"""

import unittest

import test_game_mercy_staff as staff_tests
from test_game_mover import FLOOR, ramp

from ow174.game.collision import World
from ow174.game.commands import Command

JUMP = 0x40


class AngelicDescentTests(unittest.TestCase):
    def setUp(self):
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.mercy = fixture.mercy
        self.mercy.spawn = ((0.0, 10.0, 0.0), 0.0)
        self.mercy.mover = self.mercy.new_mover()
        self.step(1)

    def step(self, count, buttons=0):
        self.fixture.step(count, buttons=buttons)

    def descent(self):
        instance = next(i for i in self.mercy.body_script.component.instances.values()
                        if i.graph.index == 0x0071)
        return instance.state(1)

    def place_at_elevated_firing_platform(self, height):
        fixture = self.fixture
        fixture.match.collision_world = World(
            FLOOR + ramp(-2, 2, height, 0, x0=3, x1=6))
        self.mercy.spawn = ((0, height, 0), 0.0)
        self.mercy.mover = self.mercy.new_mover()
        fixture.enemy.spawn = ((4, height, 0), 0.0)
        fixture.enemy.mover = fixture.enemy.new_mover()

    def test_hold_jump_uses_authored_two_meter_per_second_terminal_modifier(self):
        self.step(30, JUMP)
        self.assertTrue(self.descent().active)
        terminal = [m for m in self.mercy.mover.mods.values() if m.index == 13]
        self.assertEqual([(m.op, m.value) for m in terminal], [(2, 2.0)])
        self.assertAlmostEqual(self.mercy.mover.velocity[1], -2.0, delta=0.01)
        self.assertGreater(self.mercy.mover.position[1], 9.0)

    def test_no_jump_falls_faster_without_terminal_modifier(self):
        self.step(30)
        self.assertFalse(self.descent().active)
        self.assertLess(self.mercy.mover.velocity[1], -8.0)
        self.assertLess(self.mercy.mover.position[1], 8.0)
        self.assertFalse(any(m.index == 13 for m in self.mercy.mover.mods.values()))

    def test_release_restores_gravity_and_repress_slows_existing_fall(self):
        self.step(30, JUMP)
        self.step(20)
        self.assertFalse(self.descent().active)
        self.assertLess(self.mercy.mover.velocity[1], -7.0)
        self.step(30, JUMP)
        self.assertTrue(self.descent().active)
        self.assertAlmostEqual(self.mercy.mover.velocity[1], -2.0, delta=0.01)

    def test_held_descent_lands_on_collision_floor_without_floating(self):
        self.step(350, JUMP)
        self.assertEqual(self.mercy.mover.position[1], 1.0)
        self.assertEqual(self.mercy.mover.velocity[1], 0)
        self.step(20, JUMP)
        self.assertEqual(self.mercy.mover.position[1], 1.0)

    def test_duplicate_command_does_not_move_or_consume_extra_descent_time(self):
        self.step(30, JUMP)
        position = tuple(self.mercy.mover.position)
        velocity = self.mercy.mover.velocity
        self.mercy.take_commands([Command(self.fixture.run.frame, buttons=JUMP)])
        self.assertEqual(tuple(self.mercy.mover.position), position)
        self.assertEqual(self.mercy.mover.velocity, velocity)

    def test_death_freezes_corpse_and_new_body_does_not_inherit_descent(self):
        self.step(30, JUMP)
        target = next(t for t in self.fixture.fight._targets() if t.owner is self.mercy)
        self.fixture.fight._damage(self.fixture.enemy, target, 500, False, None,
                                   self.fixture.run.frame)
        position = tuple(self.mercy.mover.position)
        old_body = self.mercy.body
        self.step(10, JUMP)
        self.assertTrue(self.fixture.fight.is_dead(self.mercy))
        self.assertEqual(tuple(self.mercy.mover.position), position)
        self.fixture.match.switch_hero(self.mercy, self.mercy.hero)
        self.step(1)
        self.assertNotEqual(self.mercy.body, old_body)
        self.assertFalse(self.descent().active)
        self.assertFalse(any(m.index == 13 for m in self.mercy.mover.mods.values()))


if __name__ == "__main__":
    unittest.main()
