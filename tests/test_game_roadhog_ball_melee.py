"""Build 104319 Wrecking Ball quick melee into Roadhog and active defenses."""

import unittest
from dataclasses import replace

import test_game_roadhog_quad_cannons as quad_tests
from test_game_mover import box

from ow174.game import ball_grapple, combat, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class RoadhogBallMeleeTests(unittest.TestCase):
    def melee(self, run, ball, actor):
        fixture = quad_tests.RoadhogQuadCannonsTests()
        look = fixture.look(run, ball, actor, .95)
        run.step({ball: (*look, 0x800)})
        for _ in range(30):
            run.step({ball: (*look, 0)})

    def test_melee_actual_damage_and_charge_mitigation_and_overkill(self):
        fixture = quad_tests.RoadhogQuadCannonsTests()
        for mode, amount in (("normal", 30), ("breather", 15), ("lethal", 1), ("cap", 30)):
            with self.subTest(mode=mode):
                run, hog, ball, _ = fixture.scene(1.6)
                fight = run.match.combat
                pool = fixture.pool(run, hog)
                if mode == "breather":
                    pool.damage(300)
                    fight._sync_body_health(hog, pool)
                    run.step({hog: (0, 0, 16)})
                    for _ in range(75):
                        if fight.incoming_damage_scale(hog) == .5:
                            break
                        run.step({})
                    self.assertEqual(fight.incoming_damage_scale(hog), .5)
                if mode == "lethal":
                    pool.damage(599)
                    fight._sync_body_health(hog, pool)
                cost = ball.body_script.component.entity_var(ULT_COST).value()
                if mode == "cap":
                    ball.body_script.set_ult_charge(cost - 1)
                before = pool.total
                self.melee(run, ball, hog)
                self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), amount)
                self.assertEqual(fixture.charge(ball), cost if mode == "cap" else amount * .75)
                self.assertEqual(fixture.weapon(ball).vars[53].value(), 80)
                hit, = ball.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], hog.body)
                if mode == "breather":
                    self.assertGreater(pool.total, before - amount)
                else:
                    self.assertEqual(pool.total, before - amount)
                if mode == "lethal":
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, ball.body)

    def test_roll_wall_range_and_team_reject_actual_melee(self):
        fixture = quad_tests.RoadhogQuadCannonsTests()
        for mode in ("roll", "wall", "range", "team"):
            with self.subTest(mode=mode):
                run, hog, ball, _ = fixture.scene(4 if mode == "range" else 1.7)
                if mode == "roll":
                    run.step({ball: (0, 0, 8)})
                    for _ in range(45):
                        run.step({})
                    self.assertTrue(ball_grapple.rolling(ball))
                elif mode == "wall":
                    x, y, z = ball.mover.position
                    run.match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + box(x - 4, x + 4, y, y + 5, z + .75, z + .85),
                    )
                elif mode == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = ball.team
                self.melee(run, ball, hog)
                self.assertEqual(fixture.pool(run, hog).total, 600)
                self.assertEqual(fixture.charge(ball), 0)
                self.assertFalse(ball.client.messages(combat.HIT))

    def test_projected_barrier_absorbs_melee_without_charge(self):
        fixture = quad_tests.RoadhogQuadCannonsTests()
        run, hog, ball, support = fixture.scene(1.7)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        ball.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, hero))
        x, y, z = hog.mover.position
        fixture.place(support, (x + 7, y, z))
        for _ in range(45):
            run.step({})
        run.step({support: (*fixture.look(run, support, hog, 1.2), 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.melee(run, ball, hog)
        self.assertEqual(bubble.health, 170)
        self.assertEqual(support.body_script.component.entity_var(87).value(), 6)
        self.assertEqual(fixture.pool(run, hog).total, 600)
        self.assertEqual(fixture.charge(ball), 0)

    def test_front_deflect_blocks_melee_but_grasp_does_not_absorb_it(self):
        fixture = quad_tests.RoadhogQuadCannonsTests()
        for name, button, amount in (("Genji", 16, 0), ("Sigma", 8, 30)):
            with self.subTest(name=name):
                run, hog, ball, defender = fixture.scene(6)
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(run.match.switch_hero(defender, hero))
                x, y, z = ball.mover.position
                fixture.place(defender, (x, y, z + 1.7))
                for _ in range(45):
                    run.step({})
                face = fixture.look(run, defender, ball, 1.15)
                run.step({defender: (*face, button)})
                before = fixture.pool(run, defender).total
                look = fixture.look(run, ball, defender, .95)
                run.step({ball: (*look, 0x800), defender: (*face, 0)})
                for _ in range(25):
                    run.step({ball: (*look, 0), defender: (*face, 0)})
                self.assertEqual(fixture.pool(run, defender).total, before - amount)
                self.assertEqual(fixture.charge(ball), amount)
                self.assertEqual(fixture.pool(run, hog).total, 600)
                self.assertFalse(defender.client.messages(combat.HIT))
                if name == "Sigma":
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == 0x1D02)
                    self.assertTrue(grasp.states[1].active)
                    self.assertEqual(grasp.vars[21312].value(), 0)
