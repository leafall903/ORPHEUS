"""Actual Pharah primary collision and guarded rocket behavior."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_sombra_barrier as barriers
import test_game_sombra_npc as npcs
import test_game_sombra_pharah as fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, shot_guards, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import graph


class SombraPharahPrimaryTests(unittest.TestCase):
    def test_primary_intercepts_in_three_actual_guard_windows(self):
        cases = ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8), (shot_guards.DVA, 2))
        for hero, button in cases:
            with self.subTest(hero=hero):
                f, guard = barriers.SombraBarrierTests().projected_arena()
                fight = fixture.prepare(f)
                guard.spawned = True
                selected = heroes()[hero]
                if hero == shot_guards.DVA:
                    if graph.entity_body(shot_guards.MECH) is None:
                        self.skipTest("requires prepared actual 0407 mech")
                    selected = replace(selected, body=shot_guards.MECH)
                self.assertTrue(f.run.match.switch_hero(guard, selected))
                place(guard, (0, 0, 3))
                place(f.b, (0, 0, 8))
                f.a.team, f.b.team, guard.team = 0, 1, 0
                f.press(0, 50)
                angles = aim(fight._eye(f.b), (0, 1.45, 0))
                health = f.health(guard)
                returned = False
                for frame in range(50):
                    f.run.step({guard: (0, 0, button if frame == 0 or hero == shot_guards.DVA else 0),
                                f.b: (*angles, 1 if frame == 16 else 0)})
                    if frame == 16:
                        self.assertIsNotNone(shot_guards.active_guard(guard, fight.tick * 16))
                    returned |= any(r.shooter is guard for r in fight.rockets)
                self.assertEqual(f.health(guard), health)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(returned, hero == shot_guards.GENJI)
                self.assertEqual(f.health(f.b), 80 if returned else 200)

    def test_primary_self_splash_never_awards_a_hit_or_charge(self):
        f, _ = fixture.SombraPharahTests().arena()
        place(f.a, (8, 0, 0))
        z = 6.8
        f.geometry((-3, 0, z, 3, 0, z, 3, 4, z), (-3, 0, z, 3, 4, z, -3, 4, z))
        f.run.step({f.b: (0, 0, 1)})
        for _ in range(20):
            f.run.step({f.b: (0, 0, 0)})
        self.assertEqual(f.health(f.b), 160)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(charge(f.b), 0)
        self.assertEqual(f.b.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(f.b.client.messages(combat.HIT))

    def test_primary_hits_and_ends_thermoptic_camo(self):
        f, fight = fixture.SombraPharahTests().arena()
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        fixture.fire(f, fight, (0, 1.15, 0))
        self.assertEqual(f.health(f.a), 80)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertGreater(camo.remaining(), 0)


    def test_primary_extent_grazes_sombra_only_inside_the_side_boundary(self):
        for offset, health in ((-.002, 80), (.002, 200)):
            with self.subTest(offset=offset):
                f, fight = fixture.SombraPharahTests().arena()
                eye, radius, vertical = self.contact_geometry(f, fight)
                lateral = math.sqrt((radius + .2 + offset)**2 - vertical**2)
                place(f.a, (lateral, 0, eye[2] + 6))
                f.run.step({f.b: (0, 0, 1)})
                f.press(0, 25)
                self.assertEqual(f.health(f.a), health)
                self.assertEqual(len(f.b.client.messages(combat.HIT)), 2 if health == 80 else 0)

    @staticmethod
    def contact_geometry(f, fight):
        eye = fight._eye(f.b)
        target = next(t for t in fight._targets() if t.owner is f.a)
        a, b = target.segment()
        return eye, target.radius, max(a[1] - eye[1], eye[1] - b[1], 0)

    def test_expanded_contact_competes_with_wall_by_distance_plus_extent(self):
        for wall, health in ((4.8, 200), (4.95, 80)):
            with self.subTest(wall=wall):
                f, fight = fixture.SombraPharahTests().arena()
                eye, radius, vertical = self.contact_geometry(f, fight)
                lateral = radius + .1
                depth = 4.7 + math.sqrt((radius + .2)**2 - lateral**2)
                place(f.a, (math.sqrt(lateral**2 - vertical**2), 0, eye[2] + depth))
                z = eye[2] + wall
                cover = World([(-8, 0, z, 8, 0, z, 8, 4, z), (-8, 0, z, 8, 4, z, -8, 4, z)])
                # Keep this probe's near-wall capsules in their original pose.
                with patch.object(fight, "_collision", return_value=cover):
                    f.run.step({f.b: (0, 0, 1)})
                    f.press(0, 22)
                self.assertEqual(f.health(f.a), health)

    def test_primary_range_clips_before_the_expanded_contact_outside_100_metres(self):
        for offset, health in ((-.004, 80), (.004, 200)):
            with self.subTest(offset=offset):
                f, fight = fixture.SombraPharahTests().arena()
                f.floor = [(-20, 0, -20, -20, 0, 180, 20, 0, 180),
                           (-20, 0, -20, 20, 0, 180, 20, 0, -20)]
                f.geometry()
                eye, radius, vertical = self.contact_geometry(f, fight)
                depth = math.sqrt((radius + .2)**2 - vertical**2)
                place(f.a, (0, 0, eye[2] + 100 + depth + offset))
                f.run.step({f.b: (0, 0, 1)})
                f.press(0, 195)
                self.assertFalse(fight.rockets)
                self.assertEqual(f.health(f.a), health)



    @staticmethod
    def wall_contact(f, fight, z):
        f.geometry((-8, 0, z, 8, 0, z, 8, 4, z), (-8, 0, z, 8, 4, z, -8, 4, z))
        f.run.step({f.b: (0, 0, 1)})
        for _ in range(25):
            if not fight.rockets:
                return
            f.run.step({f.b: (0, 0, 0)})
        raise AssertionError("Primary rocket did not reach the wall")

    def test_wall_blast_pushes_pharah_and_sombra_but_credits_only_enemy_damage(self):
        f, fight = fixture.SombraPharahTests().arena()
        place(f.a, (1.2, 0, 6.3))
        self.wall_contact(f, fight, 6.8)
        own = fight.impulses.live[f.b.body].velocity
        enemy = fight.impulses.live[f.a.body].velocity
        self.assertAlmostEqual(own[2], -7.5)
        self.assertGreater(own[1], 0)
        self.assertGreater(enemy[0], 0)
        self.assertGreater(enemy[1], 0)
        self.assertEqual(f.health(f.b), 160)
        self.assertLess(f.health(f.a), 200)
        self.assertAlmostEqual(charge(f.b), 200 - f.health(f.a), places=4)

    def test_source_impulse_has_a_separate_cutoff_from_damage(self):
        for offset in (-.002, .002):
            with self.subTest(offset=offset):
                f, fight = fixture.SombraPharahTests().arena()
                place(f.a, (8, 0, 0))
                target = next(t for t in fight._targets() if t.owner is f.b)
                _, y, z = fight._eye(f.b)
                vertical = target.feet[1] + target.height * .5 - y
                depth = math.sqrt((target.radius + 2.5 + offset)**2 - vertical**2)
                self.wall_contact(f, fight, z + depth)
                self.assertEqual(f.b.body in fight.impulses.live, offset < 0)
                self.assertLess(f.health(f.b), 200)
                self.assertEqual(charge(f.b), 0)


class SombraPharahPrimaryNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npcs.SombraActualNpcTests.restore_graphs(path)

    def test_expanded_primary_grazes_each_actual_npc(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npcs.SombraActualNpcTests.make(self, key)
                fight = fixture.prepare(f)
                place(f.a, (8, 0, 0))
                target = next(t for t in fight._targets() if t.owner is bot)
                bot.position = (target.radius + .1, 0, 12)
                pool = fight.vitals[bot.entity]
                before = pool.total
                fixture.fire(f, fight, (0, target.height * .55, 12))
                self.assertEqual(len(f.b.client.messages(combat.HIT)), 2)
                self.assertLess(pool.total, before)
                self.assertAlmostEqual(charge(f.b), before - pool.total, places=5)
