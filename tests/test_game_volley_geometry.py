"""Reconstructed pellet geometry; native rotation/muzzle/units remain unaccepted."""

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import bots, combat, volley_geometry
from ow174.game.commands import Command
from ow174.game.script import expr


def number(value):
    return {"$": "STUConfigVarFloat", "m_value": value}


def state(spread=4, count=25):
    launch = {"$": "STU_613BDDB0", "m_spread": number(spread), "m_92308789": number(2),
              "m_muzzles": [{}], "m_0D30E40C": [
                  {"$": "STU_B101467F", "m_6425FFB9": number(radius), "m_B19BD35D": number(size)}
                  for radius, size in ((0, 1), (0.5, 8), (1, 16))]}
    fields = {"m_numProjectilesPerShot": number(count), "m_5EC1EB38": launch,
              "m_6396149F": {"m_modifyHealth": {"m_amount": number(2)}, "m_A872C70E": number(1)}}
    return SimpleNamespace(node=SimpleNamespace(field=fields.get, pos=24),
                           instance=SimpleNamespace(component=SimpleNamespace(frame_ms=16),
                                                    graph=SimpleNamespace(index=0xFFFF)),
                           evaluate=lambda cfg, watch=False: expr.evaluate(cfg, expr.Context()))


class RingGeometryTests(unittest.TestCase):
    def test_ring_counts_angles_and_symmetry(self):
        rays = volley_geometry.directions(state(), (0, 0, 1), reconstructed_patterns=True)
        self.assertEqual(len(rays), 25)
        self.assertEqual(rays[0], (0, 0, 1))
        for ray, angle in zip(rays, [0] + [2] * 8 + [4] * 16, strict=True):
            self.assertAlmostEqual(sum(v * v for v in ray), 1)
            self.assertAlmostEqual(math.degrees(math.acos(ray[2])), angle)
        self.assertAlmostEqual(sum(v[0] for v in rays), 0)
        self.assertAlmostEqual(sum(v[1] for v in rays), 0)

    def test_zero_spread_and_vertical_aim_remain_finite(self):
        for forward in ((0, 1, 0), (0, -1, 0), (1, 0, 0)):
            rays = volley_geometry.directions(state(0), forward, reconstructed_patterns=True)
            self.assertEqual(rays, (forward,) * 25)

    def test_count_must_match_rings_and_unknown_launch_transforms_are_refused(self):
        for change in (lambda f: f.update(m_numProjectilesPerShot=number(24)),
                       lambda f: f['m_5EC1EB38'].update(m_22CE7F30=number(1)),
                       lambda f: f['m_5EC1EB38'].update(m_92308789=number(3)),
                       lambda f: f['m_5EC1EB38'].update(m_spread=number(float('nan')))):
            shot = state()
            change(shot.node.field.__self__)
            with self.assertRaises(volley_geometry.UnsupportedGeometry):
                volley_geometry.directions(shot, (0, 0, 1), reconstructed_patterns=True)

    def test_single_zero_and_opt_in(self):
        self.assertEqual(volley_geometry.directions(state(count=1), (0, 0, 1)), ((0, 0, 1),))
        self.assertEqual(volley_geometry.directions(state(count=0), (0, 0, 1)), ())
        with self.assertRaises(volley_geometry.UnsupportedGeometry):
            volley_geometry.directions(state(), (0, 0, 1))

    def test_paired_muzzles_use_two_independent_opposite_directions(self):
        shot = state(count=2)
        launch = {"$": "STU_239B571E", "m_muzzles": [{}, {}], "m_AC20B1C1": number(10),
                  "m_7F0FED02": number(3), "m_429A6C85": number(3)}
        shot.node.field.__self__['m_5EC1EB38'] = launch
        rays = volley_geometry.directions(shot, (0, 0, 1), reconstructed_patterns=True)
        self.assertEqual(len(rays), 2)
        self.assertAlmostEqual(rays[0][0], -rays[1][0])
        self.assertAlmostEqual(rays[0][1], -rays[1][1])
        for ray in rays:
            self.assertAlmostEqual(sum(v * v for v in ray), 1)
        launch['m_muzzles'].pop()
        with self.assertRaises(volley_geometry.UnsupportedGeometry):
            volley_geometry.directions(shot, (0, 0, 1), reconstructed_patterns=True)


class PelletCombatTests(unittest.TestCase):
    def arena(self, *, x=0, distance=1, armour=0):
        run = Run(names=("Shooter", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, victim = run.players
        floor = shooter.mover.position[1]
        shooter.mover.position[:] = (0, floor, 0)
        victim.mover.position[:] = (x, floor, distance)
        shooter.mover.body = victim.mover.body = None
        fight = match.combat
        fight.history.clear()
        fight.replacement_ticks.clear()
        fight.vitals[victim.body] = vitals = combat.Vitals(victim.body, 200, armour=armour)
        fight.configure_volley_geometry(reconstructed_patterns=True)
        return fight, shooter, vitals

    def shoot(self, fight, shooter, shot=None):
        fight._shoot(shooter, Command(1000), shot or state(), 0, 16000, activation=(1, 16000))

    def test_close_shot_processes_all_pellets_and_armour_independently(self):
        for armour, expected in ((0, 150), (50, 225)):
            with self.subTest(armour=armour):
                fight, shooter, vitals = self.arena(armour=armour)
                with patch.object(fight, '_hitscan', wraps=fight._hitscan) as ray:
                    self.shoot(fight, shooter)
                self.assertEqual(ray.call_count, 25)
                self.assertEqual(vitals.total, expected)

    def test_off_axis_capsule_is_hit_by_outer_pellets_when_centre_ray_misses(self):
        fight, shooter, vitals = self.arena(x=0.65, distance=5)
        self.shoot(fight, shooter, state(count=1))
        self.assertEqual(vitals.total, 200)
        self.shoot(fight, shooter)
        self.assertLess(vitals.total, 200)
        self.assertGreater(vitals.total, 150)

    def test_each_pellet_checks_static_occlusion_and_range(self):
        fight, shooter, vitals = self.arena()
        with patch.object(combat, 'ray_world', return_value=0) as wall:
            self.shoot(fight, shooter)
        self.assertEqual(wall.call_count, 25)
        self.assertEqual(vitals.total, 200)
        shot = state()
        shot.node.field.__self__['m_FEC435C6'] = number(0.1)
        self.shoot(fight, shooter, shot)
        self.assertEqual(vitals.total, 200)

    def test_unconfigured_multiple_pellets_are_refused_and_flag_is_explicit(self):
        fight, shooter, vitals = self.arena()
        fight.configure_volley_geometry(reconstructed_patterns=False)
        self.shoot(fight, shooter)
        self.assertEqual(vitals.total, 200)
        with self.assertRaises(ValueError):
            fight.configure_volley_geometry(reconstructed_patterns=1)

    def test_multiple_player_projectile_leases_are_not_silently_replaced_by_one(self):
        fight, shooter, vitals = self.arena()
        shot = state()
        shot.node.field.__self__['m_projectileSpeed'] = number(20)
        self.shoot(fight, shooter, shot)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(fight.rockets, [])

    def test_npc_pellets_fly_and_damage_independently(self):
        fight, shooter, vitals = self.arena()
        bot = object.__new__(bots.Bot)
        bot.entity, bot.team, bot.position = 0xC0000100, shooter.team, tuple(shooter.mover.position)
        bot.yaw = bot.pitch = 0
        shot = state()
        shot.node.field.__self__['m_projectileSpeed'] = number(80)
        self.shoot(fight, bot, shot)
        self.assertEqual(len(fight.rockets), 25)
        self.assertEqual(len({id(rocket) for rocket in fight.rockets}), 25)
        self.assertEqual(len({id(rocket.hit_entities) for rocket in fight.rockets}), 25)
        self.assertEqual(vitals.total, 200)  # projectiles must travel before doing damage
        fight.tick += 1
        for rocket in tuple(fight.rockets):
            fight._fly(rocket)
        self.assertEqual(vitals.total, 150)
        self.assertFalse(fight.rockets)
