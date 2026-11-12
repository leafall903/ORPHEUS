"""Soldier movement/attack transitions through real Player command handling."""

import math
import unittest

import test_soldier_combat_lane as fixture
from test_game_combat import aim

from ow174.game.collision import flat_world
from ow174.game.commands import Command


class SoldierSkillInterruptTests(unittest.TestCase):
    input = fixture.SoldierCombatLaneTests.input
    vitals = fixture.SoldierCombatLaneTests.vitals

    def arena(self):
        run, soldier, other = fixture.SoldierCombatLaneTests().arena()
        run.match.collision_world = flat_world(0.0)
        soldier.mover.position = list(soldier.mover.position)
        other.mover.position = [0.0, 0.0, 15.0]
        return run, soldier, other

    def move(self, run, soldier, buttons=0, forward=127, target=None):
        frame = soldier.body_script.frame + 1
        yaw, pitch = (0, 0) if target is None else aim(run.match.combat._eye(soldier), target)
        run.match.update(frame * 0.016, frame, frames=False)
        soldier.take_commands([Command(frame, yaw=yaw, pitch=pitch, forward=forward, buttons=buttons)])
        return math.hypot(soldier.mover.velocity[0], soldier.mover.velocity[2])

    def sprint(self, run, soldier):
        for _ in range(30):
            self.move(run, soldier, forward=0)
        for _ in range(30):
            speed = self.move(run, soldier)
        self.assertAlmostEqual(speed, 5.5, delta=0.01)
        self.move(run, soldier, buttons=8)
        for _ in range(10):
            speed = self.move(run, soldier)
        self.assertAlmostEqual(speed, 8.25, delta=0.01)

    def test_sprint_firing_returns_to_running_speed_and_deals_damage(self):
        run, soldier, other = self.arena()
        self.sprint(run, soldier)
        pool = self.vitals(run, other)
        for _ in range(15):
            speed = self.move(run, soldier, buttons=1, target=(0.0, 0.8, 15.0))
        self.assertAlmostEqual(speed, 5.5, delta=0.01)
        self.assertEqual(pool.total, 200)  # the authored sprint-out delay still blocks the first rounds
        for _ in range(15):
            speed = self.move(run, soldier, buttons=1, target=(0.0, 0.8, 15.0))
        self.assertLess(pool.total, 200)
        self.assertLess(soldier.body_script.component.instances[11].vars[53].value(), 30)

    def test_death_during_sprint_rejects_further_movement_and_shooting(self):
        run, soldier, other = self.arena()
        self.sprint(run, soldier)
        fight = run.match.combat
        victim = next(target for target in fight._targets() if target.owner is soldier)
        fight._damage(other, victim, 200, False, None, fight.tick)
        self.assertTrue(fight.is_dead(soldier))
        position = tuple(soldier.mover.position)
        pool = self.vitals(run, other)
        for _ in range(30):
            self.move(run, soldier, buttons=1 | 8, target=(0.0, 0.8, 15.0))
        self.assertEqual(tuple(soldier.mover.position), position)
        self.assertEqual(pool.total, 200)

    def test_hero_replacement_during_field_windup_cannot_throw_from_retired_body(self):
        run, soldier, other = self.arena()
        other.team = soldier.team
        other.mover.position = soldier.mover.position
        pool = self.vitals(run, other)
        pool.damage(100)
        self.input(run, soldier, 0x10)
        old_body, old_script = soldier.body, soldier.body_script
        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
        self.assertNotEqual(soldier.body, old_body)
        self.assertTrue(old_script.retired)
        for _ in range(120):
            self.input(run, soldier)
        self.assertEqual(pool.total, 100)
        self.assertFalse(run.match.combat.projectiles.shots)


if __name__ == "__main__":
    unittest.main()
