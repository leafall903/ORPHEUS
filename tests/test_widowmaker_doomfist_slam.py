"""Actual Doomfist Seismic Slam against Widowmaker in build 104319."""

import unittest

from test_game_mover import box
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import combat, doomfist_punch, server_snapshot
from ow174.game.collision import World
from ow174.game.content import heroes


class WidowmakerSeismicSlamTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[doomfist_punch.HERO]))
        self.a.mover.position[:] = (0, 0, 10)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat
        self.slams = self.fight.slams

    def land(self, *, aerial=False):
        if aerial:
            self.b.mover.position[1] = 8
            self.b.mover.body = None
        self.run.step({self.b: (0, 9000 if aerial else 0, 16)})
        for _ in range(140):
            cast = self.slams.casts.get(self.b.body)
            if cast is not None and cast.wave_at is not None:
                return cast
            self.assertEqual(self.health(self.a), 175)
            self.run.step({})
        self.fail(str((self.slams.casts, self.b.mover.position)))

    def test_actual_ground_slam_lands_then_damages_and_displaces(self):
        cast = self.land()
        self.assertEqual(cast.kind, 'ground')
        self.assertGreater(cast.origin[2], 1)
        self.assertGreater(cast.damage, 25)
        self.assertLessEqual(cast.damage, 125)
        for _ in range(35):
            self.run.step({})
        self.assertAlmostEqual(self.health(self.a), 175 - cast.damage)
        self.assertLess(self.a.mover.position[2], 10)
        self.assertGreater(self.a.mover.position[1], 0.1)
        self.assertTrue(cast.finished)
        for _ in range(90):
            self.run.step({})
        self.assertAlmostEqual(self.a.mover.position[1], 0, delta=0.01)
        self.assertAlmostEqual(self.health(self.a), 175 - cast.damage)

    def test_actual_downward_aerial_slam_lands_and_hits(self):
        cast = self.land(aerial=True)
        self.assertEqual(cast.kind, 'aerial')
        self.assertEqual(cast.speed, 25)
        self.assertAlmostEqual(cast.destination[1], 0)
        for _ in range(55):
            self.run.step({})
        self.assertTrue(cast.finished)
        self.assertLess(self.health(self.a), 175)
        self.assertAlmostEqual(self.health(self.a), 175 - cast.damage)

    def test_static_wall_blocks_wave_and_prevents_damage(self):
        self.run.match.collision_world = World(
            box(-40, 40, -1, 0, -40, 40) + box(-8, 8, 0, 6, 3, 4),
        )
        cast = self.land()
        for _ in range(80):
            self.run.step({})
        self.assertTrue(cast.finished)
        self.assertLess(self.b.mover.position[2], 3)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.vitals_of(self.target(self.b)).temporary_shields)

    def test_released_wave_advances_without_further_caster_pose(self):
        cast = self.land()
        body_frame = self.b.body_script.frame
        for _ in range(40):
            self.run.frame += 1
            tick = self.run.frame
            self.run.match.update(tick * combat.TICK, tick)
        self.assertTrue(cast.finished)
        self.assertEqual(self.b.body_script.frame, body_frame)
        self.assertAlmostEqual(self.health(self.a), 175 - cast.damage)

    def test_active_wave_progress_changes_stored_state(self):
        cast = self.land()

        def capture():
            graph = server_snapshot._Graph({'combat': self.fight}, server_snapshot.WorldRegistry())
            root = graph.value(self.slams)
            return root, graph.finish()

        initial = capture()
        cast.wave_at += 0.016
        self.assertNotEqual(capture(), initial)



class WidowmakerSeismicSlamBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_absorbs_wave_without_displacement_or_passive_grant(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[doomfist_punch.HERO]))
        self.a.mover.position[:] = (0, 0, 10)
        self.b.mover.position[:] = (0, 0, 0)
        self.c.mover.position[:] = (-8, 0, 10)
        for player in self.run.players:
            player.mover.body = None
        self.activate()
        fight = self.run.match.combat
        self.run.step({self.b: (0, 0, 16)})
        for _ in range(115):
            self.run.step({})
            cast = fight.slams.casts.get(self.b.body)
            if cast is not None and cast.finished:
                break
        self.assertIn(self.a.body, cast.hit)
        self.assertAlmostEqual(fight.zarya_barriers.projected[self.c.body].remaining, 200 - cast.damage)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(tuple(self.a.mover.position), (0, 0, 10))
        self.assertFalse(fight.vitals_of(self.target(self.b)).temporary_shields)
