"""Actual charged Flux input through the static placement host and authored confirmation path."""

import copy
import unittest

import test_sigma_combat as fixtures
from test_game_combat import aim

from ow174.game import combat, sigma
from ow174.game.commands import CROUCH, JUMP, Command
from ow174.game.script import expr, graph, nodes, runtime


class SigmaFluxPlacementTests(unittest.TestCase):
    def arena(self):
        run, player, enemy, health = fixtures.SigmaCombatTests().arena(offset=0, distance=6)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        instance = next(i for i in script.component.instances.values() if i.graph.index == sigma.FLUX)
        return run, player, enemy, health, instance

    def select(self, run, player, point, *, click=True):
        for frame in range(81):
            yaw, pitch = aim(run.match.combat._eye(player), point)
            buttons = 0x20 if frame == 0 else (1 if click and frame == 80 else 0)
            run.step({player: (yaw, pitch, buttons)})
        return yaw, pitch

    def test_actual_primary_confirmation_captures_a_static_point_and_reaches_the_target_phase(self):
        run, player, enemy, health, instance = self.arena()
        self.select(run, player, tuple(enemy.mover.position))
        point = instance.vars[2478].value()
        self.assertIsInstance(point, expr.Vec3)
        for actual, expected in zip((point.x, point.y, point.z), enemy.mover.position, strict=True):
            self.assertAlmostEqual(actual, expected, delta=0.001)
        self.assertEqual(instance.vars[1883].value(), expr.Entity(0))
        self.assertTrue(instance.vars[1002].value())
        self.assertEqual(instance.vars[59].value(), 2)
        self.assertFalse(instance.states[7].active)
        for _ in range(150):
            run.step({})
        self.assertEqual(instance.states[15].counter, 1)
        self.assertEqual(health.total, 150)

    def test_preview_follows_actual_aim_but_does_not_confirm_without_primary(self):
        run, player, enemy, _health, instance = self.arena()
        self.select(run, player, tuple(enemy.mover.position), click=False)
        self.assertEqual(instance.vars[59].value(), 1)
        x, y, z = enemy.mover.position
        yaw, pitch = aim(run.match.combat._eye(player), (x + 3, y, z))
        run.step({player: (yaw, pitch, 0)})
        point = instance.vars[2478].value()
        origin = run.match.combat._eye(player)
        direction = combat.aim(yaw, pitch)
        distance = (y - origin[1]) / direction[1]  # analytic plane hit for the quantized input
        self.assertAlmostEqual(point.x, origin[0] + distance * direction[0], delta=0.00001)
        self.assertAlmostEqual(point.z, origin[2] + distance * direction[2], delta=0.00001)
        self.assertEqual(instance.vars[59].value(), 1)

    def test_caster_flies_with_input_then_lands_after_selection_timeout(self):
        run, player, enemy, _health, instance = self.arena()
        floor = player.mover.position[1]
        self.select(run, player, tuple(enemy.mover.position), click=False)
        self.assertTrue(instance.states[13].active)
        self.assertEqual(player.mover.gravity_scale, 0)
        hover = player.mover.position[1]
        self.assertGreater(hover - floor, 2)
        for _ in range(10):
            run.step({player: (0, 0, JUMP)})
        raised = player.mover.position[1]
        self.assertGreater(raised - hover, 1)
        for _ in range(10):
            run.step({})
        self.assertEqual(player.mover.position[1], raised)
        for _ in range(10):
            run.step({player: (0, 0, CROUCH)})
        self.assertAlmostEqual(player.mover.position[1], hover, delta=0.01)
        start_z = player.mover.position[2]
        for _ in range(16):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            player.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(player.mover.position[2] - start_z, 1)
        self.assertAlmostEqual(player.mover.values.run_forward, 7.15, places=4)
        for _ in range(430):
            run.step({})
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(instance.states[13].active)
        self.assertEqual(player.mover.gravity_scale, player.mover.data.gravity_scale)
        self.assertAlmostEqual(player.mover.position[1], floor, delta=0.01)
        self.assertFalse(player.mover.airborne)
        self.assertNotIn(375, instance.vars)

    def test_invalid_sky_click_stays_in_selection_and_a_real_ground_retry_confirms(self):
        run, player, enemy, _health, instance = self.arena()
        for frame in range(81):
            run.step({player: (0, -8192, 0x20 if frame == 0 else (1 if frame == 80 else 0))})
        self.assertFalse(instance.vars[1002].value())
        self.assertEqual(instance.vars[59].value(), 1)
        self.assertTrue(instance.states[7].active)
        yaw, pitch = aim(run.match.combat._eye(player), tuple(enemy.mover.position))
        run.step({player: (yaw, pitch, 0)})
        run.step({player: (yaw, pitch, 1)})
        self.assertEqual(instance.vars[59].value(), 2)

    def test_authored35m_ray_rejects_far_ground_and_accepts_nearer_ground(self):
        for distance, valid in ((40, False), (30, True)):
            with self.subTest(distance=distance):
                run, player, _enemy, _health, instance = self.arena()
                x, y, z = player.mover.position
                self.select(run, player, (x, y, z + distance))
                self.assertEqual(instance.vars[1002].value(), valid)
                self.assertEqual(instance.vars[59].value(), 2 if valid else 1)

    def test_a_nearer_wall_prevents_confirming_the_ground_beyond_it(self):
        run, player, enemy, _health, instance = self.arena()
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
        self.select(run, player, tuple(enemy.mover.position))
        self.assertFalse(instance.vars[1002].value())
        self.assertEqual(instance.vars[59].value(), 1)

    def test_position_query_only_handles_declared_absent_static_entity_and_exact_selector(self):
        run, player, enemy, _health, instance = self.arena()
        self.select(run, player, tuple(enemy.mover.position), click=False)
        cfg = graph.graph(sigma.FLUX).node(77).field("m_value")
        world = player.body_script.world
        self.assertEqual(sigma.flux_position_query(world, cfg, instance), instance.vars[2478].value())
        altered = copy.deepcopy(cfg)
        altered["m_entity"]["m_identifier"] = 99
        self.assertIs(sigma.flux_position_query(world, altered, instance), sigma.NOT_HANDLED)
        instance.write((graph.INSTANCE, 1883), expr.Entity(enemy.body))
        self.assertIs(sigma.flux_position_query(world, cfg, instance), sigma.NOT_HANDLED)
        instance.write((graph.INSTANCE, 1883), None)
        self.assertIs(sigma.flux_position_query(world, cfg, instance), sigma.NOT_HANDLED)

    def test_strict_native_execution_refuses_the_reconstructed_placement_service(self):
        _run, _player, _enemy, _health, instance = self.arena()
        state = instance.state(7)
        instance.component.strict = True
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "native placement service"):
            state.begin()


if __name__ == "__main__":
    unittest.main()
