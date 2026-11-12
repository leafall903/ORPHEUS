"""Actual command-driven ability physics; impact effects/replication remain separate."""

import math
import unittest

import test_orisa_combat as orisa_fixture

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import find_hero


class OrisaFlightTests(unittest.TestCase):
    def cast(self, button, *, pitch=0):
        run, player, enemy = orisa_fixture.OrisaCombatTests().arena()
        for index in range(40):
            run.step({player: (0, pitch, button if index == 0 else 0)})
            if run.match.combat.rockets:
                break
        self.assertEqual(len(run.match.combat.rockets), 1)
        return run, player, enemy, run.match.combat.rockets[0]

    def test_halt_command_creates_server_flight_without_owner_prediction_lease(self):
        run, player, _, rocket = self.cast(2)
        fight = run.match.combat
        self.assertEqual(rocket.source_graph, 0xCCB)
        self.assertIsNone(fight.projectiles.lease_id(player, rocket.definition, run.frame))
        self.assertTrue(rocket.shot.authoritative)
        origin = rocket.position
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(rocket.position[0], origin[0])
        self.assertAlmostEqual(rocket.position[1], origin[1])
        self.assertAlmostEqual(rocket.position[2] - origin[2], 25 * 20 * combat.TICK)

    def test_halt_passes_enemy_body_without_damaging_or_impacting_it(self):
        run, _, enemy, rocket = self.cast(2)
        fight = run.match.combat
        for _ in range(20):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertGreater(rocket.position[2], target.feet[2])
        self.assertIn(rocket, fight.rockets)
        self.assertFalse(fight.ability_impacts)

    def test_halt_hits_wall_at_the_flight_step_endpoint(self):
        run, player, _, rocket = self.cast(2)
        fight = run.match.combat
        orisa_fixture.OrisaCombatTests().wall(run, player, 2.0)
        for _ in range(5):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(rocket.ended, "world")
        self.assertAlmostEqual(rocket.travelled, 2.0)
        self.assertEqual(list(fight.ability_impacts), [rocket])
        self.assertIsNotNone(fight.orisa.orbs[rocket.entity].requested)

    def test_barrier_arc_lands_at_world_floor_with_real_spawn_definition(self):
        run, player, enemy, rocket = self.cast(16)
        fight = run.match.combat
        origin = rocket.position
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertIsNone(fight.projectiles.lease_id(player, rocket.definition, run.frame))
        for _ in range(80):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(rocket.ended, "world")
        self.assertEqual(rocket.spawn, 0x04000000000014A1)
        self.assertAlmostEqual(rocket.position[1], player.mover.position[1], places=6)
        self.assertGreater(rocket.position[2], origin[2])
        self.assertIn(rocket, fight.ability_impacts)
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.assertEqual(fight.vitals_of(target).total, 200)

    def test_barrier_ballistic_position_matches_acceleration_before_impact(self):
        run, _, _, rocket = self.cast(16, pitch=-8192)
        run.step({})  # admit at an explicit world tick before measuring catch-up
        position = rocket.position
        velocity = tuple(axis * rocket.speed for axis in rocket.direction)
        for _ in range(10):
            run.step({})
        seconds = 10 * combat.TICK
        self.assertAlmostEqual(rocket.position[1],
                               position[1] + velocity[1] * seconds - 0.5 * 9.8 * seconds ** 2, places=6)
        self.assertAlmostEqual(rocket.position[2], position[2] + velocity[2] * seconds, places=6)

    def test_gravity_sweep_after_world_gap_matches_contiguous_ticks(self):
        results = []
        for advances in (range(1, 21), (20,)):
            run, _, _, rocket = self.cast(16, pitch=-8192)
            run.step({})
            start = run.frame
            for offset in advances:
                at = start + offset
                run.match.update(at * combat.TICK, at)
            results.append((rocket.position, rocket.speed, rocket.travelled))
        for a, b in zip(results[0][0], results[1][0], strict=True):
            self.assertAlmostEqual(a, b, places=6)
        self.assertAlmostEqual(results[0][1], results[1][1], places=6)
        self.assertAlmostEqual(results[0][2], results[1][2], places=6)

    def test_repeated_command_does_not_duplicate_nonpredicted_flight(self):
        run, player, _, rocket = self.cast(2)
        for _ in range(5):
            player.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=2, extra=0)])
        self.assertEqual(run.match.combat.rockets, [rocket])

    def test_hero_switch_retires_nonpredicted_ability_flight(self):
        run, player, _, rocket = self.cast(2)
        self.assertEqual(rocket.source_body, player.body)
        run.match.switch_hero(player, find_hero("Soldier"))
        self.assertNotIn(rocket, run.match.combat.rockets)
        for _ in range(20):
            run.step({})
        self.assertFalse(run.match.combat.ability_impacts)

    def test_vertical_ballistic_apex_does_not_divide_by_zero(self):
        run, _, _, rocket = self.cast(16, pitch=-16384)
        rocket.direction = (0, 1, 0)
        rocket.speed = rocket.gravity * combat.TICK
        run.step({})
        self.assertTrue(all(math.isfinite(value) for value in rocket.position))
        run.step({})
        self.assertTrue(all(math.isfinite(value) for value in rocket.direction))


if __name__ == "__main__":
    unittest.main()
