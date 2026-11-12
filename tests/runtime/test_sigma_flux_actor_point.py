"""Actual Flux candidate config has vector output and explicit live pose binding."""

import copy
import unittest

import test_sigma_combat as fixtures

from ow174.game import sigma_flux as sigma
from ow174.game.commands import CROUCH
from ow174.game.content import heroes
from ow174.game.script import expr


class SigmaFluxActorPointTests(unittest.TestCase):
    def fixture(self):
        run, player, enemy, health = fixtures.SigmaCombatTests().arena(offset=0, distance=6)
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == sigma.FLUX)
        return run, player, enemy, health, instance, instance.graph.node(37).field('m_48837057')

    def test_real_candidate_scope_outputs_position_vector_not_entity(self):
        run, _player, enemy, _health, instance, cfg = self.fixture()
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        with instance.component.parameter_scope({92: expr.Entity(enemy.body)}):
            point = instance.evaluate(cfg)
        self.assertIsInstance(point, expr.Vec3)
        self.assertEqual(point, expr.Vec3(expr.f32(target.feet[0]),
                                        expr.f32(expr.f32(target.feet[1]) + expr.f32(target.eye)),
                                        expr.f32(target.feet[2])))
        self.assertIsNone(instance.context(None).param(92))

    def test_current_pose_and_crouch_height_are_read_each_time(self):
        run, player, enemy, _health, instance, cfg = self.fixture()
        world = player.body_script.world
        with instance.component.parameter_scope({92: expr.Entity(enemy.body)}):
            before = sigma.flux_actor_point_query(world, cfg, instance)
        enemy.mover.position[0] += 3
        for _ in range(20):
            run.step({enemy: (0, 0, CROUCH)})
        self.assertTrue(enemy.mover.crouched)
        with instance.component.parameter_scope({92: expr.Entity(enemy.body)}):
            after = sigma.flux_actor_point_query(world, cfg, instance)
        self.assertAlmostEqual(after.x - before.x, 3, delta=0.001)
        self.assertLess(after.y, before.y)
        self.assertEqual(after.z, before.z)

    def test_missing_candidate_fails_instead_of_fabricating_an_origin_point(self):
        _run, player, _enemy, _health, instance, cfg = self.fixture()
        for value in (None, expr.Entity(0), expr.Entity(0xA0007777)):
            with self.subTest(value=value), instance.component.parameter_scope({92: value}):
                self.assertIs(sigma.flux_actor_point_query(player.body_script.world, cfg, instance),
                              expr.EVALUATION_FAILED)

    def test_body_replacement_retires_the_previous_lookup(self):
        run, player, enemy, _health, instance, cfg = self.fixture()
        old = enemy.body
        run.match.switch_hero(enemy, heroes()[sigma.HERO])
        self.assertNotEqual(enemy.body, old)
        with instance.component.parameter_scope({92: expr.Entity(old)}):
            self.assertIs(sigma.flux_actor_point_query(player.body_script.world, cfg, instance),
                          expr.EVALUATION_FAILED)

    def test_position_query_does_not_apply_a_team_or_health_filter(self):
        run, player, enemy, health, instance, cfg = self.fixture()
        enemy.team = player.team
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        run.match.combat._damage(player, target, 1000, False, None, run.frame)
        self.assertTrue(health.dead)
        with instance.component.parameter_scope({92: expr.Entity(enemy.body)}):
            self.assertIsInstance(sigma.flux_actor_point_query(player.body_script.world, cfg, instance),
                                  expr.Vec3)

    def test_other_selectors_and_strict_native_mode_are_unhandled(self):
        _run, player, _enemy, _health, instance, cfg = self.fixture()
        altered = copy.deepcopy(cfg)
        altered['m_entity'] = {'$': 'STU_B5A0CAF0'}
        self.assertIs(sigma.flux_actor_point_query(player.body_script.world, altered, instance),
                      sigma.NOT_HANDLED)
        world = player.body_script.world
        body, world.body = world.body, 0xA0007777
        self.assertIs(sigma.flux_actor_point_query(world, cfg, instance), sigma.NOT_HANDLED)
        world.body = body
        instance.component.strict = True
        self.assertIs(sigma.flux_actor_point_query(player.body_script.world, cfg, instance),
                      sigma.NOT_HANDLED)


if __name__ == '__main__':
    unittest.main()
