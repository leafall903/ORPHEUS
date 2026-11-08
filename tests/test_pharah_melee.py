"""Actual melee inputs at the reconstructed sphere/capsule boundary; no native hitbox claim."""

import math
import unittest
from dataclasses import replace

import test_pharah_weapon as weapon


class PharahMeleeTests(unittest.TestCase):
    encounter = weapon.PharahWeaponTests.encounter
    health = weapon.PharahWeaponTests.health
    relocate = weapon.PharahWeaponTests.relocate
    step = weapon.PharahWeaponTests.step

    def test_melee_excludes_friendly_player_and_gives_no_charge_or_hit_notice(self):
        run, player, ally = self.encounter()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        self.relocate(ally, z=1.8)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        self.step(run, player, 1, 0x800)
        self.step(run, player, 70)
        self.assertEqual(self.health(run, ally).total, 200)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(player.client.messages(25001))

    def test_reconstructed_sphere_capsule_front_boundary_and_targets_behind(self):
        # This brackets the executable adapter, not retail TrackTargets geometry.
        for offset, expected in ((-0.002, 170), (0.002, 200), (None, 200)):
            with self.subTest(offset=offset):
                run, player, enemy = self.encounter()
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                eye_y = run.match.combat._eye(player)[1]
                a, b = target.segment()
                vertical = max(a[1] - eye_y, eye_y - b[1], 0)
                boundary = 1.5 + math.sqrt((1.0 + target.radius) ** 2 - vertical ** 2)
                self.relocate(enemy, z=-1.8 if offset is None else boundary + offset)
                # Populate view history at this pose before the attack.
                self.step(run, player, 3)
                self.step(run, player, 1, 0x800)
                self.step(run, player, 20)
                self.assertEqual(self.health(run, enemy).total, expected)

    def test_holding_button_does_not_repeat_and_spam_cannot_bypass_recovery(self):
        for held in (True, False):
            with self.subTest(held=held):
                run, player, enemy = self.encounter()
                self.relocate(enemy, z=1.8)
                for frame in range(60):
                    self.step(run, player, 1, 0x800 if held or frame % 2 == 0 else 0)
                self.assertEqual(self.health(run, enemy).total, 170)
                for frame in range(60, 80):
                    self.step(run, player, 1, 0x800 if held or frame % 2 == 0 else 0)
                self.assertEqual(self.health(run, enemy).total, 170 if held else 140)

    def test_death_ends_active_melee_and_rejects_new_melee_presses(self):
        run, player, enemy = self.encounter()
        self.relocate(enemy, z=1.8)
        self.step(run, player, 1, 0x800)
        self.assertEqual(self.health(run, enemy).total, 170)
        script = player.body_script
        melee = next(i for i in script.component.instances.values() if i.graph.index == 0x43)
        self.assertTrue(melee.states[4].active)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        self.assertTrue(script.component.owner_dead)
        self.assertFalse(melee.states[4].active)
        for frame in range(90):
            self.step(run, player, 1, 0x800 if frame % 2 == 0 else 0)
        self.assertEqual(self.health(run, enemy).total, 170)


if __name__ == "__main__":
    unittest.main()
