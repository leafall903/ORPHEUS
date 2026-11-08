"""Real zero-damage concussion volleys with a reconstructed player impulse adapter."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_combat as primary

from ow174.game import combat
from ow174.game.collision import World


class PharahImpulseTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    health = primary.PharahCombatTests.health
    wall = primary.PharahCombatTests.wall

    def launch(self, run, player, buttons=16):
        run.step({player: (0, 0, buttons)})
        for _ in range(40):
            run.step({})

    def test_zero_damage_concussion_pushes_enemy_and_self_without_hitmarker(self):
        run, player, enemy = self.encounter()
        before_enemy, before_self = enemy.mover.position[2], player.mover.position[2]
        self.launch(run, player)
        self.assertGreater(enemy.mover.position[2], before_enemy + 3)
        self.assertLess(player.mover.position[2], before_self - 3)
        self.assertEqual(self.health(run, enemy).total, 200)
        self.assertEqual(self.health(run, player).total, 200)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_concussion_does_not_push_ally(self):
        run, player, ally = self.encounter()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        before = list(ally.mover.position)
        self.launch(run, player)
        self.assertEqual(ally.mover.position, before)
        self.assertEqual(self.health(run, ally).total, 200)

    def test_wall_blocks_concussion_impulse_to_far_side(self):
        run, player, enemy = self.encounter()
        before = list(enemy.mover.position)
        with patch.object(run.match.combat, "_collision", return_value=self.wall(player.mover.position[1])):
            self.launch(run, player)
        self.assertEqual(enemy.mover.position, before)
        self.assertEqual(self.health(run, enemy).total, 200)

    def test_primary_wall_splash_hurts_self_without_self_hitmarker(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0] = 20
        enemy.mover.body = None
        # Move collision wall close to the shooter without changing its map floor.
        y = player.mover.position[1]
        close = World([
            (-10, y - 5, 1, 10, y - 5, 1, 10, y + 5, 1),
            (-10, y - 5, 1, 10, y + 5, 1, -10, y + 5, 1),
        ])
        with patch.object(run.match.combat, "_collision", return_value=close):
            self.launch(run, player, 1)
        self.assertLess(self.health(run, player).total, 200)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_barrage_does_not_charge_during_its_active_volley_then_resumes(self):
        run, player, _ = self.encounter()
        script = player.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        run.step({player: (0, 0, 32)})
        self.assertTrue(script.ultimate_running())
        for _ in range(60):
            run.step({})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(150):
            run.step({})
        self.assertFalse(script.ultimate_running())
        self.assertGreater(script.component.entity_var(342).value(), 0)


if __name__ == "__main__":
    unittest.main()
