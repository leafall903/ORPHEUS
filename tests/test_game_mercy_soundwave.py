"""Actual Soundwave contacts and movement in the Mercy lane."""

import unittest

import test_game_mercy_descent as descent_tests
import test_game_mercy_staff as staff_tests
from test_game_combat import aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, lucio_soundwave, stats
from ow174.game.collision import World
from ow174.game.commands import JUMP, SECONDARY
from ow174.game.content import heroes


class MercySoundwaveTests(unittest.TestCase):
    def encounter(self, distance=4):
        f = staff_tests.MercyStaffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.match.switch_hero(f.enemy, heroes()[lucio_soundwave.HERO])
        x, y, z = f.mercy.mover.position
        f.enemy.mover.position[:] = (x, y, z - distance)
        f.enemy.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        return f, f.fight.vitals_of(target)

    def cast(self, f, *, mercy_buttons=0):
        x, y, z = f.mercy.mover.position
        direction = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        f.run.step({f.enemy: (*direction, SECONDARY), f.mercy: (0, 0, mercy_buttons)})

    def airborne_descent(self):
        descent = descent_tests.AngelicDescentTests()
        descent.setUp()
        self.addCleanup(descent.doCleanups)
        f = descent.fixture
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[lucio_soundwave.HERO]))
        descent.place_at_elevated_firing_platform(40)
        descent.step(30, JUMP)
        self.assertTrue(descent.descent().active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)
        return descent

    def test_actual_soundwave_impulse_moves_mercy_without_canceling_descent(self):
        descent = self.airborne_descent()
        f = descent.fixture
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        health = f.fight.vitals_of(target)
        state = descent.descent()
        self.cast(f, mercy_buttons=JUMP)
        self.assertEqual(health.total, 175)
        self.assertTrue(state.active)
        self.assertLess(health.pending_impulse[0], -10)
        self.assertGreater(health.pending_impulse[1], 0)
        before = tuple(f.mercy.mover.position)

        descent.step(1, JUMP)
        self.assertIsNone(health.pending_impulse)
        self.assertLess(f.mercy.mover.position[0], before[0] - 0.1)
        self.assertGreater(f.mercy.mover.position[1], before[1])
        self.assertTrue(state.active)
        for _ in range(85):
            descent.step(1, JUMP)
            self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

    def test_projected_barrier_preserves_descent_and_prevents_soundwave_impulse(self):
        descent = self.airborne_descent()
        f = descent.fixture
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E0000000000068]))
        f.ally.spawn = ((5, 40, 1.5), 0)
        f.ally.mover = f.ally.new_mover()
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        health = f.fight.vitals_of(target)
        x, y, z = f.mercy.mover.position
        direction = aim(f.fight._eye(f.ally), (x, y + 1, z))
        f.run.step({f.ally: (*direction, 16), f.mercy: (0, 0, JUMP)})
        self.assertTrue(f.fight.zarya_barriers.protects(f.mercy))
        self.cast(f, mercy_buttons=JUMP)
        self.assertEqual(f.fight.zarya_barriers.projected[f.ally.body].remaining, 175)
        self.assertEqual(health.total, 200)
        self.assertIsNone(health.pending_impulse)
        descent.step(1, JUMP)
        self.assertEqual(f.mercy.mover.position[0], x)
        self.assertEqual(f.mercy.mover.position[2], z)
        self.assertTrue(descent.descent().active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

    def test_lethal_soundwave_leaves_no_descent_or_pending_impulse_on_respawn(self):
        descent = self.airborne_descent()
        f = descent.fixture
        body = f.mercy.body
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        health = f.fight.vitals_of(target)
        f.fight._damage(f.enemy, target, 180, False, None, f.run.frame)
        self.assertEqual(health.total, 20)
        self.cast(f, mercy_buttons=JUMP)
        self.assertTrue(health.dead)
        self.assertIsNone(health.pending_impulse)
        self.assertEqual(f.enemy.stats.value(stats.ELIMINATIONS), 1)
        position = tuple(f.mercy.mover.position)
        descent.step(10, JUMP)
        self.assertEqual(f.mercy.body, body)
        self.assertEqual(tuple(f.mercy.mover.position), position)

        deadline = next(when for when, player in f.fight.respawns if player is f.mercy)
        while (f.run.frame + 1) * combat.TICK < deadline:
            f.run.frame += 1
            f.match.update(f.run.frame * combat.TICK, f.run.frame)
        descent.step(1)
        self.assertNotEqual(f.mercy.body, body)
        self.assertFalse(descent.descent().active)
        self.assertFalse(any(mod.index == 13 for mod in f.mercy.mover.mods.values()))
        self.assertEqual(f.mercy.mover.velocity[0], 0)

    def test_actual_soundwave_damages_and_moves_on_the_next_victim_pose(self):
        f, health = self.encounter()
        before = tuple(f.mercy.mover.position)
        self.cast(f)
        self.assertEqual(health.total, 175)
        self.assertEqual(tuple(f.mercy.mover.position), before)
        self.assertIsNotNone(health.pending_impulse)
        f.run.step({})
        self.assertIsNone(health.pending_impulse)
        self.assertGreater(f.mercy.mover.position[1], before[1])
        self.assertGreater(f.mercy.mover.position[2], before[2])
        self.assertGreater(f.mercy.mover.velocity[2], 13)
        for _ in range(8):
            f.run.step({})
        self.assertEqual(health.total, 175)

    def test_wall_faction_and_range_reject_damage_and_impulse(self):
        for rejected in ("wall", "ally", "range"):
            with self.subTest(rejected=rejected):
                f, health = self.encounter(distance=12 if rejected == "range" else 4)
                if rejected == "wall":
                    x, _, z = f.mercy.mover.position
                    f.match.collision_world = World(FLOOR + wall(x - 4, z - 2, x + 4, z - 2))
                if rejected == "ally":
                    f.enemy.team = f.mercy.team
                self.cast(f)
                self.assertEqual(health.total, 200)
                self.assertIsNone(health.pending_impulse)

    def test_projected_barrier_on_mercy_blocks_soundwave_damage_and_impulse(self):
        f, health = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E0000000000068]))
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x + 3, y, z)
        f.ally.mover.body = None
        f.run.step({f.ally: (-16384, 0, 16)})
        self.cast(f)
        self.assertEqual(f.fight.zarya_barriers.projected[f.ally.body].remaining, 175)
        self.assertEqual(health.total, 200)
        self.assertIsNone(health.pending_impulse)
        f.run.step({})
        self.assertEqual(tuple(f.mercy.mover.position), (x, y, z))
