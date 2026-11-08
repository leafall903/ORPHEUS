"""Pharah's extracted primary weapon through real Match commands; no live-client claim."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import bots, combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes

PHARAH = 0x02E0000000000008


class PharahCombatTests(unittest.TestCase):
    def encounter(self):
        run = Run(KANEZAKA_DM, ("Pharah", "Enemy"))
        shooter, victim = run.players
        run.match.switch_hero(shooter, heroes()[PHARAH])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        y = shooter.mover.position[1]
        shooter.mover.position = [0.0, y, 0.0]
        victim.mover.position = [0.0, y, 8.0]
        for _ in range(30):
            run.step({})
        return run, shooter, victim

    def fire_once(self, run, shooter):
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(60):
            run.step({})

    def health(self, run, owner):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target)

    def test_primary_is_40_direct_plus_80_splash_without_headshot(self):
        run, shooter, victim = self.encounter()
        self.fire_once(run, shooter)
        self.assertEqual(self.health(run, victim).total, 80.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [40, 80])
        self.assertTrue(all(h["+0x78"]["+0x8"] == 0 for h in hits))
        self.assertFalse(run.match.combat.rockets)

    def test_second_primary_kills_and_dead_body_cannot_fire(self):
        run, shooter, victim = self.encounter()
        self.fire_once(run, shooter)
        self.fire_once(run, shooter)
        self.assertTrue(self.health(run, victim).dead)
        self.assertEqual(self.health(run, victim).total, 0.0)
        before = self.health(run, shooter).total
        for _ in range(20):
            run.step({victim: (-32768, 0, FIRE)})
        self.assertEqual(self.health(run, shooter).total, before)

    def test_enemy_player_rifle_damages_pharah(self):
        run, pharah, enemy = self.encounter()
        for _ in range(8):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertLess(self.health(run, pharah).total, 200.0)
        self.assertTrue(enemy.client.messages(combat.HIT))

    def test_primary_consumes_real_reinhardt_armor_and_zenyatta_shield_pools(self):
        for hero_id, expected in ((0x02E0000000000007, 390.0), (0x02E0000000000020, 80.0)):
            with self.subTest(hero=hero_id):
                run, shooter, victim = self.encounter()
                run.match.switch_hero(victim, heroes()[hero_id])
                victim.mover.position = [0, shooter.mover.position[1], 8]
                victim.mover.body = None
                self.fire_once(run, shooter)
                self.assertEqual(self.health(run, victim).total, expected)

    def test_primary_passes_allies_without_dealing_damage(self):
        run, shooter, ally = self.encounter()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = shooter.team
        self.fire_once(run, shooter)
        self.assertEqual(self.health(run, ally).total, 200.0)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_primary_damages_retained_training_npc_and_preserves_corpse(self):
        run, shooter, victim = self.encounter()
        victim.mover.position[0] = 20.0
        victim.mover.body = None
        hero_key, definition = next(iter(bots.table()["heroes"].items()))
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": hero_key, "team": 1, "position": (0.0, shooter.mover.position[1], 8.0),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, definition, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        self.fire_once(run, shooter)
        self.assertLess(self.health(run, bot).total, bot.health)
        self.fire_once(run, shooter)
        self.assertTrue(bot.dead)
        for _ in range(round(combat.BOT_RESPAWN / combat.TICK) + 5):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertEqual(self.health(run, bot).total, 0.0)

    def wall(self, y):
        return World([
            (-10, y - 5, 5, 10, y - 5, 5, 10, y + 5, 5),
            (-10, y - 5, 5, 10, y + 5, 5, -10, y + 5, 5),
        ])

    def test_wall_impact_splash_cannot_damage_enemy_behind_wall(self):
        run, shooter, victim = self.encounter()
        victim.mover.position[2] = 5.8  # inside blast radius, fully behind the wall
        victim.mover.body = None
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=self.wall(shooter.mover.position[1])):
            self.fire_once(run, shooter)
        self.assertEqual(self.health(run, victim).total, 200.0)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_wall_impact_still_damages_enemy_on_incoming_side(self):
        run, shooter, victim = self.encounter()
        # Off the projectile axis: hit the wall first, then splash this nearby victim.
        victim.mover.position[0] = 1.2
        victim.mover.position[2] = 4.3
        victim.mover.body = None
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=self.wall(shooter.mover.position[1])):
            self.fire_once(run, shooter)
        self.assertLess(self.health(run, victim).total, 200.0)
        self.assertGreater(self.health(run, victim).total, 120.0)


if __name__ == "__main__":
    unittest.main()
