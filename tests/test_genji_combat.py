"""Command-path Genji shuriken damage; not original-client hero acceptance."""

import math
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from test_game_mover import FLOOR, wall

from ow174.game import bodytrace, bots, combat, movelog
from ow174.game.collision import World
from ow174.game.content import heroes

GENJI = 0x02E0000000000029


class GenjiFixture(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))

    def spawn(self, distance=0.8, names=("Genji", "Enemy")):
        run = Run(KANEZAKA_DM, names)
        run.match.bots.clear()
        player = run.players[0]
        run.match.switch_hero(player, heroes()[GENJI])
        player.mover.position[:] = [0.0, 1.0, 0.0]
        for enemy in run.players[1:]:
            enemy.mover.position[:] = [0.0, 1.0, distance]
        return run, player

    def fire(self, run, player, button):
        for frame in range(60):
            run.step({player: (0, 0, button if frame == 0 else 0)})

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total


class GenjiShurikenTests(GenjiFixture):
    def test_primary_burst_deals_three_hits(self):
        run, player = self.spawn(4.0)
        self.fire(run, player, 1)
        self.assertEqual(self.health(run, run.players[1]), 113.0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_secondary_point_blank_deals_three_independent_hits(self):
        run, player = self.spawn()
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 113.0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_secondary_distant_narrow_target_only_receives_centre(self):
        run, player = self.spawn(10.0)
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 171.0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_secondary_fan_can_hit_three_different_enemies(self):
        run, player = self.spawn(10.0, ("Genji", "Left", "Centre", "Right"))
        for enemy, degrees in zip(run.players[1:], (-9, 0, 9), strict=True):
            enemy.mover.position[0] = math.tan(math.radians(degrees)) * 10
        self.fire(run, player, 2)
        self.assertEqual([self.health(run, p) for p in run.players[1:]], [171.0] * 3)

    def test_secondary_armour_mitigates_each_shuriken(self):
        run, player = self.spawn()
        enemy = run.players[1]
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200, 100)
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, enemy), 228.0)  # 3 * (29 - 5), not 87 - 5

    def test_secondary_respects_team_semantics(self):
        run, player = self.spawn()
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        run.players[1].team = player.team
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 200.0)

    def test_secondary_stops_at_static_collision(self):
        run, player = self.spawn(4.0)
        run.match.collision_world = World(FLOOR + wall(-5.0, 2.0, 5.0, 2.0))
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 200.0)

    def test_secondary_hits_a_real_training_bot_actor(self):
        run, player = self.spawn(names=("Genji",))
        hero_guid, data = next(iter(bots.table()["heroes"].items()))
        npc = bots.Bot(bots.BOT_ENTITY, {
            "hero": hero_guid, "team": 1, "position": [0, 1, 0.8],
            "rotation": [0, 0, 0, 1], "graphs": [],
        }, data, {})
        run.match.bots.append(npc)
        self.fire(run, player, 2)
        self.assertEqual(run.match.combat.vitals[npc.entity].total, data["health"] - 87)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_dead_genji_cannot_fire_secondary(self):
        run, player = self.spawn()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(run.players[1], target, 200, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(player))
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 200.0)


if __name__ == "__main__":
    unittest.main()
