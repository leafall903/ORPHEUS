"""Actual Soundwave impulse expression only; target impulse application is open."""
import math
import unittest

import test_lucio_combat as fixtures

from ow174.game import lucio_movement
from ow174.game.script import expr


class LucioSoundwaveImpulseTests(unittest.TestCase):
    def scenario(self):
        _, player, _, _ = fixtures.LucioCombatTests().scenario()
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x0776)
        config = instance.state(11).node.field("m_modifyHealth.m_1376B4DF")
        self.assertIsNotNone(config)
        return player, instance, config

    def test_actual_impulse_expression_uses_authored_seventeen_and_ruleset(self):
        player, instance, config = self.scenario()
        player.mover.yaw, player.mover.pitch = 0, 0
        value = instance.evaluate(config)
        self.assertIsInstance(value, expr.Vec3)
        self.assertAlmostEqual(math.sqrt(sum(axis * axis for axis in value)), 17., delta=2e-5)
        self.assertAlmostEqual(value.y / value.z, .5, delta=2e-6)
        self.assertEqual(instance.component.world.ruleset(10615), 1.)

    def test_actual_direction_clamps_down_and_up_aim_to_twenty_and_seventy_degrees(self):
        player, instance, config = self.scenario()
        player.mover.yaw = 8192
        for pitch, elevation in ((16000,20.), (-16000,70.)):
            with self.subTest(pitch=pitch):
                player.mover.pitch = pitch
                value = instance.evaluate(config)
                self.assertAlmostEqual(value.x, value.z, delta=2e-5)
                self.assertAlmostEqual(value.y, 17. * math.sin(math.radians(elevation)), delta=2e-5)
                self.assertAlmostEqual(math.hypot(value.x,value.z),
                                       17. * math.cos(math.radians(elevation)), delta=2e-5)

    def test_expression_does_not_mutate_a_mover_or_enqueue_a_target_impulse(self):
        player, instance, config = self.scenario()
        velocity = player.mover.velocity
        instance.evaluate(config)
        self.assertEqual(player.mover.velocity, velocity)
        self.assertEqual(player.mover.velocity_changes, [])

    def test_unproved_limiter_parameters_defer_to_existing_unsupported_policy(self):
        _, instance, config = self.scenario()
        limiter = config["m_configVars"][0]
        altered = dict(limiter, m_E26E5E09={"$":"STUConfigVarFloat", "m_value":30.})
        self.assertIs(lucio_movement.query(instance.component.world, altered, instance), NotImplemented)


if __name__ == '__main__':
    unittest.main()
