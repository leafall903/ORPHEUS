"""Explosion cover changes actual player/NPC health on the existing combat path."""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import bots
from ow174.game.collision import World
from ow174.game.combat import HitDef, Rocket


class BlastCoverTests(unittest.TestCase):
    def scenario(self, npc=False):
        run = Run(KANEZAKA_DM, ("Shooter", "Front", "Behind"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, front, behind = run.players
        shooter.mover.position = [0.0, 0.0, 0.0]
        front.mover.position = [1.5, 0.0, 0.0]
        behind.mover.position = [1.5, 0.0, 2.0]
        if npc:
            for player, entity in ((front, bots.BOT_ENTITY), (behind, bots.BOT_ENTITY + 1)):
                bot = bots.Bot(entity, {
                    "hero": f"0x{player.hero.guid:X}", "team": 1,
                    "position": tuple(player.mover.position), "rotation": (0, 0, 0, 1), "graphs": [],
                }, {"name": "Cover target", "body": "0x0400000000001403", "health": 200}, {})
                match.bots.append(bot)
                player.spawned = False
        # A wall across z=1. The projectile strikes it; targets stand off-axis
        # so neither target can intercept the direct hit.
        match.collision_world = World([
            (-10, -10, 1, 10, -10, 1, 10, 10, 1),
            (-10, -10, 1, 10, 10, 1, -10, 10, 1),
        ])
        fight = match.combat
        rocket = Rocket(shooter, shooter.team, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
                        100.0, 1.0, HitDef(20.0), HitDef(80.0, rings=((3.0, 1.0),)))
        fight.rockets.append(rocket)
        return run, rocket

    def test_wall_impact_damages_front_but_not_behind_for_players_and_npcs(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, rocket = self.scenario(npc)
                fight = run.match.combat
                fight._fly(rocket)
                targets = fight._enemies(rocket.shooter)
                self.assertEqual([fight.vitals_of(target).total for target in targets], [120.0, 200.0])
                self.assertNotIn(rocket, fight.rockets)

    def test_removing_cover_restores_damage_to_both_targets(self):
        run, rocket = self.scenario()
        fight = run.match.combat
        # Hit an off-axis body to detonate, with the other target nearby.
        run.players[1].mover.position = [0.0, 0.0, 1.0]
        run.match.collision_world = World([])
        fight._fly(rocket)
        self.assertEqual([fight.vitals_of(target).total for target in fight._enemies(rocket.shooter)],
                         [100.0, 120.0])

    def test_npc_projectile_obeys_the_same_cover_for_player_victims(self):
        run, rocket = self.scenario()
        original = rocket.shooter
        original.spawned = False
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": f"0x{original.hero.guid:X}", "team": 1,
            "position": (0, 0, 0), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Shooter", "body": "0x0400000000001403", "health": 200}, {})
        run.match.bots.append(bot)
        rocket.shooter, rocket.team = bot, bot.team
        fight = run.match.combat
        fight._fly(rocket)
        self.assertEqual([fight.vitals_of(target).total for target in fight._enemies(bot)], [120.0, 200.0])

    def test_allies_remain_excluded_from_explosion_damage(self):
        run, rocket = self.scenario()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        for player in run.players:
            player.team = rocket.shooter.team
        fight = run.match.combat
        fight._fly(rocket)
        self.assertEqual([fight.vitals_of(target).total for target in fight._targets()], [200.0] * 3)


if __name__ == "__main__":
    unittest.main()
