"""Real Sombra shots through the original cone math and reconstructed seed mapping."""

import math
import unittest
from unittest.mock import patch

import test_game_sombra_combat as fixture

from ow174.game import combat, sombra_spread, weapon_spread
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.script import expr, nodes


class SombraSpreadTests(unittest.TestCase):
    def make(self, distance=1.5):
        f = fixture.SombraCombatTests()
        f.setUp()
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.press(0, 30)
        f.a.body_script.ult_seconds = 0
        f.a.body_script.set_ult_charge(0)
        return f

    @staticmethod
    def volley(f):
        return next(s for s in f.weapon().states.values() if isinstance(s, nodes.WeaponVolley))

    def test_actual_pistol_bakes_original_ramp_and_cone_endpoints(self):
        f = self.make()
        f.press(1)
        state = self.volley(f)
        launch = state.node.field('m_5EC1EB38')
        self.assertEqual(launch['$'], 'STU_239B571E')
        self.assertIsNone(launch.get('m_23C72C36'))
        self.assertEqual(state.spread_ramp, 3)
        self.assertEqual(state.spread0, 0)
        self.assertEqual([state.evaluate(launch[k], watch=False)
                          for k in ('m_7F0FED02', 'm_429A6C85')], [expr.f32(.5), expr.f32(2.4)])

    def test_actual_shots_ramp_first_three_cones_then_keep_maximum(self):
        f = self.make()
        with patch.object(weapon_spread, 'direction', wraps=weapon_spread.direction) as sample:
            f.press(1, 20)
        self.assertGreaterEqual(sample.call_count, 6)
        cones = [c.args[1] for c in sample.call_args_list]
        expected = []
        for shot in range(len(cones)):
            value = min(1., expr.f32((shot + 1) / 3))
            deg = expr.f32(expr.f32(expr.f32(expr.f32(2.4) - expr.f32(.5)) * value) + expr.f32(.5))
            expected.append(expr.f32(deg * expr.f32(math.pi / 180)))
        self.assertEqual(cones, expected)
        self.assertEqual(f.health(f.b), 200 - 8 * len(cones))
        self.assertEqual(f.weapon().vars[53].value(), 60 - len(cones))

    def test_actual_ray_is_sampled_and_seeded_by_stored_activation_identity(self):
        f = self.make()
        with (patch.object(sombra_spread, 'direction', wraps=sombra_spread.direction) as policy,
              patch.object(weapon_spread, 'direction', wraps=weapon_spread.direction) as sample):
            f.press(1)
        shooter, state, forward, number, _time, activation = policy.call_args.args
        cone, base = sample.call_args.args[1:3]
        self.assertEqual(base, weapon_spread.seed_base(shooter.body, state.instance.id,
                                                     state.node.pos, activation[0]))
        actual = weapon_spread.direction(forward, cone, base, number)
        self.assertNotEqual(actual, forward)
        self.assertAlmostEqual(sum(v*v for v in actual), 1, places=6)
        angle = math.acos(min(1., sum(a*b for a, b in zip(actual, forward, strict=True))))
        self.assertGreater(angle, 0)
        self.assertLessEqual(angle, cone + 1e-6)

    def test_far_actual_burst_has_real_hits_and_misses_without_spending_extra_rounds(self):
        f = self.make(40)
        f.press(1, 170)
        rounds = 60 - f.weapon().vars[53].value()
        hits = f.a.client.messages(combat.HIT)
        self.assertGreater(rounds, len(hits))
        self.assertGreater(len(hits), 0)
        self.assertGreater(len(hits), 1)
        criticals = [bool(hit['+0x78']['+0x8'] & combat.CRITICAL) for hit in hits]
        self.assertIn(True, criticals)
        self.assertIn(False, criticals)
        loss = 2.4 * (len(hits) + sum(criticals))
        self.assertAlmostEqual(f.health(f.b), 200 - loss, places=4)
        self.assertAlmostEqual(f.a.body_script.component.entity_var(342).value(), loss, places=4)
        self.assertEqual([hit['+0x78']['+0x4'] for hit in hits], [5 if crit else 2 for crit in criticals])

    def test_static_wall_blocks_every_sampled_ray_without_hit_or_damage_charge(self):
        f = self.make()
        f.wall()
        f.press(1, 30)
        self.assertEqual(f.health(f.b), 200)
        self.assertEqual(f.a.client.messages(combat.HIT), [])
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(f.weapon().vars[53].value(), 50)

    def test_live_baked_ramp_is_in_actual_body_causal_snapshot(self):
        f = self.make()
        f.press(1)
        state = self.volley(f)
        before = snapshot_digest(export_body_vm(f.a.body_script))
        state.spread_ramp = 4.
        self.assertNotEqual(before, snapshot_digest(export_body_vm(f.a.body_script)))
        state.spread_ramp = 3.
        self.assertEqual(before, snapshot_digest(export_body_vm(f.a.body_script)))

    def test_same_shot_inputs_are_reproducible_without_mutable_random_state(self):
        f = self.make()
        f.press(1)
        state = self.volley(f)
        args = (f.a, state, (0., 0., 1.), 0, state.start, (state.counter, state.start))
        first = sombra_spread.direction(*args)
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        for _ in range(5):
            self.assertEqual(sombra_spread.direction(*args), first)

    def test_non_pistol_hero_and_unmodeled_launch_do_not_take_sombra_policy(self):
        f = self.make()
        f.press(1)
        state = self.volley(f)
        args = (state, (0., 0., 1.), 0, state.start, (state.counter, state.start))
        self.assertEqual(sombra_spread.direction(f.b, *args), (0., 0., 1.))
        state.spread_ramp = None
        self.assertEqual(sombra_spread.direction(f.a, *args), (0., 0., 1.))

    def test_actual_vertical_up_and_down_aim_samples_near_pole_axis_and_hits(self):
        for upward in (True, False):
            with self.subTest(upward=upward):
                f = self.make()
                f.a.mover.position[:] = (0, 0 if upward else 10, 0)
                f.b.mover.position[:] = (0, 10 if upward else 0, 0)
                f.a.mover.body = f.b.mover.body = None
                with patch.object(weapon_spread, 'direction', wraps=weapon_spread.direction) as sample:
                    f.press(1)
                forward = sample.call_args.args[0]
                self.assertAlmostEqual(forward[1], 1 if upward else -1)
                self.assertLess(abs(forward[2]), 1e-15)
                self.assertGreater(abs(forward[2]), 0)
                self.assertEqual(f.health(f.b), 192 if upward else 184)
                self.assertEqual(f.weapon().vars[53].value(), 59)


class SombraSpreadNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global actual_npcs
        import test_game_sombra_npc as actual_npcs
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_actual_far_sampled_bursts_reconcile_rounds_hits_pool_loss_and_charge_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=40)
                f.a.body_script.ult_seconds = 0
                f.a.body_script.set_ult_charge(0)
                before = f.run.match.combat.vitals[bot.entity].total
                point = (0, 1, 40)
                yaw, pitch = actual_npcs.aim(f.run.match.combat._eye(f.a), point)
                for _ in range(170):
                    f.run.step({f.a: (yaw, pitch, 1)})
                rounds = 60 - f.weapon().vars[53].value()
                hits = f.a.client.messages(combat.HIT)
                self.assertGreater(len(hits), 0)
                self.assertGreater(rounds, len(hits))
                loss = before - f.run.match.combat.vitals[bot.entity].total
                criticals = sum(bool(hit['+0x78']['+0x8'] & combat.CRITICAL) for hit in hits)
                self.assertAlmostEqual(loss, 2.4 * (len(hits) + criticals), places=4)
                self.assertAlmostEqual(f.a.body_script.component.entity_var(342).value(), loss, places=4)

    def test_actual_near_bursts_still_hit_all_five_bodies_at_authored_rate(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=3)
                before = f.run.match.combat.vitals[bot.entity].total
                f.press(1, 30)
                self.assertEqual(f.weapon().vars[53].value(), 50)
                self.assertEqual(f.run.match.combat.vitals[bot.entity].total, before - 80)
