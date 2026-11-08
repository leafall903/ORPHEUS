"""Dragonblade commands execute authored charge gates and sword target windows."""

import unittest

from test_game_mover import FLOOR, wall
from test_genji_combat import GenjiFixture

from ow174.game.collision import World


class GenjiBladeTests(GenjiFixture):
    def run_blade(self, run, player, frames=110, *, charge=True):
        component = player.body_script.component
        if charge:
            player.body_script.set_ult_charge(component.entity_var(338).value())
        for frame in range(frames):
            run.step({player: (0, 0, 32 if frame == 1 else (1 if frame > 70 else 0))})

    def ultimate(self, player):
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0317)
        return instance.state(3)

    def test_full_charge_is_consumed_and_swing_deals_authored_110_once(self):
        run, player = self.spawn(4)
        self.run_blade(run, player)
        self.assertEqual(self.health(run, run.players[1]), 90)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertTrue(self.ultimate(player).flags & 1)

    def test_empty_charge_rejects_ultimate(self):
        run, player = self.spawn(4)
        self.run_blade(run, player, frames=40, charge=False)
        self.assertFalse(self.ultimate(player).flags & 1)

    def test_repeated_swings_kill_and_do_not_hit_dead_body(self):
        run, player = self.spawn(4)
        self.run_blade(run, player, frames=170)
        enemy = run.players[1]
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(self.health(run, enemy), 0)
        self.assertEqual(len(player.client.messages(25001)), 2)

    def test_sword_misses_distant_or_rear_enemy(self):
        for distance in (8, -4):
            with self.subTest(distance=distance):
                run, player = self.spawn(distance)
                self.run_blade(run, player)
                self.assertEqual(self.health(run, run.players[1]), 200)

    def test_sword_static_wall_blocks_damage(self):
        run, player = self.spawn(4)
        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
        self.run_blade(run, player)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_sword_hits_multiple_enemies(self):
        run, player = self.spawn(4, ("Genji", "A", "B"))
        run.players[2].mover.position[0] = 1
        self.run_blade(run, player)
        self.assertEqual([self.health(run, p) for p in run.players[1:]], [90, 90])

    def test_sword_does_not_hit_ally(self):
        run, player = self.spawn(4)
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        run.players[1].team = player.team
        self.run_blade(run, player)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_ultimate_ends_and_shuriken_returns(self):
        run, player = self.spawn(10)
        self.run_blade(run, player, frames=530)
        self.assertFalse(self.ultimate(player).flags & 1)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)
        self.assertGreater(weapon.state(4).counter, 0)


if __name__ == "__main__":
    unittest.main()
