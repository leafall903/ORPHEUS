"""Actual Call Mech entry using authored MoveTo states."""
import unittest

import test_dva_factory as factory
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER, heroes


class DvaEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        factory.DvaFactoryTests.setUpClass()

    def test_actual_call_mech_runs_both_entry_moves(self):
        helper, run, pilot, _ = factory.DvaFactoryTests().arena(cast=False)
        x, y, z = pilot.mover.position
        instance, _, _ = helper.cast(run, pilot, frames=120)
        states = {state.node.pos: state for state in instance.states.values()}
        for pos in (85, 200):
            self.assertEqual(states[pos].counter, 1)
            self.assertFalse(states[pos].active)
        destination = states[200].evaluate(states[200].node.field("m_865F3D01"), watch=False)
        self.assertGreater(destination.z, z)
        for actual, expected in zip(pilot.mover.position, destination, strict=True):
            self.assertAlmostEqual(actual, expected, delta=.002)
        self.assertEqual(pilot.mover.position[:2], [x, y])

    def test_wall_blocks_first_entry_and_second_does_not_start(self):
        helper, run, pilot, _ = factory.DvaFactoryTests().arena(cast=False)
        instance, _, _ = helper.cast(run, pilot, frames=20)
        x, y, z = pilot.mover.position
        wall_z = z + pilot.mover.data.stand_radius + .08
        run.match.collision_world = World(
            box(x-20, x+20, 0, y, z-20, z+20) + wall(x-2, wall_z, x+2, wall_z)
        )
        for _ in range(120):
            run.step({})
        states = {state.node.pos: state for state in instance.states.values()}
        self.assertEqual(states[85].counter, 1)
        self.assertFalse(states[85].active)
        self.assertNotIn(200, states)
        self.assertGreater(pilot.mover.position[2], z)
        self.assertLess(pilot.mover.position[2], z+.14)


    def test_enemy_rifle_kills_pilot_during_first_entry_without_starting_second(self):
        helper, run, pilot, enemy = factory.DvaFactoryTests().arena(cast=False)
        run.match.switch_hero(enemy, heroes()[SOLDIER])
        enemy.team = 1 - pilot.team
        x, y, z = pilot.mover.position
        enemy.mover.position = [x+3, y, z]
        enemy.mover.body = None
        fight = run.match.combat
        fight.vitals_of(next(t for t in fight._targets() if t.owner is pilot)).now[0] = 10
        instance, _, _ = helper.cast(run, pilot, frames=20)
        for _ in range(120):
            state = next((s for s in instance.states.values() if s.node.pos == 85), None)
            if state is not None and state.move_request() is not None:
                break
            run.step({})
        self.assertIsNotNone(state)
        self.assertIsNotNone(state.move_request())
        self.assertFalse(fight.is_dead(pilot))
        x, y, z = pilot.mover.position
        angles = aim(fight._eye(enemy), (x, y+1, z))
        run.step({enemy: (*angles, FIRE)})
        self.assertTrue(fight.is_dead(pilot))
        for _ in range(45):
            run.step({})
        self.assertFalse(any(s.node.pos == 200 and s.counter for s in instance.states.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
