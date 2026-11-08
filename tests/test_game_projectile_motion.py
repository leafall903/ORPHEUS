"""Server ballistic reconstruction, including an actual Torbjorn rivet volley."""

import unittest

import test_game_torbjorn as torbjorn

from ow174.game import combat
from ow174.game.commands import Command


class ProjectileMotionTests(unittest.TestCase):
    def flight(self, advances):
        run, shooter, _ = torbjorn.TorbjornProjectileTests().arena(100.0)
        fight = run.match.combat
        x, y, z = shooter.mover.position
        rocket = combat.Rocket(shooter, shooter.team, (x, y + 10.0, z),
                               (0.0, 0.0, 1.0), 10.0, 2.0, None, None,
                               last_tick=run.frame, gravity=9.8)
        fight.rockets.append(rocket)
        for ticks in advances:
            fight.tick += ticks
            fight._fly(rocket)
        return rocket, (x, y, z), fight

    def test_one_second_gravity_displacement(self):
        rocket, origin, fight = self.flight((62,))
        self.assertIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.position[2] - origin[2], 9.92)
        self.assertAlmostEqual(rocket.position[1] - origin[1], 5.1780864)
        self.assertAlmostEqual(rocket.flown, 0.992)
        self.assertGreater(rocket.distance, 9.92)

    def test_gap_and_continuous_flight_agree(self):
        continuous, _, _ = self.flight((1,) * 62)
        gap, _, _ = self.flight((62,))
        for a, b in zip(continuous.position, gap.position, strict=True):
            self.assertAlmostEqual(a, b)
        self.assertAlmostEqual(continuous.distance, gap.distance)
        self.assertAlmostEqual(continuous.left, gap.left)

    def test_authored_torbjorn_rivet_drops_and_hits_ground(self):
        run, shooter, victim = torbjorn.TorbjornProjectileTests().arena(100.0)
        fight = run.match.combat
        state = next(i for i in shooter.body_script.component.instances.values()
                     if i.graph.index == 0x1683).states[10]
        origin = fight._eye(shooter)
        fight._shoot(shooter, Command(run.frame), state, 0, run.frame * 16,
                     activation=(1, run.frame * 16))
        rocket, = fight.rockets
        self.assertAlmostEqual(rocket.gravity, 9.8, places=6)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(rocket.position[1], origin[1] - 0.50176)
        self.assertAlmostEqual(rocket.position[2] - origin[2], 22.4)
        for _ in range(40):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertLess(rocket.flown, 0.6)
        target = next(t for t in fight._targets() if t.owner is victim)
        self.assertEqual(fight.vitals_of(target).total, 200.0)

    def test_identical_projectiles_keep_independent_lifetimes(self):
        rocket, _, fight = self.flight((1,))
        other = combat.Rocket(rocket.shooter, rocket.team, rocket.position,
                              rocket.direction, rocket.speed, rocket.left,
                              rocket.direct, rocket.blast, flown=rocket.flown,
                              last_tick=rocket.last_tick, gravity=rocket.gravity,
                              distance=rocket.distance)
        fight.rockets.append(other)
        fight._landed(other)
        self.assertIn(rocket, fight.rockets)
        self.assertNotIn(other, fight.rockets)

    def test_lifetime_clips_the_last_ballistic_step(self):
        rocket, _, fight = self.flight((1,))
        rocket.left = 0.005
        old = rocket.flown
        fight.tick += 10
        fight._fly(rocket)
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.flown - old, 0.005)
