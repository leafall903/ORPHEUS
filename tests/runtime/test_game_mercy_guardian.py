"""Guardian Angel through real ally selection, input and collision movement.

Pose/eye/capsule/angle and corpse target bindings are reconstructed host rules;
these headless controls do not establish native targeting or client presentation.
"""

import unittest
from dataclasses import replace

import test_game_mercy_staff as staff_tests
from test_game_combat import aim
from test_game_mover import FLOOR, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command

SHIFT = 0x08


class GuardianAngelTests(unittest.TestCase):
    def setUp(self):
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.run, self.match, self.fight = fixture.run, fixture.match, fixture.fight
        self.mercy, self.ally, self.enemy = fixture.mercy, fixture.ally, fixture.enemy
        self.ally.spawn = ((0.0, 1.0, 12.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        self.instance = next(i for i in self.mercy.body_script.component.instances.values()
                             if i.graph.index == 0x006E)

    def step(self, count, buttons=0, target=None):
        target = target or self.ally
        for _ in range(count):
            x, y, z = target.mover.position
            yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1.0, z))
            self.run.step({self.mercy: (yaw, pitch, buttons)})

    def cooldown(self):
        return self.instance.find_var(0, 61).value()

    def finish(self):
        self.step(1, SHIFT)
        self.assertTrue(self.instance.state(0).active)
        for _ in range(60):
            self.step(1, SHIFT)
            if not self.instance.state(0).active:
                break
        self.assertFalse(self.instance.state(0).active)

    def test_actual_shift_flies_at_authored_speed_and_enters_one_point_five_second_cooldown(self):
        self.step(1, SHIFT)
        self.assertTrue(self.instance.state(0).active)
        self.assertEqual(self.instance.state(0).evaluate(self.instance.state(0).node.field("m_speed")), 17)
        self.assertEqual(self.cooldown(), 0)
        destination, _ = self.instance.state(0).move_request()
        self.step(4, SHIFT)
        self.assertGreater(self.mercy.mover.position[2], 0.5)
        self.step(45, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertAlmostEqual(self.mercy.mover.position[2], destination.z, delta=0.01)
        self.assertGreater(self.cooldown(), 1.0)
        self.assertLessEqual(self.cooldown(), 1.5)

    def test_cooldown_rejects_repeated_presses_then_allows_a_new_flight(self):
        self.finish()
        self.step(1)
        position = self.mercy.mover.position[2]
        self.ally.spawn = ((0.0, 1.0, 22.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        for _ in range(15):
            self.step(1, SHIFT)
            self.step(1)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.mercy.mover.position[2], position)
        self.assertGreater(self.cooldown(), 0)
        self.step(110)
        self.assertEqual(self.cooldown(), 0)
        self.step(5, SHIFT)
        self.assertGreater(self.mercy.mover.position[2], position + 0.5)

    def test_release_cancels_flight_and_starts_cooldown(self):
        self.step(4, SHIFT)
        self.assertTrue(self.instance.state(0).active)
        self.step(1)
        position = self.mercy.mover.position[2]
        self.assertFalse(self.instance.state(0).active)
        self.assertGreater(self.cooldown(), 1.4)
        self.step(10)
        self.assertEqual(self.mercy.mover.position[2], position)

    def test_hostile_target_miss_does_not_fly_or_consume_cooldown(self):
        self.step(20, SHIFT, self.enemy)
        self.assertEqual(self.mercy.mover.position[2], 0)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.cooldown(), 0)

    def test_out_of_range_friendly_target_does_not_start(self):
        self.ally.spawn = ((0.0, 1.0, 50.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        self.step(20, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.mercy.mover.position[2], 0)
        self.assertEqual(self.cooldown(), 0)

    def test_occluded_friendly_target_does_not_start(self):
        self.match.collision_world = World(FLOOR + wall(-5, 2.5, 5, 2.5))
        self.step(20, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.mercy.mover.position[2], 0)

    def test_collision_inserted_during_flight_aborts_and_starts_cooldown(self):
        self.step(4, SHIFT)
        self.match.collision_world = World(FLOOR + wall(-5, 2.5, 5, 2.5))
        self.step(30, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertLess(self.mercy.mover.position[2], 2.5)
        self.assertGreater(self.cooldown(), 0)

    def test_friendly_player_corpse_is_a_target_without_resurrecting_it(self):
        target = next(t for t in self.fight._targets() if t.owner is self.ally)
        self.fight._damage(self.enemy, target, 500, False, None, self.run.frame)
        self.step(8, SHIFT)
        self.assertGreater(self.mercy.mover.position[2], 0.5)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertEqual(self.fight.vitals_of(target).total, 0)

    def test_caster_death_interrupts_flight(self):
        self.step(4, SHIFT)
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.fight._damage(self.enemy, target, 500, False, None, self.run.frame)
        position = self.mercy.mover.position[2]
        self.step(10, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.mercy.mover.position[2], position)
        self.assertGreater(self.cooldown(), 0)

    def test_duplicate_input_does_not_advance_movement_or_cooldown(self):
        self.step(5, SHIFT)
        before = tuple(self.mercy.mover.position), self.cooldown()
        self.mercy.take_commands([Command(self.run.frame, buttons=SHIFT)])
        self.assertEqual((tuple(self.mercy.mover.position), self.cooldown()), before)

    def test_weapon_switch_cannot_reset_a_running_cooldown(self):
        self.finish()
        self.step(1)
        position = self.mercy.mover.position[2]
        self.ally.spawn = ((0.0, 1.0, 22.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame, action=2)])
        self.step(10, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.mercy.mover.position[2], position)
        self.assertGreater(self.cooldown(), 0)

    def test_free_for_all_has_no_other_friendly_targets(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=True)
        self.step(20, SHIFT)
        self.assertFalse(self.instance.state(0).active)
        self.assertEqual(self.cooldown(), 0)
