"""Actual build 104319 Quad Cannons input against Roadhog and defenses."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_minefield as mine_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import ball_grapple, combat, quad_cannons
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class RoadhogQuadCannonsTests(unittest.TestCase):
    def place(self, actor, point):
        mine_tests.RoadhogMinefieldTests().place(actor, point)

    def scene(self, distance=4):
        run, hog, ball, other = mine_tests.RoadhogMinefieldTests().scene()
        x, y, z = ball.mover.position
        self.place(hog, (x, y, z + distance))
        self.place(other, (x + 35, y, z))
        for _ in range(40):
            run.step({})
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        return run, hog, ball, other

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def pool(self, run, actor):
        return run.match.combat.vitals_of(self.target(run, actor))

    def look(self, run, source, target, height=.75):
        x, y, z = self.target(run, target).feet
        return aim(run.match.combat._eye(source), (x, y + height, z))

    def weapon(self, ball):
        return ball_grapple.instance(ball, quad_cannons.GRAPH)

    def charge(self, actor):
        return actor.body_script.component.entity_var(ULT_CHARGE).value()

    def test_actual_cannon_damage_critical_and_falloff_fund_charge(self):
        for distance, head, expected in ((4, False, 5), (4, True, 10), (20, False, 3.25)):
            with self.subTest(distance=distance, head=head):
                run, hog, ball, _ = self.scene(distance)
                height = self.target(run, hog).eye if head else .75
                angles = self.look(run, ball, hog, height)
                run.step({ball: (*angles, 1)})
                self.assertEqual(self.weapon(ball).vars[53].value(), 79)
                self.assertAlmostEqual(self.pool(run, hog).total, 600 - expected, delta=.25)
                self.assertAlmostEqual(self.charge(ball), (600 - self.pool(run, hog).total) * .75, places=5)
                hit, = ball.client.messages(combat.HIT)
                self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), head)

    def test_hold_emits_cone_release_stops_and_reload_restores(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        weapon = self.weapon(ball)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            for _ in range(30):
                run.step({ball: (-32768, 0, 1)})
            self.assertEqual(weapon.vars[53].value(), 68)
            rays = [call.args[3] for call in hitscan.call_args_list]
        self.assertEqual(len(rays), 12)
        self.assertGreater(len(set(rays)), 3)
        deviation = [math.hypot(x, y) for x, y, _ in rays]
        self.assertLess(max(deviation[:4]), max(deviation[-4:]))
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(self.charge(ball), 0)
        run.step({ball: (-32768, 0, 0)})
        self.assertEqual(weapon.vars[53].value(), 67)
        for _ in range(10):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 67)
        run.step({ball: (0, 0, 1024)})
        for _ in range(130):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 80)
        run.step({ball: (*self.look(run, ball, hog), 1)})
        self.assertEqual(self.pool(run, hog).total, 595)

    def test_wall_ally_and_beyond_authored_range_reject_a_round(self):
        for mode in ("wall", "ally", "range"):
            with self.subTest(mode=mode):
                run, hog, ball, _ = self.scene(101 if mode == "range" else 4)
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = ball.team
                elif mode == "wall":
                    x, y, z = ball.mover.position
                    run.match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + box(x - 4, x + 4, y, y + 5, z + 2, z + 2.25),
                    )
                run.step({ball: (*self.look(run, ball, hog), 1)})
                self.assertEqual(self.weapon(ball).vars[53].value(), 79)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertEqual(self.charge(ball), 0)

    def test_breather_and_lethal_round_credit_only_effective_damage(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, hog, ball, _ = self.scene()
                fight = run.match.combat
                pool = self.pool(run, hog)
                if lethal:
                    pool.damage(599)
                else:
                    pool.damage(300)
                fight._sync_body_health(hog, pool)
                if not lethal:
                    run.step({hog: (0, 0, 16)})
                    for _ in range(75):
                        if fight.incoming_damage_scale(hog) == .5:
                            break
                        run.step({})
                    self.assertEqual(fight.incoming_damage_scale(hog), .5)
                run.step({ball: (*self.look(run, ball, hog), 1)})
                self.assertEqual(self.charge(ball), .75 if lethal else 1.875)
                if lethal:
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, ball.body)
                else:
                    after = pool.total
                    for _ in range(70):
                        run.step({})
                    self.assertGreater(pool.total, after)

    def test_roadhog_hook_suppresses_cannon_until_release(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        look = self.look(run, hog, ball)
        run.step({hog: (*look, 8)})
        for _ in range(60):
            if fight.hooks.controlled(ball.body):
                break
            run.step({hog: (*look, 0)})
        self.assertTrue(fight.hooks.controlled(ball.body))
        for _ in range(8):
            run.step({ball: (*self.look(run, ball, hog), 1)})
            self.assertTrue(fight.hooks.controlled(ball.body))
        self.assertEqual(self.weapon(ball).vars[53].value(), 80)
        self.assertEqual(self.pool(run, hog).total, 600)
        for _ in range(80):
            if not fight.hooks.controlled(ball.body):
                break
            run.step({})
        self.assertFalse(fight.hooks.controlled(ball.body))
        run.step({ball: (*self.look(run, ball, hog), 1)})
        self.assertEqual(self.weapon(ball).vars[53].value(), 79)
        self.assertEqual(self.pool(run, hog).total, 595)

    def test_genji_reflects_a_round_into_roadhog_with_final_source_credit(self):
        run, hog, ball, genji = self.scene(3)
        hero = heroes()[quad_cannons.GENJI]
        self.assertTrue(run.match.switch_hero(genji, hero))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = ball.team = 0
        genji.team = 1
        x, y, z = ball.mover.position
        self.place(genji, (x, y, z + 6))
        for _ in range(40):
            run.step({})
        script = genji.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 1)
        face = self.look(run, genji, hog)
        run.step({genji: (*face, 16)})
        for _ in range(5):
            run.step({genji: (*face, 0)})
        run.step({ball: (*self.look(run, ball, genji), 1), genji: (*face, 0)})
        self.assertEqual(self.pool(run, hog).total, 595)
        self.assertEqual(self.charge(genji), cost)
        self.assertEqual(self.charge(ball), 0)
        self.assertFalse(ball.client.messages(combat.HIT))
        hit, = genji.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], hog.body)
        self.assertEqual(self.weapon(ball).vars[53].value(), 79)

    def test_roll_uses_recorded_form_to_reject_roadhog_head_pellets(self):
        for rolled in (False, True):
            with self.subTest(rolled=rolled):
                run, hog, ball, _ = self.scene(1.8)
                fight = run.match.combat
                normal_tick = fight.tick
                if rolled:
                    run.step({ball: (0, 0, 8)})
                    for _ in range(25):
                        run.step({})
                self.assertEqual(ball_grapple.rolling(ball), rolled)
                current = self.target(run, ball)
                self.assertTrue(fight.seen(current, normal_tick).critical_allowed)
                self.assertEqual(fight.seen(current, fight.tick).critical_allowed, not rolled)
                before = self.pool(run, ball).total
                run.step({hog: (*self.look(run, hog, ball, current.eye), 1)})
                for _ in range(25):
                    run.step({})
                hits = [hit["+0x78"] for hit in hog.client.messages(combat.HIT)]
                self.assertTrue(hits)
                self.assertEqual(any(hit["+0x8"] & combat.CRITICAL for hit in hits), not rolled)
                self.assertLess(self.pool(run, ball).total, before)
                if rolled:
                    for _ in range(45):
                        run.step({ball: (*self.look(run, ball, hog), 1)})
                        if self.weapon(ball).vars[53].value() < 80:
                            break
                    self.assertFalse(ball_grapple.rolling(ball))
                    self.assertEqual(self.weapon(ball).vars[53].value(), 79)
                    self.assertEqual(self.pool(run, hog).total, 595)

    def test_sigma_grasp_absorbs_round_before_roadhog(self):
        run, hog, ball, sigma = self.scene(8)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = sigma.team = 0
        ball.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Sigma")
        self.assertTrue(run.match.switch_hero(sigma, hero))
        x, y, z = ball.mover.position
        self.place(sigma, (x, y, z + 4))
        for _ in range(45):
            run.step({})
        before = self.pool(run, sigma).total
        face = self.look(run, sigma, ball, 1.2)
        run.step({sigma: (*face, 8)})
        run.step({ball: (*self.look(run, ball, sigma, 1.2), 1), sigma: (*face, 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(grasp.vars[21312].value(), 5)
        self.assertEqual(self.pool(run, sigma).total, before)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(self.charge(ball), 0)
        self.assertFalse(ball.client.messages(combat.HIT))
        self.assertEqual(self.weapon(ball).vars[53].value(), 79)

    def test_projected_zarya_barrier_spends_pool_without_cannon_charge(self):
        run, hog, ball, support = self.scene(5)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        ball.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, hero))
        x, y, z = hog.mover.position
        self.place(support, (x + 7, y, z))
        for _ in range(45):
            run.step({})
        run.step({support: (*self.look(run, support, hog, 1.2), 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        run.step({ball: (*self.look(run, ball, hog), 1)})
        self.assertEqual(bubble.health, 195)
        self.assertEqual(support.body_script.component.entity_var(87).value(), 1)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(self.charge(ball), 0)

    def test_twenty_shot_ramp_empty_refill_and_duplicate_inputs(self):
        from ow174.game.commands import Command
        from ow174.game.script import expr

        run, hog, ball, _ = self.scene(80)
        weapon = self.weapon(ball)
        angles = self.look(run, ball, hog, 1.05)
        observed = []
        original = quad_cannons.direction

        def trace(shooter, state, forward, number, time, activation):
            vector = original(shooter, state, forward, number, time, activation)
            ramp = state.spread_at(time - activation[1] - state.offset)
            observed.append((number, ramp, vector, activation))
            return vector

        with patch.object(quad_cannons, "direction", side_effect=trace):
            for _ in range(220):
                run.step({ball: (*angles, 1)})
                ball.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                if weapon.vars[53].value() == 0:
                    break
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual([number for number, *_ in observed], list(range(80)))
        for number, ramp, _, _ in observed:
            self.assertEqual(ramp, min(1, expr.f32((number + 1) / 20)))
        self.assertEqual(len({activation for *_, activation in observed}), 1)
        hits = len(ball.client.messages(combat.HIT))
        self.assertGreater(hits, 0)
        self.assertLess(hits, 80)
        self.assertGreater(self.pool(run, hog).total, 0)
        self.assertEqual(weapon.vars[231].value(), 1)
        for _ in range(145):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 80)
        self.assertEqual(weapon.vars[231].value(), 0)

    def test_active_spread_ramp_is_causal_body_vm_storage(self):
        from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest

        run, _, ball, _ = self.scene()
        run.step({ball: (-32768, 0, 1)})
        volley = self.weapon(ball).states[9]
        self.assertTrue(volley.active)
        self.assertEqual(volley.spread_ramp, 20)
        with patch.object(type(volley), "spread_at", side_effect=AssertionError("no spread evaluation")):
            before = snapshot_digest(export_body_vm(ball.body_script))
            self.assertEqual(snapshot_digest(export_body_vm(ball.body_script)), before)
            volley.spread_ramp = 21
            self.assertNotEqual(snapshot_digest(export_body_vm(ball.body_script)), before)

    def test_roadhog_whole_hog_keeps_hitting_after_cannon_damage(self):
        from ow174.game.script.nodes import Ability

        run, hog, ball, target = self.scene(6)
        x, y, z = hog.mover.position
        self.place(target, (x + 8, y, z))
        for _ in range(8):
            run.step({})
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        ultimate, = script.ultimates
        target_pool = self.pool(run, target)
        run.step({hog: (*self.look(run, hog, target, 1.1), 32)})
        for _ in range(45):
            run.step({hog: (*self.look(run, hog, target, 1.1), 0)})
            if target_pool.total < target_pool.maximum:
                break
        self.assertLess(target_pool.total, target_pool.maximum)
        run.step({ball: (*self.look(run, ball, hog), 1), hog: (*self.look(run, hog, target, 1.1), 0)})
        self.assertEqual(self.pool(run, hog).total, 595)
        self.assertTrue(ultimate.flags & Ability.RUNNING)
        after = target_pool.total
        for _ in range(35):
            run.step({hog: (*self.look(run, hog, target, 1.1), 0)})
            if target_pool.total < after:
                break
        self.assertLess(target_pool.total, after)
        self.assertTrue(ultimate.flags & Ability.RUNNING)
