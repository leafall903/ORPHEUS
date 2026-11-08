"""Weapon damage regions, resources, reload and melee command-path boundaries."""

import unittest

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.script.expr import INSTANCE


class GenjiWeaponTests(GenjiFixture):
    def weapon(self, player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)

    def fire_at(self, run, player, *, button=1, height=1.55, frames=60):
        enemy = run.players[1]
        x, y, z = enemy.mover.position
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + height, z))
        for frame in range(frames):
            run.step({player: (yaw, pitch, button if frame == 0 else 0)})

    def test_primary_headshots_are_58_each(self):
        run, player = self.spawn(4)
        self.fire_at(run, player)
        self.assertEqual(self.health(run, run.players[1]), 26)
        self.assertEqual(self.weapon(player).vars[53].value(), 27)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [58] * 3)
        self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))

    def test_secondary_headshots_are_58_each(self):
        run, player = self.spawn()
        self.fire_at(run, player, button=2)
        self.assertEqual(self.health(run, run.players[1]), 26)
        self.assertEqual(self.weapon(player).vars[53].value(), 27)

    def test_primary_body_shots_remain_29_each(self):
        run, player = self.spawn(4)
        self.fire_at(run, player, height=1.0)
        self.assertEqual(self.health(run, run.players[1]), 113)
        self.assertTrue(all(not hit["+0x78"]["+0x8"] & combat.CRITICAL
                            for hit in player.client.messages(combat.HIT)))

    def test_manual_reload_restores_thirty_after_authored_delay(self):
        run, player = self.spawn(4)
        self.fire_at(run, player, height=1.0)
        weapon = self.weapon(player)
        run.step({player: (0, 0, 0x400)})  # logical button14, reload
        for _ in range(40):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 27)
        for _ in range(25):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 30)

    def test_primary_miss_consumes_three_without_damage(self):
        run, player = self.spawn(4)
        for frame in range(60):
            run.step({player: (-32768, 0, 1 if frame == 0 else 0)})
        self.assertEqual(self.health(run, run.players[1]), 200)
        self.assertEqual(self.weapon(player).vars[53].value(), 27)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_melee_wall_blocks_damage(self):
        run, player = self.spawn(1.5)
        run.match.collision_world = World(FLOOR + wall(-5, 0.75, 5, 0.75))
        self.fire_at(run, player, button=0x800)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_melee_damage_does_not_consume_shuriken(self):
        run, player = self.spawn(1.5)
        self.fire_at(run, player, button=0x800)
        self.assertEqual(self.health(run, run.players[1]), 170)
        self.assertEqual(self.weapon(player).vars[53].value(), 30)

    def test_reload_blocks_fire_through_both_authored_waits(self):
        run, player = self.spawn(4)
        self.fire_at(run, player, height=1.0)
        weapon = self.weapon(player)
        run.step({player: (0, 0, 0x400)})
        for _ in range(90):
            run.step({player: (0, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), 30)
        self.assertEqual(weapon.state(4).counter, 1)
        for _ in range(20):
            run.step({player: (0, 0, 1)})
        self.assertEqual(weapon.state(4).counter, 2)
        self.assertEqual(weapon.vars[53].value(), 27)

    def test_empty_weapon_cannot_shoot_until_automatic_reload_ends(self):
        run, player = self.spawn(4)
        weapon = self.weapon(player)
        weapon.write((INSTANCE, 53), 0)
        weapon.component.run_now()
        for _ in range(40):
            run.step({player: (0, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(player.client.messages(combat.HIT), [])
        for _ in range(70):
            run.step({player: (0, 0, 1)})
        self.assertGreater(weapon.state(4).counter, 0)
        self.assertGreaterEqual(weapon.vars[53].value(), 0)
        self.assertLessEqual(weapon.vars[53].value(), 30)

    def test_primary_with_one_round_only_emits_one_shuriken(self):
        run, player = self.spawn(4)
        weapon = self.weapon(player)
        weapon.write((INSTANCE, 53), 1)
        weapon.component.run_now()
        self.fire_at(run, player, height=1.0, frames=30)
        self.assertEqual(self.health(run, run.players[1]), 171)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_last_partial_cost_fan_emits_one_three_projectile_shot(self):
        for ammo in (1, 2):
            with self.subTest(ammo=ammo):
                run, player = self.spawn()
                weapon = self.weapon(player)
                weapon.write((INSTANCE, 53), ammo)
                weapon.component.run_now()
                self.fire_at(run, player, button=2, height=1.0, frames=30)
                self.assertEqual(self.health(run, run.players[1]), 113)
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_long_range_shuriken_has_no_damage_falloff(self):
        run, player = self.spawn(99)
        self.fire_at(run, player, height=1.0, frames=360)
        self.assertEqual(self.health(run, run.players[1]), 113)

    def test_target_beyond_authored_projectile_range_is_not_hit(self):
        run, player = self.spawn(102)
        self.fire_at(run, player, height=1.0, frames=360)
        self.assertEqual(self.health(run, run.players[1]), 200)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_melee_misses_out_of_range_and_allies(self):
        run, player = self.spawn(4)
        self.fire_at(run, player, button=0x800)
        self.assertEqual(self.health(run, run.players[1]), 200)
        run, player = self.spawn(1.5)
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        run.players[1].team = player.team
        self.fire_at(run, player, button=0x800)
        self.assertEqual(self.health(run, run.players[1]), 200)


if __name__ == "__main__":
    unittest.main()
