"""Actual Winston damage volumes against independent defensive surfaces."""

import unittest

import test_game_sombra_grenade as grenades
import test_game_sombra_winston as winston
import test_game_sombra_winston_primal as primal
from test_game_combat import aim
from test_game_sombra_mei_wall import tap
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_reinhardt_charge import surface
from test_game_sombra_winston_jump import finish, step

from ow174.game import mei, winston_combat


class SombraWinstonSurfaceTests(unittest.TestCase):
    def with_wall(self):
        f, fight = winston.SombraWinstonTests().arena(1.5)
        surface(f)
        caster = grenades.SombraGrenadeTests().join_ally(f, mei.HERO, (0, 0, 7))
        place(f.a, (0, 0, 5))
        facing = aim(fight._eye(caster), (0, 0, 3))
        tap(f, caster, 16, facing)
        tap(f, caster, 1, facing)
        pieces = list(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        return f, caster, pieces[2]

    def test_actual_jump_damages_ice_wall_at_takeoff_and_landing(self):
        f, caster, centre = self.with_wall()
        before = f.health(caster)
        step(f, 8)
        self.assertEqual(centre.health, 399)
        finish(f)
        self.assertLess(centre.health, 399)
        self.assertGreater(centre.health, 349)
        self.assertEqual(f.health(caster), before)
        self.assertEqual(f.health(f.a), 200)
        self.assertNotIn(f.a.body, f.run.match.combat.impulses.live)
        self.assertEqual(charge(f.b), 0)

    def test_actual_primal_hits_ice_wall_and_leaves_body_behind_it_unharmed(self):
        f, caster, centre = self.with_wall()
        before = f.health(caster)
        place(f.a, (0, 0, 3.65))
        primal.rage(f)
        for _ in range(70):
            winston.drive(f, 1, point=(0, 1.4, 3.65))
            if centre.health < 400:
                break
        self.assertEqual(centre.health, 360)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(caster), before)
        self.assertNotIn(f.a.body, f.run.match.combat.impulses.live)

    def test_primal_sweep_hits_dome_and_reaches_body_across_shell(self):
        f, fight = winston.SombraWinstonTests().arena(1.2)
        surface(f)
        defender = grenades.SombraGrenadeTests().join_ally(f, winston_combat.HERO, (0, 0, 7))
        f.run.step({defender: (0, 0, 16)})
        winston.drive(f, 0, 5)
        dome, = winston.domes(fight)
        place(f.a, (0, 0, 2.65))
        primal.rage(f)
        for _ in range(70):
            winston.drive(f, 1, point=(0, 1.4, 2.65))
            if dome.health < 700:
                break
        self.assertEqual(dome.health, 660)
        self.assertEqual(f.health(f.a), 160)
        self.assertEqual(f.health(defender), 500)
        self.assertEqual(charge(f.b), 0)
