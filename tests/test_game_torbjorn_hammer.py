"""Actual command-driven hammer damage under the documented timing reconstruction."""

import unittest
from dataclasses import replace

import test_game_torbjorn as torbjorn

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command


class TorbjornHammerTests(unittest.TestCase):
    def arena(self, distance=1.5):
        run, shooter, victim = torbjorn.TorbjornProjectileTests().arena(distance)
        run.frame += 1
        shooter.take_commands([Command(run.frame, action=2)])
        for _ in range(45):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        return run, shooter, victim, fight.vitals_of(target)

    def swing(self, run, shooter, ticks=45, yaw=0):
        for _ in range(ticks):
            run.step({shooter: (yaw, 0, 1)})

    def test_hammer_delays_damage_and_hits_once_per_swing(self):
        run, shooter, _, vitals = self.arena()
        self.swing(run, shooter, 19)
        self.assertEqual(vitals.total, 200.0)
        self.swing(run, shooter, 1)
        self.assertEqual(vitals.total, 145.0)
        self.swing(run, shooter, 25)
        self.assertEqual(vitals.total, 145.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 55)

    def test_hammer_repeat_rate_does_not_duplicate_an_activation(self):
        run, shooter, _, vitals = self.arena()
        self.swing(run, shooter, 100)
        self.assertEqual(vitals.total, 90.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[1]["+0x84"] - hits[0]["+0x84"], 47)

    def test_hammer_miss_range_and_friendly_filter(self):
        for distance, yaw, friendly in ((5.0, 0, False), (1.5, -32768, False), (1.5, 0, True)):
            with self.subTest(distance=distance, yaw=yaw, friendly=friendly):
                run, shooter, victim, vitals = self.arena(distance)
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = shooter.team
                self.swing(run, shooter, yaw=yaw)
                self.assertEqual(vitals.total, 200.0)
                self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_hammer_is_occluded_by_real_collision_triangles(self):
        run, shooter, _, vitals = self.arena()
        x, y, z = shooter.mover.position
        wall = z + 0.75
        run.match.collision_world = World([
            (x - 3, y, wall, x + 3, y, wall, x + 3, y + 4, wall),
            (x - 3, y, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
        ], name="hammer-wall")
        self.swing(run, shooter)
        self.assertEqual(vitals.total, 200.0)

    def test_holstering_or_dying_before_impact_cancels_damage(self):
        for cause in ("holster", "death"):
            with self.subTest(cause=cause):
                run, shooter, victim, vitals = self.arena()
                self.swing(run, shooter, 5)
                if cause == "holster":
                    run.frame += 1
                    shooter.take_commands([Command(run.frame, action=1)])
                else:
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is shooter)
                    fight._damage(victim, target, 500.0, False, None, run.frame)
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, 200.0)
