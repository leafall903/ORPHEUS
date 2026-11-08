"""Tracer damage acceptance through actual build-104319 graphs and player input.

These server fixtures do not establish spread, abilities or original-client acceptance.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats
from ow174.game.commands import FIRE
from ow174.game.content import heroes

TRACER = 0x02E0000000000003


class TracerPistolTests(unittest.TestCase):
    def fight(self):
        run = Run(KANEZAKA_DM, ("Tracer", "Enemy"))
        player, enemy = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[TRACER])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 5]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        return run, player, enemy, fight.vitals_of(target)

    def fire(self, run, player, enemy, frames=1):
        yaw, pitch = aim(run.match.combat._eye(player),
                         (enemy.mover.position[0], enemy.mover.position[1] + 1,
                          enemy.mover.position[2]))
        for _ in range(frames):
            run.step({player: (yaw, pitch, FIRE)})

    def test_one_input_shot_deals_two_six_damage_bullets(self):
        run, player, enemy, vitals = self.fight()
        self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 188)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 12)
        self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_armour_is_applied_to_each_bullet(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 200, armour=50)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 44)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_bullets_cross_shields_into_health(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 200, shields=5)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 193)

    def test_first_lethal_bullet_emits_one_death(self):
        run, player, enemy, _ = self.fight()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 5)
        self.fire(run, player, enemy)
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
        self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(len(enemy.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 5)

    def test_full_magazine_deals_forty_bullets(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy, frames=61)
        self.assertEqual(vitals.total, 760)
        self.assertEqual(len(player.client.messages(combat.HIT)), 40)

    def test_wall_stops_both_bullets(self):
        run, player, enemy, vitals = self.fight()
        with patch.object(combat, "ray_world", return_value=0.1):
            self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_same_team_is_not_damaged(self):
        run, player, enemy, vitals = self.fight()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = player.team
        self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 200)

    def test_reload_restores_the_next_pair_of_bullets(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy, frames=61)
        for frame in range(90):
            run.step({player: (0, 0, 0x400 if frame == 1 else 0)})
        self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 748)
        self.assertEqual(len(player.client.messages(combat.HIT)), 42)

    def test_quick_melee_deals_thirty_once(self):
        run, player, enemy, vitals = self.fight()
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + 1.5))
        run.step({player: (yaw, pitch, 0x800)})
        for _ in range(40):
            run.step({player: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 170)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_enemy_player_fire_damages_tracer(self):
        run, player, enemy, _ = self.fight()
        self.fire(run, enemy, player)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 130)

    @patch("ow174.game.script.graph.entity_body", return_value=None)
    def test_pair_kills_a_training_npc_and_retains_its_corpse(self, _entity_body):
        run = Run()
        player, = run.players
        run.match.switch_hero(player, heroes()[TRACER])
        bot = next(bot for bot in run.match.bots if bot.team != player.team)
        run.match.bots[:] = [bot]
        run.match._pending_bot_scripts.clear()
        bot.body_script = None
        bot.route = []
        bot.firing = False
        bot.retain_corpse = True
        bot.health = 11
        x, y, z = player.mover.position
        bot.position = (x, y, z + 5)
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + 5))
        run.step({player: (yaw, pitch, FIRE)})
        self.assertTrue(bot.dead)
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 0)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 11)
        for _ in range(200):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))


if __name__ == "__main__":
    unittest.main()
