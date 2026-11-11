"""Actual 104319 Grappling Claw and Fireball input against Roadhog."""

import math
import unittest
from dataclasses import replace

import test_game_roadhog_minefield as mine_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import ball_grapple, stats
from ow174.game.collision import World
from ow174.game.mover import FELL
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.nodes import Ability


class RoadhogGrappleTests(unittest.TestCase):
    def prepare(self, run, ball, *, wall=12):
        x, y, z = ball.mover.position
        triangles = box(x - 90, x + 90, y - 1, y, z - 90, z + 90)
        if wall is not None:
            triangles += box(x - 30, x + 30, y - 1, y + 45, z + wall, z + wall + .25)
        run.match.collision_world = World(triangles)
        for _ in range(320):
            run.step({})
        ball.mover.position[1] += 8
        ball.mover.body = None
        ball.mover.flags_word |= FELL
        for _ in range(2):
            run.step({})
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        return ball_grapple.instance(ball, ball_grapple.GRAPPLE)

    def scene(self, *, wall=12, side=0):
        fixture = mine_tests.RoadhogMinefieldTests()
        run, hog, ball, other = fixture.scene()
        x, y, z = ball.mover.position
        fixture.place(hog, (x + side, y, z + 6))
        controller = self.prepare(run, ball, wall=wall)
        return run, hog, ball, other, controller

    def pool(self, run, actor):
        return mine_tests.RoadhogMinefieldTests().pool(run, actor)

    def hold(self, run, ball, frames):
        for _ in range(frames):
            run.step({ball: (0, 0, 2)})

    def hit(self, run, ball, hog, *, button=0):
        for step in range(100):
            command = {ball: (0, 0, 2), hog: (16384, 0, button if step == 0 else 0)}
            run.step(command)
            if ball.stats.value(stats.HERO_DAMAGE):
                return
        self.fail("actual swing never contacted Roadhog")

    def test_wall_attach_arrival_release_cooldown_and_fresh_reuse(self):
        run, hog, ball, _, controller = self.scene()
        fight = run.match.combat
        z = ball.mover.position[2]
        self.hold(run, ball, 1)
        grapple = fight.ball_grapples[ball.body]
        self.assertFalse(grapple.attached)
        self.assertAlmostEqual(grapple.anchor[2] - z, 12)
        self.hold(run, ball, 3)
        self.assertFalse(grapple.attached)
        self.hold(run, ball, 1)
        self.assertTrue(grapple.attached)
        self.assertTrue(controller.vars[435].value())
        self.assertEqual(self.pool(run, hog).total, 600)
        run.step({})
        self.assertNotIn(ball.body, fight.ball_grapples)
        self.assertFalse(controller.vars[435].value())
        self.assertAlmostEqual(controller.states[25].remaining(), 5, delta=.02)
        self.hold(run, ball, 30)
        self.assertNotIn(ball.body, fight.ball_grapples)
        self.assertGreater(controller.states[25].remaining(), 4)
        for _ in range(300):
            run.step({})
        self.assertEqual(controller.states[25].remaining(), 0)
        self.hold(run, ball, 1)
        self.assertIn(ball.body, fight.ball_grapples)
        self.assertNotEqual(fight.ball_grapples[ball.body].activation, grapple.activation)

    def test_real_swing_uses_authored_speed_gate_and_hits_once(self):
        run, hog, ball, _, _ = self.scene()
        fight = run.match.combat
        self.hit(run, ball, hog)
        self.assertTrue(ball_grapple.instance(ball, ball_grapple.ROLL).states[35].active)
        self.assertEqual(self.pool(run, hog).total, 550)
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 37.5)
        self.assertEqual(fight.impulses.velocity(hog.body), (0, 5, 22))
        self.assertFalse(fight.is_stunned(hog))
        self.hold(run, ball, 45)
        self.assertEqual(self.pool(run, hog).total, 550)

    def test_fireball_preserves_buffered_quick_melee_during_displacement(self):
        run, hog, ball, victim, _ = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, ball.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        self.hold(run, ball, 15)

        grapple = {ball: (0, 0, 2)}
        held = {hog: (16384, 0, 0x800)}
        both = {**grapple, **held}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = self.pool(run, victim).total
        run.step(both)
        first = run.frame
        run.step(grapple)
        run.step(both)
        self.assertEqual(self.pool(run, victim).total, before - 30)
        self.assertEqual(self.pool(run, hog).total, 600)
        while run.frame < first + 57 and self.pool(run, hog).total == 600:
            run.step(both)
        self.assertEqual(self.pool(run, hog).total, 550)
        self.assertEqual(fight.impulses.velocity(hog.body), (0, 5, 22))
        while run.frame < first + 57:
            run.step(held)
        moved_x, moved_y, moved_z = hog.mover.position
        self.assertGreater(moved_z, z + 1.5)
        victim.mover.position[:] = (moved_x + 1.5, moved_y, moved_z)
        victim.mover.body = None
        while run.frame < first + 61:
            run.step(held)
        self.assertEqual(self.pool(run, victim).total, before - 30)

        run.step(held)
        self.assertEqual(run.frame, first + 62)
        self.assertGreater(hog.mover.position[2], moved_z)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.pool(run, victim).total, before - 60)
        for _ in range(65):
            run.step(held)
        self.assertEqual(self.pool(run, victim).total, before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_breather_halves_fireball_and_whole_hog_is_not_interrupted(self):
        for button in (16, 32):
            with self.subTest(button=button):
                run, hog, ball, _, _ = self.scene()
                fight = run.match.combat
                pool = self.pool(run, hog)
                if button == 16:
                    pool.damage(300)
                    fight._sync_body_health(hog, pool)
                else:
                    hog.body_script.set_ult_charge(hog.body_script.component.entity_var(ULT_COST).value())
                self.hit(run, ball, hog, button=button)
                if button == 16:
                    self.assertEqual(fight.incoming_damage_scale(hog), .5)
                    self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 25)
                    after = pool.total
                    for _ in range(20):
                        run.step({})
                    self.assertGreater(pool.total, after)
                else:
                    self.assertEqual(pool.total, 550)
                    ultimate, = hog.body_script.ultimates
                    self.assertTrue(ultimate.flags & Ability.RUNNING)

    def test_roadhog_hook_cancels_attached_swing_before_fireball(self):
        run, hog, ball, _, controller = self.scene()
        fight = run.match.combat
        self.hold(run, ball, 5)
        self.assertTrue(fight.ball_grapples[ball.body].attached)
        for i in range(50):
            x, y, z = ball.mover.position
            look = aim(fight._eye(hog), (x, y + .8, z))
            run.step({ball: (0, 0, 2), hog: (*look, 8 if i == 0 else 0)})
            if fight.hooks.controlled(ball.body):
                break
        self.assertTrue(fight.hooks.controlled(ball.body))
        self.assertNotIn(ball.body, fight.ball_grapples)
        self.assertFalse(controller.vars[435].value())
        self.assertGreater(controller.states[25].remaining(), 0)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(self.pool(run, ball).total, 19970)

    def test_allied_sleep_dart_stops_the_swing_before_fireball(self):
        from ow174.game.content import heroes

        run, hog, ball, support, controller = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = support.team = 0
        ball.team = 1
        ana = next(hero for hero in heroes().values() if hero.name == "Ana")
        self.assertTrue(match.switch_hero(support, ana))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 6, y, z)
        support.mover.body = None
        for _ in range(45):
            run.step({support: (0, 0, 0)})

        self.hold(run, ball, 5)
        self.assertTrue(fight.ball_grapples[ball.body].attached)
        enemy = self.pool(run, ball)
        before = enemy.total
        for at in range(65):
            target = next(target for target in fight._targets() if target.owner is ball)
            a, b = target.segment()
            sight = aim(fight._eye(support), tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True)))
            run.step({ball: (0, 0, 2), support: (*sight, 8 if at == 0 else 0)})
            if ball.body in fight.sleep:
                break

        self.assertIn(ball.body, fight.sleep)
        self.assertEqual(enemy.total, before - 5)
        self.assertNotIn(ball.body, fight.ball_grapples)
        self.assertFalse(controller.vars[435].value())
        self.assertGreater(controller.states[25].remaining(), 0)
        deadline = fight.sleep[ball.body]
        while run.frame < deadline - 1:
            self.hold(run, ball, 1)
            self.assertTrue(fight.is_stunned(ball))
            self.assertNotIn(ball.body, fight.ball_grapples)
        run.step({})
        self.assertFalse(fight.is_stunned(ball))
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 0)
        self.assertNotIn(hog.body, fight.impulses.live)

    def test_missing_out_of_range_and_friendly_contacts(self):
        for wall in (None, 30):
            with self.subTest(wall=wall):
                run, hog, ball, _, controller = self.scene(wall=wall)
                self.hold(run, ball, 1)
                self.assertIsNone(run.match.combat.ball_grapples[ball.body].anchor)
                self.hold(run, ball, 15)
                self.assertNotIn(ball.body, run.match.combat.ball_grapples)
                self.assertGreater(controller.states[25].remaining(), 4)
                self.assertEqual(self.pool(run, hog).total, 600)
        run, hog, ball, _, _ = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = ball.team
        self.hold(run, ball, 100)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 0)

    def test_projected_zarya_barrier_absorbs_fireball_and_its_force(self):
        from ow174.game.content import heroes

        run, hog, ball, support, _ = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        ball.team = 1
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 7, y, z)
        support.mover.body = None
        # Cast the projected bubble after support settles and before swing contact.
        for at in range(80):
            look = aim(fight._eye(support), (x, y + 1.2, z))
            run.step({ball: (0, 0, 2), support: (*look, 16 if at == 40 else 0)})
            bubble = fight.zarya_bubbles.for_target(hog)
            if bubble is not None and bubble.health < 200:
                break
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 150)
        self.assertAlmostEqual(support.body_script.component.entity_var(87).value(), 10)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_fireball_crosses_hostile_winston_shell_without_damaging_it(self):
        from ow174.game.content import heroes
        from test_game_roadhog_winston import RoadhogWinstonTests

        fixture = mine_tests.RoadhogMinefieldTests()
        run, hog, ball, caster = fixture.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        ball.team = 1
        winston = next(hero for hero in heroes().values() if hero.name == "Winston")
        self.assertTrue(match.switch_hero(caster, winston))
        x, y, z = ball.mover.position
        fixture.place(hog, (x, y, z + 6))
        fixture.place(caster, (x, y, z + 10.55))
        for _ in range(45):
            run.step({})
        dome = RoadhogWinstonTests().cast(run, hog, caster)
        fixture.place(caster, (x + 25, y, z))
        self.prepare(run, ball)
        self.hit(run, ball, hog)

        roll = ball_grapple.instance(ball, ball_grapple.ROLL)
        offset = roll.evaluate(roll.graph.node(57).field("m_05BE62DD"))
        origin = tuple(p + axis for p, axis in zip(ball.mover.position, offset, strict=True))
        target = next(t for t in fight._targets() if t.owner is hog)
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
        length = math.dist(origin, centre)
        direction = tuple((end - start) / length for start, end in zip(origin, centre, strict=True))
        crossings = fight.winston_bubbles.intercept(ball, origin, direction, length)
        self.assertEqual([actor.body for _, actor, _ in crossings], [dome.body])
        self.assertEqual(dome.health, 700)
        self.assertEqual(self.pool(run, hog).total, 550)
        self.assertEqual(fight.impulses.velocity(hog.body), (0, 5, 22))
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 37.5)
        self.hold(run, ball, 45)
        self.assertEqual(self.pool(run, hog).total, 550)
        self.assertEqual(dome.health, 700)

    def test_lethal_roadhog_slug_retires_live_attachment(self):
        run, hog, ball, _, controller = self.scene()
        fight = run.match.combat
        self.hold(run, ball, 5)
        self.assertTrue(fight.ball_grapples[ball.body].attached)
        pool = self.pool(run, ball)
        pool.damage(pool.total - 1)
        fight._sync_body_health(ball, pool)
        x, y, z = ball.mover.position
        look = aim(fight._eye(hog), (x, y + .65, z))
        run.step({ball: (0, 0, 2), hog: (*look, 2)})
        for _ in range(15):
            if pool.dead:
                break
            run.step({ball: (0, 0, 2), hog: (*look, 0)})
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, hog.body)
        self.assertFalse(controller.vars[435].value())
        self.assertNotIn(ball.body, fight.ball_grapples)
        self.assertNotIn(ball.body, fight.grapple_seen)
        self.assertEqual(self.pool(run, hog).total, 600)

    def test_command_gap_does_not_replay_held_swing_or_fireball(self):
        from ow174.game.commands import Command

        run, hog, ball, _, controller = self.scene()
        fight = run.match.combat
        self.hold(run, ball, 5)
        self.assertTrue(fight.ball_grapples[ball.body].attached)
        z = ball.mover.position[2]
        run.frame += 140
        run.match.update(run.frame * .016, run.frame)
        ball.take_commands([Command(run.frame)])
        self.assertNotIn(ball.body, fight.ball_grapples)
        self.assertFalse(controller.vars[435].value())
        self.assertAlmostEqual(controller.states[25].remaining(), 5 - 139 * .016, delta=.02)
        self.assertEqual(ball.mover.position[2], z)
        self.assertEqual(self.pool(run, hog).total, 600)
