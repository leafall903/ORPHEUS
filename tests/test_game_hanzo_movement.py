"""Real command-path Lunge with an explicitly reconstructed mover bridge."""

import math
import unittest
from unittest.mock import patch

import test_game_hanzo as hero_tests
from test_game_mover import box, wall

from ow174.game import bodytrace, hanzo_movement, movelog
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import runtime


class HanzoLungeTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, hero, victim = hero_tests.HanzoCombatTests().arena()
        instance = next(i for i in hero.body_script.component.instances.values()
                        if i.graph.index == 0x010C)
        return run, hero, victim, instance

    @staticmethod
    def step(run, hero, **values):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for player in run.players:
            player.take_commands([Command(run.frame, **(values if player is hero else {}))])

    def arm(self, run, hero):
        self.step(run, hero, buttons=JUMP)
        for _ in range(14):
            self.step(run, hero)

    def test_authored_direction_speed_and_actual_displacement(self):
        cases = ((0, 0, 0, (0, 15)), (16384, 0, 0, (15, 0)),
                 (0, 0, 127, (-15, 0)), (0, -127, 0, (0, -15)),
                 (0, 127, 127, (-15 / math.sqrt(2), 15 / math.sqrt(2))))
        for yaw, forward, right, expected in cases:
            with self.subTest(yaw=yaw, forward=forward, right=right):
                run, hero, _, instance = self.arena()
                self.arm(run, hero)
                before = tuple(hero.mover.position)
                self.step(run, hero, buttons=JUMP, yaw=yaw, pitch=-15000,
                          forward=forward, right=right)
                vx, vy, vz = hero.mover.velocity
                self.assertAlmostEqual(vx, expected[0], places=4)
                self.assertAlmostEqual(vz, expected[1], places=4)
                self.assertEqual(vy, 4.5)
                self.assertTrue(instance.vars[1843].value())
                self.assertFalse(instance.vars[9570].value())
                self.assertEqual(hero.mover.history[-1].velocity, hero.mover.velocity)
                start = tuple(hero.mover.position)
                self.step(run, hero, yaw=yaw)
                moved = tuple(b - a for a, b in zip(start, hero.mover.position, strict=True))
                self.assertGreater(moved[0] * expected[0] + moved[2] * expected[1], 3)
                self.assertGreater(hero.mover.position[1], before[1])

    def test_held_ground_jump_and_early_repress_do_not_lunge(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, hero, _, instance = self.arena()
                self.step(run, hero, buttons=JUMP)
                for frame in range(20):
                    self.step(run, hero, buttons=JUMP if held or frame == 1 else 0)
                self.assertNotIn(1790, instance.vars)
                self.assertEqual(hero.mover.velocity[0::2], (0.0, 0.0))
                self.assertEqual(instance.vars[61].value(), 0)
                self.step(run, hero)
                self.step(run, hero, buttons=JUMP)
                self.assertEqual(hero.mover.velocity, (0.0, 4.5, 15.0))

    def test_cooldown_rejects_early_edges_then_fresh_airborne_press_reuses(self):
        run, hero, _, instance = self.arena()
        world = hero.body_script.world
        with patch.object(world, "velocity_action", wraps=world.velocity_action) as launch:
            self.arm(run, hero)
            self.step(run, hero, buttons=JUMP)
            for _ in range(8):
                self.step(run, hero)
            self.assertGreater(instance.vars[61].value(), 4.9)
            self.assertLessEqual(instance.vars[61].value(), 5)
            for frame in range(90):
                self.step(run, hero, buttons=JUMP if frame % 5 == 0 else 0)
            self.assertEqual(launch.call_count, 1)
            for _ in range(400):
                if instance.vars[61].value() <= 0:
                    break
                self.step(run, hero)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertEqual(launch.call_count, 1)
            # Ensure a fresh grounded take-off instead of reusing an expired edge.
            for _ in range(80):
                self.step(run, hero)
                if not hero.mover.flags_word & hanzo_movement.IN_AIR:
                    break
            self.arm(run, hero)
            self.step(run, hero, buttons=JUMP)
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(hero.mover.velocity, (0.0, 4.5, 15.0))

    def test_launched_body_collides_with_static_wall_and_returns_to_ground(self):
        run, hero, victim, _ = self.arena()
        victim.spawned = False
        x, y, z = hero.mover.position
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 5, z + 2, x + 5, z + 2, y0=y, y1=y + 8),
            name="declared Lunge floor and wall fixture",
        )
        self.arm(run, hero)
        self.step(run, hero, buttons=JUMP)
        for _ in range(100):
            self.step(run, hero)
        self.assertGreater(hero.mover.position[2], z + 1)
        self.assertLess(hero.mover.position[2], z + 1.51)
        self.assertAlmostEqual(hero.mover.position[1], y, places=2)
        self.assertFalse(hero.mover.flags_word & hanzo_movement.IN_AIR)
        self.assertEqual(hero.mover.velocity, (0.0, 0.0, 0.0))

    def test_launch_uses_authored_ruleset_scalar(self):
        run, hero, _, _ = self.arena()
        world = hero.body_script.world
        ruleset = world.ruleset
        with patch.object(world, "ruleset", side_effect=lambda key: 0.5 if key == 14960 else ruleset(key)):
            self.arm(run, hero)
            self.step(run, hero, buttons=JUMP)
        self.assertEqual(hero.mover.velocity, (0.0, 2.25, 7.5))

    def test_dead_hanzo_cannot_launch_and_soldier_does_not_gain_lunge(self):
        run, hero, victim, _ = self.arena()
        self.arm(run, hero)
        target = next(t for t in run.match.combat._targets() if t.owner is hero)
        run.match.combat._damage(victim, target, 200, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(hero))
        pose = tuple(hero.mover.position)
        self.step(run, hero, buttons=JUMP)
        self.assertEqual(tuple(hero.mover.position), pose)
        self.assertNotEqual(hero.mover.velocity, (0, 4.5, 15))
        run, hero, _, _ = self.arena()
        run.match.switch_hero(hero, heroes()[SOLDIER])
        self.arm(run, hero)
        self.step(run, hero, buttons=JUMP)
        self.assertEqual(hero.mover.velocity[0::2], (0.0, 0.0))

    def test_strict_mode_refuses_reconstructed_launch_and_unknown_rows_defer(self):
        _run, hero, _, instance = self.arena()
        node = next(node for node in instance.graph.nodes if node.pos == 82)
        velocity = hero.mover.velocity
        with patch.dict(node.fields, {"m_BC5E91CF": [{"$": "STU_UNKNOWN_VELOCITY"}]}):
            self.assertIs(hero.body_script.world.velocity_action(instance, node), NotImplemented)
        instance.component.strict = True
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "reconstructed Hanzo launch"):
            instance.run_node(node, None)
        self.assertEqual(hero.mover.velocity, velocity)
