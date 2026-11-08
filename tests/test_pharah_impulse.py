"""Real zero-damage concussion volleys with a reconstructed player impulse adapter."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_combat as primary

from ow174.game import combat, stats
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

    def test_concussion_miss_has_no_impulse_and_expires_at_range(self):
        run, player, enemy = self.encounter()
        enemy.mover.position[0] = 20
        enemy.mover.body = None
        start = list(player.mover.position)
        self.launch(run, player)
        for _ in range(120):
            run.step({})
        self.assertEqual(player.mover.position, start)
        self.assertEqual(self.health(run, enemy).total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_concussion_cooldown_rejects_repress_then_allows_new_knockback(self):
        run, player, enemy = self.encounter()
        self.launch(run, player)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x7B)
        self.assertGreater(instance.vars[61].value(), 8)
        self.assertEqual(instance.states[0].counter, 1)
        self.launch(run, player)
        self.assertEqual(instance.states[0].counter, 1)
        for _ in range(600):
            run.step({})
        self.assertEqual(instance.vars[61].value(), 0)
        before = enemy.mover.position[2]
        self.launch(run, player)
        self.assertEqual(instance.states[0].counter, 2)
        self.assertGreater(enemy.mover.position[2], before + 1)

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
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)

    def test_barrage_does_not_charge_during_its_active_volley_then_resumes(self):
        run, player, enemy = self.encounter()
        script = player.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        run.step({player: (0, 0, 32)})
        self.assertTrue(script.ultimate_running())
        for _ in range(60):
            run.step({})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health(run, enemy).total, 0)
        for _ in range(150):
            run.step({})
        self.assertFalse(script.ultimate_running())
        self.assertGreater(script.component.entity_var(342).value(), 0)

    def test_barrage_rejects_insufficient_charge_without_spending_or_damage(self):
        run, player, enemy = self.encounter()
        script = player.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(338).value() - 1
        script.set_ult_charge(charge)
        run.step({player: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertFalse(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), charge)
        self.assertEqual(self.health(run, enemy).total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_death_cancels_barrage_and_blocks_further_shots(self):
        run, player, enemy = self.encounter()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        self.assertTrue(script.ultimate_running())
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x2B)
        volley = instance.states[1]
        reported = volley.reported
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        for _ in range(30):
            run.step({player: (0, 0, 32)})
        self.assertFalse(script.ultimate_running())
        self.assertEqual(volley.reported, reported)


if __name__ == "__main__":
    unittest.main()
