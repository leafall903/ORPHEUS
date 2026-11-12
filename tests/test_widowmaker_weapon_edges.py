"""Executed Widowmaker miss/range/LOS/reload gates, using actual weapon graphs."""

import unittest
from unittest.mock import patch

from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.script import graph

RELOAD = 0x400  # logical14, from the target-build command edge mapping

class WidowmakerWeaponEdgeTests(_WidowmakerMatch, unittest.TestCase):
    def test_declared_scoped_residual_ammo_policy_then_reload_recovers(self):
        # Current host uses floor(ammo/5):1..4 consume the residue without a hit.
        # Native count-leaf arithmetic is known; original first-shot dispatch is not.
        for ammo in range(1, 5):
            with self.subTest(ammo=ammo):
                self.setUp()
                self.press(SCOPE, 100)
                self.weapon().write((graph.INSTANCE, 53), ammo)
                self.press(SCOPE | FIRE)
                self.press(SCOPE, 40)
                self.assertEqual(self.weapon().vars[53].value(), 0)
                self.assertEqual(self.health(self.b), 200)
                self.assertEqual(self.a.client.messages(combat.HIT), [])
                self.press(RELOAD)
                self.press(0, 200)
                self.assertEqual(self.weapon().vars[53].value(), 35)
                self.press(SCOPE, 100)
                self.press(SCOPE | FIRE)
                self.assertEqual(self.health(self.b), 80)

    def place_enemy(self, distance):
        self.b.mover.position[:] = (0, 0, distance)
        self.b.mover.body = None
        self.b.mover.velocity = (0, 0, 0)
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.run.step({})

    def weapon(self):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)

    def test_primary_far_falloff_and_out_of_range(self):
        self.place_enemy(60)
        launch = self.weapon().graph.node(9).field('m_5EC1EB38')
        # Isolate the damage falloff policy; actual cone rays have separate tests.
        with patch.dict(launch, m_7F0FED02={'$': 'STUConfigVarInt', 'm_value': 0},
                        m_429A6C85={'$': 'STUConfigVarInt', 'm_value': 0}):
            self.press(FIRE)
        self.assertAlmostEqual(200 - self.health(self.b), 3.9, places=5)
        self.setUp()
        self.place_enemy(120)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_scoped_far_falloff_and_out_of_range(self):
        self.place_enemy(150)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertAlmostEqual(200 - self.health(self.b), 36, places=4)
        self.setUp()
        self.place_enemy(450)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(self.health(self.b), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_static_wall_blocks_both_weapon_modes(self):
        self.run.match.collision_world = World([
            (-5, 0, -5, -5, 0, 5, 5, 0, 5),
            (-5, 0, -5, 5, 0, 5, 5, 0, -5),
            (-5, 0, 0.75, 5, 0, 0.75, 5, 5, 0.75),
            (-5, 0, 0.75, 5, 5, 0.75, -5, 5, 0.75),
        ], name="weapon LOS")
        self.press(FIRE)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(self.health(self.b), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_empty_clip_rejects_fire_then_actual_reload_restores_damage(self):
        self.weapon().write((graph.INSTANCE, 53), 0)
        self.press(FIRE, 10)
        self.assertEqual(self.health(self.b), 200)
        self.press(RELOAD)
        self.press(0, 200)
        self.assertEqual(self.weapon().vars[53].value(), 35)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 187)

    def test_reload_interrupts_fire_and_rejects_fire_until_it_finishes(self):
        self.press(FIRE)
        self.press(RELOAD)
        self.press(FIRE, 30)
        self.assertEqual(self.health(self.b), 187)
        self.press(0, 200)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 174)

    def test_aim_miss_and_melee_range_and_dead_source(self):
        self.run.step({self.a: (32767, 0, FIRE)})
        self.assertEqual(self.health(self.b), 200)
        self.place_enemy(5)
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 200)
        self.run.match.combat._damage(self.b, self.target(self.a), 500, False, None, self.run.frame)
        self.place_enemy(1.5)
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))


if __name__ == "__main__":
    unittest.main()
