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
        run, player = self.spawn(1.5)
        self.fire_at(run, player, button=2)
        self.assertEqual(self.health(run, run.players[1]), 26)
        self.assertEqual(self.weapon(player).vars[53].value(), 27)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [58] * 3)
        self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))

    def test_primary_body_shots_remain_29_each(self):
        run, player = self.spawn(4)
        self.fire_at(run, player, height=1.0)
        self.assertEqual(self.health(run, run.players[1]), 113)
        self.assertTrue(all(not hit["+0x78"]["+0x8"] & combat.CRITICAL
                            for hit in player.client.messages(combat.HIT)))

    def test_shuriken_flights_carry_authored_damage_and_distinct_loadouts(self):
        for button, distance, loadout in ((1, 4.0, 0x0B900000000000DB), (2, 1.5, 0x0B900000000000DC)):
            with self.subTest(button=button):
                run, player = self.spawn(distance)
                enemy = run.players[1]
                x, y, z = enemy.mover.position
                yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1.0, z))
                weapon = self.weapon(player)
                self.assertEqual(weapon.vars[476].value(), 29)

                flights = {}
                for frame in range(60):
                    run.step({player: (yaw, pitch, button if frame == 0 else 0)})
                    flights.update((id(rocket), rocket) for rocket in run.match.combat.rockets)

                self.assertEqual(len(flights), 3)
                for rocket in flights.values():
                    self.assertIs(rocket.shooter, player)
                    self.assertEqual(rocket.direct.amount, 29)
                    self.assertEqual(rocket.direct.loadout, loadout)
                    self.assertTrue(rocket.direct.deflectable)
                self.assertEqual(self.health(run, enemy), 113)
                self.assertEqual(weapon.vars[53].value(), 27)
                hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x4"], hit["+0x8"]) for hit in hits], [(29, 0)] * 3)

    def test_shuriken_samples_damage_when_each_projectile_launches(self):
        for button, distance, first_frame_count, expected in (
            (1, 4.0, 1, [17, 41, 41]),
            (2, 1.5, 3, [17, 17, 17]),
        ):
            with self.subTest(button=button):
                run, player = self.spawn(distance)
                enemy = run.players[1]
                weapon = self.weapon(player)
                weapon.write((INSTANCE, 476), 17)
                weapon.component.run_now()

                x, y, z = enemy.mover.position
                yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1.0, z))
                run.step({player: (yaw, pitch, button)})
                self.assertEqual(
                    [rocket.direct.amount for rocket in run.match.combat.rockets],
                    [17] * first_frame_count,
                )

                weapon.write((INSTANCE, 476), 41)
                weapon.component.run_now()
                for _ in range(59):
                    run.step({player: (yaw, pitch, 0)})

                hits = player.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], expected)
                self.assertEqual(self.health(run, enemy), 200 - sum(expected))

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

    def test_empty_magazine_restarts_automatic_reload_after_ability_interrupt(self):
        for button, damage, refill_frame in ((0, 0, 99), (8, 50, 136),
                                             (16, 0, 248), (0x800, 30, 144)):
            with self.subTest(button=button):
                run, player = self.spawn(1.5)
                weapon = self.weapon(player)
                for _ in range(500):
                    run.step({player: (-32768, 0, 2)})
                    if weapon.vars[53].value() == 0:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(self.health(run, run.players[1]), 200)

                for frame in range(refill_frame):
                    run.step({player: (0, 0, button if frame == 55 else 0)})
                    self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(self.health(run, run.players[1]), 200 - damage)
                run.step({})
                self.assertEqual(weapon.vars[53].value(), 30)
                for _ in range(40):
                    run.step({})
                self.fire_at(run, player, height=1.0)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, run.players[1]), 113 - damage)

    def test_dash_deflect_and_melee_interrupt_reload_before_ammo_write(self):
        for button, damage in ((8, 50), (16, 0), (0x800, 30)):
            with self.subTest(button=button):
                run, player = self.spawn(1.5)
                self.fire_at(run, player, height=1.0)
                weapon = self.weapon(player)
                run.step({player: (0, 0, 0x400)})
                for _ in range(10):
                    run.step({})
                run.step({player: (0, 0, button)})
                if button == 16:
                    deflect = next(i for i in player.body_script.component.instances.values()
                                   if i.graph.index == 0x03A3)
                    self.assertTrue(deflect.state(10).active)
                for _ in range(145):
                    run.step({})
                self.assertEqual(self.health(run, run.players[1]), 113 - damage)
                self.assertEqual(weapon.vars[53].value(), 27)

                run.step({player: (0, 0, 0x400)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 27)
                for _ in range(60):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 30)
                self.fire_at(run, player, height=1.0)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, run.players[1]), max(0, 26 - damage))

    def test_post_refill_dash_deflect_and_melee_keep_ammo_and_block_early_fire(self):
        for button, damage in ((0, 0), (8, 50), (16, 0), (0x800, 30)):
            with self.subTest(button=button):
                run, player = self.spawn(1.5)
                self.fire_at(run, player, height=1.0)
                weapon = self.weapon(player)
                run.step({player: (0, 0, 0x400)})
                for _ in range(57):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 30)
                run.step({player: (0, 0, button)})
                if button == 16:
                    deflect = next(i for i in player.body_script.component.instances.values()
                                   if i.graph.index == 0x03A3).state(10)
                    self.assertTrue(deflect.active)
                for frame in range(58, 91):
                    run.step({player: (0, 0, 16 if button == 16 and frame == 83 else 1)})
                if button == 16:
                    self.assertFalse(deflect.active)
                self.assertEqual(self.health(run, run.players[1]), 113 - damage)
                self.assertEqual(weapon.vars[53].value(), 30)
                self.assertEqual(weapon.state(4).counter, 1)

                run.step({})
                if button == 16:
                    # A fresh press at the ordinary reload boundary is still
                    # rejected immediately after cancelling Deflect.
                    run.step({player: (0, 0, 1)})
                    self.assertEqual(weapon.state(4).counter, 1)
                    for _ in range(3):
                        run.step({})
                self.fire_at(run, player, height=1.0)
                self.assertEqual(weapon.state(4).counter, 2)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, run.players[1]), max(0, 26 - damage))

    def test_abilities_preserve_automatic_refill_and_keep_early_primary_locked(self):
        for button, damage in ((0, 0), (8, 50), (16, 0), (0x800, 30)):
            with self.subTest(button=button):
                run, player = self.spawn(1.5)
                enemy = run.players[1]
                weapon = self.weapon(player)
                for _ in range(500):
                    run.step({player: (-32768, 0, 2)})
                    if weapon.vars[53].value() == 0:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(self.health(run, enemy), 200)
                for _ in range(100):
                    run.step({})
                self.assertTrue(weapon.state(53).active)
                self.assertEqual(weapon.vars[53].value(), 30)

                run.step({player: (0, 0, button)})
                if button == 16:
                    deflect = next(i for i in player.body_script.component.instances.values()
                                   if i.graph.index == 0x03A3).state(10)
                    self.assertTrue(deflect.active)
                for frame in range(32):
                    run.step({player: (0, 0, 16 if button == 16 and frame == 25 else 1)})
                    self.assertEqual(weapon.state(4).counter, 0)
                    self.assertEqual(weapon.vars[53].value(), 30)
                if button == 16:
                    self.assertFalse(deflect.active)
                self.assertEqual(self.health(run, enemy), 200 - damage)

                for _ in range(10):
                    run.step({})
                self.assertFalse(weapon.state(53).active)
                self.fire_at(run, player, height=1.0)
                self.assertEqual(weapon.state(4).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, enemy), 113 - damage)

    def test_dragonblade_replaces_both_manual_reload_phases(self):
        for phase, draw_frame in (("idle", 10), ("before_refill", 10), ("after_refill", 57)):
            with self.subTest(phase=phase):
                run, player = self.spawn(4)
                self.fire_at(run, player, height=1.0)
                weapon = self.weapon(player)
                component = player.body_script.component
                player.body_script.set_ult_charge(component.entity_var(338).value())
                run.step({player: (0, 0, 0x400 if phase != "idle" else 0)})
                for _ in range(draw_frame):
                    run.step({})
                ammo_at_draw = 30 if phase == "after_refill" else 27
                self.assertEqual(weapon.vars[53].value(), ammo_at_draw)
                run.step({player: (0, 0, 32)})
                self.assertEqual(component.entity_var(342).value(), 0)
                for _ in range(61):
                    run.step({})
                    self.assertEqual(weapon.vars[53].value(), ammo_at_draw)
                run.step({})
                self.assertEqual(weapon.vars[53].value(), 30)
                run.step({player: (0, 0, 1)})
                for _ in range(450):
                    run.step({})
                self.assertEqual(self.health(run, run.players[1]), 3)
                self.assertEqual(weapon.vars[53].value(), 30)
                self.assertEqual(weapon.state(4).counter, 1)
                self.fire_at(run, player, height=1.0)
                self.assertEqual(weapon.state(4).counter, 2)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, run.players[1]), 0)

    def test_dragonblade_replaces_both_automatic_reload_phases(self):
        for frames, ammo_at_draw in ((55, 0), (100, 30)):
            with self.subTest(ammo_at_draw=ammo_at_draw):
                run, player = self.spawn(4)
                enemy = run.players[1]
                weapon = self.weapon(player)
                component = player.body_script.component
                for _ in range(500):
                    run.step({player: (-32768, 0, 2)})
                    if weapon.vars[53].value() == 0:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(self.health(run, enemy), 200)

                for _ in range(frames):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), ammo_at_draw)
                self.assertTrue(weapon.state(54 if ammo_at_draw == 0 else 53).active)
                player.body_script.set_ult_charge(component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                self.assertEqual(component.entity_var(342).value(), 0)
                for _ in range(61):
                    run.step({})
                    self.assertEqual(weapon.vars[53].value(), ammo_at_draw)
                run.step({})
                self.assertEqual(weapon.vars[53].value(), 30)

                run.step({player: (0, 0, 1)})
                for _ in range(450):
                    run.step({})
                self.assertEqual(self.health(run, enemy), 90)
                self.assertEqual(weapon.vars[53].value(), 30)
                self.assertEqual(weapon.state(4).counter, 0)
                self.fire_at(run, player, height=1.0)
                self.assertEqual(weapon.state(4).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, enemy), 3)

    def test_rifle_death_during_reload_retires_timer_before_normal_respawn(self):
        cases = ((False, 5, 27, 54), (False, 60, 30, 53), (True, 55, 0, 54), (True, 100, 30, 53))
        for automatic, delay, death_ammo, wait_state in cases:
            with self.subTest(automatic=automatic, wait_state=wait_state):
                run, player = self.spawn(4)
                enemy = run.players[1]
                self.fire_at(run, player, height=1.0)
                old_body = player.body_script
                old_weapon = self.weapon(player)
                yaw, pitch = aim(run.match.combat._eye(enemy), (0, 2, 0))
                for _ in range(120):
                    run.step({enemy: (yaw, pitch, 1)})
                    if self.health(run, player) == 40:
                        break
                self.assertEqual(self.health(run, player), 40)
                for _ in range(20):
                    run.step({})
                if automatic:
                    for _ in range(500):
                        run.step({player: (-32768, 0, 2)})
                        if old_weapon.vars[53].value() == 0:
                            break
                    self.assertEqual(old_weapon.vars[53].value(), 0)
                    self.assertEqual(self.health(run, enemy), 113)
                else:
                    run.step({player: (0, 0, 0x400)})
                for _ in range(delay):
                    run.step({})
                self.assertTrue(old_weapon.state(wait_state).active)
                self.assertEqual(old_weapon.vars[53].value(), death_ammo)
                run.step({enemy: (yaw, pitch, 1)})
                self.assertTrue(run.match.combat.is_dead(player))

                old_entity = player.body
                for _ in range(650):
                    run.step({})
                    if player.body != old_entity:
                        break
                self.assertNotEqual(player.body, old_entity)
                self.assertFalse(run.match.combat.is_dead(player))
                self.assertTrue(old_body.retired)
                self.assertEqual(old_weapon.vars[53].value(), death_ammo)
                fresh_weapon = self.weapon(player)
                self.assertIsNot(fresh_weapon, old_weapon)
                self.assertEqual(fresh_weapon.vars[53].value(), 30)
                # Allow the full burst to reach the opponent from the natural spawn.
                health_before = self.health(run, enemy)
                hit_count = len(player.client.messages(combat.HIT))
                self.fire_at(run, player, height=1.0, frames=140)
                self.assertEqual(fresh_weapon.state(4).counter, 1)
                self.assertEqual(fresh_weapon.vars[53].value(), 27)
                self.assertEqual(old_weapon.vars[53].value(), death_ammo)
                self.assertEqual(self.health(run, enemy), health_before - 87)
                hits = player.client.messages(combat.HIT)[hit_count:]
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29, 29, 29])

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

    def test_shuriken_range_clips_swept_capsule_entry(self):
        for beyond, expected in ((.08, 87), (.12, 0)):
            with self.subTest(beyond=beyond):
                run, player = self.spawn(100)
                enemy = run.players[1]
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                enemy.mover.position[2] = 100 + target.radius + beyond
                for frame in range(145):
                    run.step({player: (0, 0, 1 if frame == 0 else 0)})
                self.assertEqual(self.health(run, enemy), 200 - expected)
                self.assertEqual(self.weapon(player).vars[53].value(), 27)
                self.assertFalse(run.match.combat.rockets)

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
