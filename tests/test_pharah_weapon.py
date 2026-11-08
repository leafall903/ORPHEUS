"""Ammo/reload, range/miss and melee behavior through Pharah's authored graphs."""

import unittest
from unittest.mock import patch

import test_pharah_combat as primary

from ow174.game.collision import World


class PharahWeaponTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    health = primary.PharahCombatTests.health

    def step(self, run, player, frames, buttons=0):
        for _ in range(frames):
            run.step({player: (0, 0, buttons)})

    def weapon(self, player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x23)

    def relocate(self, player, *, x=0, z=8):
        player.mover.position[0] = x
        player.mover.position[2] = z
        player.mover.body = None

    def test_six_rockets_exhaust_magazine_then_auto_reload_without_extra_shot(self):
        run, player, enemy = self.encounter()
        self.relocate(enemy, x=20)
        weapon = self.weapon(player)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.step(run, player, 300, 1)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(weapon.states[6].counter, 6)
        self.step(run, player, 50, 1)
        self.assertEqual(weapon.states[6].counter, 6)
        self.assertEqual(self.health(run, enemy).total, 200)
        self.step(run, player, 100, 1)
        self.assertGreater(weapon.states[6].counter, 6)

    def test_manual_reload_refills_after_delay_and_does_not_fire_on_its_own(self):
        run, player, enemy = self.encounter()
        self.relocate(enemy, x=20)
        weapon = self.weapon(player)
        self.step(run, player, 1, 1)
        self.step(run, player, 65)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.step(run, player, 1, 0x400)
        self.step(run, player, 20)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.step(run, player, 140)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.assertEqual(weapon.states[6].counter, 1)

    def test_primary_cannot_damage_capsule_beyond_authored_100m_range(self):
        run, player, enemy = self.encounter()
        self.relocate(enemy, z=101)
        self.step(run, player, 1, 1)
        self.step(run, player, 200)
        self.assertEqual(self.health(run, enemy).total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_melee_hits_once_for_30_then_new_press_can_hit_after_recovery(self):
        run, player, enemy = self.encounter()
        self.relocate(enemy, z=1.8)
        self.step(run, player, 1, 0x800)
        self.step(run, player, 70)
        self.assertEqual(self.health(run, enemy).total, 170)
        self.step(run, player, 1, 0x800)
        self.step(run, player, 70)
        self.assertEqual(self.health(run, enemy).total, 140)

    def test_melee_miss_and_static_wall_do_not_deal_damage(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, player, enemy = self.encounter()
                if blocked:
                    self.relocate(enemy, z=1.8)
                y = player.mover.position[1]
                wall = World([
                    (-10, y - 5, 1, 10, y - 5, 1, 10, y + 5, 1),
                    (-10, y - 5, 1, 10, y + 5, 1, -10, y + 5, 1),
                ]) if blocked else run.match.collision_world
                with patch.object(run.match.combat, "_collision", return_value=wall):
                    self.step(run, player, 1, 0x800)
                    self.step(run, player, 45)
                self.assertEqual(self.health(run, enemy).total, 200)


if __name__ == "__main__":
    unittest.main()
