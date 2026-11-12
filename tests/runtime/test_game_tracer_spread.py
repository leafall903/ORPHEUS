"""Actual0016 input bloom and per-bullet rays; native RNG/muzzles remain open."""

import math
import unittest

import test_game_tracer as tracer_tests

from ow174.game import combat, tracer_pistol
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.script import expr


class TracerSpreadTests(tracer_tests.TracerCommandFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.pistol = next(i for i in self.player.body_script.component.instances.values()
                           if i.graph.index == 0x16)
        self.volley = self.pistol.state(8)

    def spread(self):
        return self.pistol.find_var(expr.INSTANCE, 231).value()

    def hold(self, frames):
        for _ in range(frames):
            self.step(FIRE)

    def test_first_pair_has_zero_cone_but_linked_output_counts_the_fired_pair(self):
        self.step(FIRE)
        self.assertEqual(self.spread(), expr.f32(1 / 3))
        key, radius = next(iter(self.volley.shot_spread.items()))
        self.assertEqual(radius, 0)
        self.assertEqual(tracer_pistol.rays(self.volley, key[2], key[3], key[:2], (0, 0, 1), 2),
                         ((0, 0, 1), (0, 0, 1)))

    def test_actual_ramp_is_baked_and_exported_without_losing_reported_angles(self):
        from unittest.mock import patch

        self.step(FIRE)
        self.assertEqual(self.volley.spread_ramp, 3)
        retained = dict(self.volley.shot_spread)
        launch = self.volley.node.field('m_5EC1EB38')
        # Explicit configuration mutation isolates activation baking; it is not
        # a native gameplay mechanism for changing Tracer's authored constant.
        with patch.dict(launch, {'m_AC20B1C1': {'$': 'STUConfigVarFloat', 'm_value': 99}}):
            self.assertAlmostEqual(tracer_pistol.angle(self.volley, 1), math.radians(3.5/3), places=7)
        baseline = snapshot_digest(export_body_vm(self.player.body_script))
        ramp = self.volley.spread_ramp
        self.volley.spread_ramp = 6.0
        self.assertNotEqual(snapshot_digest(export_body_vm(self.player.body_script)), baseline)
        self.volley.spread_ramp = ramp
        self.assertEqual(snapshot_digest(export_body_vm(self.player.body_script)), baseline)
        self.assertEqual(self.volley.shot_spread, retained)

    def test_held_fire_grows_by_shot_count_and_caps_at_authored_three_point_five_degrees(self):
        self.hold(11)
        radii = list(self.volley.shot_spread.values())
        self.assertEqual(len(radii), 4)
        for actual, degrees in zip(radii, (0, 3.5/3, 7/3, 3.5), strict=True):
            self.assertAlmostEqual(actual, math.radians(degrees), places=7)
        self.assertEqual(self.spread(), 1)
        self.hold(15)
        self.assertEqual(self.spread(), 1)

    def test_authored_recovery_waits_after_release_then_reaches_zero(self):
        self.step(FIRE)
        self.step()
        before = self.spread()
        self.wait(10)
        self.assertEqual(self.spread(), before)
        self.wait(6)
        self.assertLess(self.spread(), before)
        self.wait(20)
        self.assertEqual(self.spread(), 0)

    def test_buffered_rapid_repress_retains_bloom_instead_of_resetting_accuracy(self):
        self.step(FIRE)
        self.wait(10)
        self.step(FIRE)
        self.wait(4)
        values = list(self.volley.shot_spread.items())
        self.assertEqual(len(values), 2)
        self.assertNotEqual(values[0][0][:2], values[1][0][:2])
        self.assertEqual(values[0][1], 0)
        self.assertGreater(values[1][1], 0)

    def test_old_queued_activation_keeps_its_angle_after_recovery_and_new_activation(self):
        self.hold(11)
        key, radius = list(self.volley.shot_spread.items())[-1]
        before = tracer_pistol.rays(self.volley, key[2], key[3], key[:2], (0, 0, 1), 2)
        self.wait(40)
        self.assertEqual(self.spread(), 0)
        self.step(FIRE)
        self.assertEqual(self.volley.spread0, 0)
        self.assertEqual(self.volley.shot_spread[key], radius)
        self.assertEqual(tracer_pistol.rays(self.volley, key[2], key[3], key[:2], (0, 0, 1), 2), before)
        self.assertNotEqual(before, ((0, 0, 1), (0, 0, 1)))

    def test_two_rays_are_distinct_unit_vectors_in_the_cone_without_consuming_graph_rng(self):
        self.hold(11)
        key, radius = list(self.volley.shot_spread.items())[-1]
        rng = self.volley.instance.component.rng
        saved = rng.getstate()
        rays = tracer_pistol.rays(self.volley, key[2], key[3], key[:2], (0, 0, 1), 2)
        self.assertNotEqual(rays[0], rays[1])
        for ray in rays:
            self.assertAlmostEqual(sum(x*x for x in ray), 1)
            self.assertLessEqual(math.acos(ray[2]), radius + 1e-7)
        self.assertEqual(rng.getstate(), saved)

    def test_real_far_target_loses_some_bullets_to_spread_in_a_full_magazine(self):
        run, player, enemy, _ = tracer_tests.TracerPistolTests().fight()
        enemy.mover.position[2] = player.mover.position[2] + 20
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        tracer_tests.TracerPistolTests().fire(run, player, enemy, frames=61)
        hits = player.client.messages(combat.HIT)
        self.assertGreaterEqual(len(hits), 2)
        self.assertLess(len(hits), 40)
        self.assertLess(vitals.total, 1000)
        self.assertEqual(next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x16).find_var(0, 53).value(), 0)

    def test_real_wall_can_block_one_bullet_while_the_other_hits(self):
        self.hold(8)  # three reports; the next report has reached full bloom
        state = self.volley
        time = state.start + 150
        rays = tracer_pistol.rays(state, 3, time, (state.counter, state.start), (0, 0, 1), 2)
        x, floor, z = self.player.mover.position
        at_wall = [x + ray[0] * 2.5 / ray[2] for ray in rays]
        self.assertGreater(abs(at_wall[0] - at_wall[1]), 1e-6)
        cut = sum(at_wall) / 2
        face = z + 2.5
        wall = World([
            (-1000, floor, -1000, -1000, floor, 1000, 1000, floor, 1000),
            (-1000, floor, -1000, 1000, floor, 1000, 1000, floor, -1000),
            (x-5, floor, face, cut, floor, face, cut, floor+4, face),
            (x-5, floor, face, cut, floor+4, face, x-5, floor+4, face),
            (x-5, floor, face, cut, floor+4, face, cut, floor, face),
            (x-5, floor, face, x-5, floor+4, face, cut, floor+4, face),
        ], name="Tracer split bullet wall")
        self.run.match.collision_world = wall
        for player in self.run.players:
            player.mover.use(world=wall)
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.enemy)
        vitals = self.run.match.combat.vitals_of(target)
        before = vitals.total
        hits = len(self.player.client.messages(combat.HIT))
        self.hold(3)
        self.assertEqual(vitals.total, before - 6)
        self.assertEqual(len(self.player.client.messages(combat.HIT)), hits + 1)

    def test_read_only_snapshot_retains_the_angle_of_an_already_reported_shot(self):
        self.hold(11)
        script = self.player.body_script
        baseline = snapshot_digest(export_body_vm(script))
        key = next(iter(self.volley.shot_spread))
        self.volley.shot_spread[key] += 0.01
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), baseline)


if __name__ == "__main__":
    unittest.main()
