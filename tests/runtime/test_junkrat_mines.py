"""Actual Match input and damage for reconstructed sticky/remote Concussion Mines."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_junkrat_projectile_combat as helpers
from test_game_combat import aim

from ow174.game import combat, immunity, junkrat
from ow174.game.commands import FIRE, Command
from ow174.game.content import find_hero, heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr


class JunkratMineTests(unittest.TestCase):
    health = helpers.JunkratProjectileTests.health
    place = helpers.JunkratProjectileTests.place

    def setUp(self):
        self.run, self.player, self.enemy = helpers.JunkratProjectileTests().setup_fight()
        self.fight = self.run.match.combat
        x, y, z = self.player.mover.position
        self.place(self.run, self.enemy, (x, y, z + 4.5))

    def throw(self, pitch=4096):
        self.run.step({self.player: (0, pitch, 8)})
        for _ in range(49):
            self.run.step({self.player: (0, pitch, 0)})

    def detonate(self):
        self.run.step({self.player: (0, 0, 2)})
        for _ in range(20):
            self.run.step({})

    def test_impact_parks_without_damage_and_secondary_deals_120_once(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        self.assertEqual(mine.vitals.total, 25)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertEqual(mine.instance.find_var(*mine.output).value(), expr.Entity(mine.entity))
        self.detonate()
        self.assertFalse(self.fight.junkrat.mines)
        self.assertEqual(self.health(self.run, self.enemy).total, 80)
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 80)

    def test_second_cast_replaces_prior_mine_without_exploding_it(self):
        self.throw()
        first, = self.fight.junkrat.mines.values()
        self.throw()
        second, = self.fight.junkrat.mines.values()
        self.assertNotEqual(second.entity, first.entity)
        self.assertNotIn(first.flight, self.fight.rockets)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_active_deflect_does_not_block_an_already_parked_mine_blast(self):
        self.run.match.switch_hero(self.enemy, find_hero("Genji"))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        self.run.step({self.enemy: (-32768, 0, 16)})
        self.run.step({self.player: (0, 0, 2), self.enemy: (-32768, 0, 0)})
        for _ in range(20):
            self.run.step({self.enemy: (-32768, 0, 0)})
        instances = self.enemy.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == 0x03A3)
        self.assertTrue(instance.states[10].active)
        self.assertEqual(self.health(self.run, self.enemy).total, 80)
        self.assertFalse(self.fight.junkrat.mines)

    def test_active_grasp_does_not_absorb_parked_mine_blast(self):
        self.run.match.switch_hero(self.enemy, find_hero("Sigma"))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        before = self.health(self.run, self.enemy).total
        self.run.step({self.enemy: (-32768, 0, 8)})
        self.run.step({self.player: (0, 0, 2), self.enemy: (-32768, 0, 0)})
        for _ in range(20):
            self.run.step({self.enemy: (-32768, 0, 0)})
        instance = next(i for i in self.enemy.body_script.component.instances.values()
                        if i.graph.index == 0x1D02)
        self.assertTrue(instance.states[1].active)
        self.assertEqual(instance.vars[21312].value(), 0)
        self.assertEqual(self.health(self.run, self.enemy).total, before - 120)
        self.assertFalse(self.fight.junkrat.mines)

    def test_zarya_barrier_absorbs_parked_mine_damage_and_impulse(self):
        self.enemy.spawn = (tuple(self.enemy.mover.position), self.enemy.spawn[1])
        self.run.match.switch_hero(self.enemy, find_hero("Zarya"))
        self.throw()
        self.run.step({self.enemy: (0, 0, 8)})
        bubble = self.fight.barriers.sync(self.enemy)
        self.assertIsNotNone(bubble)
        position = tuple(self.enemy.mover.position)
        self.detonate()
        self.assertEqual(bubble.health, 80)
        self.assertEqual(self.health(self.run, self.enemy).total, 400)
        self.assertEqual(tuple(self.enemy.mover.position), position)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "prepared Reinhardt collision required")
    def test_reinhardt_mesh_blocks_parked_mine_damage_and_impulse(self):
        self.run.match.switch_hero(self.enemy, find_hero("Reinhardt"))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        x, _, z = mine.flight.position
        y = self.player.mover.position[1]
        self.enemy.mover = Mover((x, y, z + 2), world=self.run.match.collision_world,
                                 data=mover_data(self.enemy.hero.body))
        for _ in range(8):
            self.run.step({self.enemy: (-32768, 0, 2)})
        shield = self.fight.barriers.reinhardt.actors[self.enemy.body]
        self.assertTrue(shield.active)
        before = self.health(self.run, self.enemy).total
        position = tuple(self.enemy.mover.position)
        self.run.step({self.player: (0, 0, 2), self.enemy: (-32768, 0, 2)})
        for _ in range(20):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertFalse(self.fight.junkrat.mines)
        self.assertLess(shield.health, 1600)
        self.assertEqual(self.health(self.run, self.enemy).total, before)
        self.assertEqual(tuple(self.enemy.mover.position), position)

    def test_winston_shell_blocks_parked_mine_damage_and_impulse_across_surface(self):
        self.run.match.switch_hero(self.enemy, find_hero("Winston"))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        x, _, z = mine.flight.position
        y = self.player.mover.position[1]
        world = self.run.match.collision_world
        data = mover_data(self.enemy.hero.body)
        self.enemy.mover = Mover((x, y, z + 6), world=world, data=data)
        self.run.step({self.enemy: (0, 0, 16)})
        for _ in range(12):
            self.run.step({})
        bubble, = self.fight.barriers.world.actors.values()
        self.assertEqual(bubble.health, 700)
        self.enemy.mover = Mover((x, y, z + 1.8), world=world, data=data)
        self.run.step({})
        before = self.health(self.run, self.enemy).total
        position = tuple(self.enemy.mover.position)
        self.assertGreater(math.dist(mine.flight.position, bubble.point), bubble.radius)
        self.assertLess(math.dist(position, bubble.point), bubble.radius)
        self.detonate()
        self.assertLess(bubble.health, 700)
        self.assertEqual(self.health(self.run, self.enemy).total, before)
        self.assertEqual(tuple(self.enemy.mover.position), position)

    def test_cryo_freeze_blocks_parked_mine_damage_and_impulse_until_cancel(self):
        self.run.match.switch_hero(self.enemy, find_hero("Mei"))
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.throw()
                mine, = self.fight.junkrat.mines.values()
                self.assertTrue(mine.parked)
                x, _, z = mine.flight.position
                y = self.player.mover.position[1]
                self.enemy.mover = Mover((x, y, z + 1.2), world=self.run.match.collision_world,
                                         data=mover_data(self.enemy.hero.body))
                self.run.step({self.enemy: (0, 0, 8 if protected else 2)})
                self.assertEqual(immunity.cryo_active(self.enemy, self.enemy.body), protected)
                before = self.health(self.run, self.enemy).total
                position = tuple(self.enemy.mover.position)
                self.detonate()
                self.assertFalse(self.fight.junkrat.mines)
                self.assertEqual(self.health(self.run, self.enemy).total, before if protected else before - 120)
                self.assertEqual(tuple(self.enemy.mover.position) == position, protected)

    def test_wraith_blocks_parked_mine_damage_and_impulse_until_expiry(self):
        self.run.match.switch_hero(self.enemy, find_hero("Reaper"))
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.throw()
                mine, = self.fight.junkrat.mines.values()
                self.assertTrue(mine.parked)
                x, _, z = mine.flight.position
                y = self.player.mover.position[1]
                self.enemy.mover = Mover((x, y, z + 1.2), world=self.run.match.collision_world,
                                         data=mover_data(self.enemy.hero.body))
                if protected:
                    self.run.step({self.enemy: (0, 0, 8)})
                else:
                    for _ in range(220):
                        if not immunity.wraith_active(self.enemy, self.enemy.body):
                            break
                        self.run.step({})
                self.assertEqual(immunity.wraith_active(self.enemy, self.enemy.body), protected)
                before = self.health(self.run, self.enemy).total
                position = tuple(self.enemy.mover.position)
                self.detonate()
                self.assertFalse(self.fight.junkrat.mines)
                self.assertEqual(self.health(self.run, self.enemy).total,
                                 before if protected else before - 120)
                self.assertEqual(tuple(self.enemy.mover.position) == position, protected)

    def test_fade_blocks_parked_mine_damage_and_impulse(self):
        self.run.match.switch_hero(self.enemy, find_hero("Moira"))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        x, _, z = mine.flight.position
        y = self.player.mover.position[1]
        self.enemy.mover = Mover((x, y, z + 1.2), world=self.run.match.collision_world,
                                 data=mover_data(self.enemy.hero.body))
        self.run.step({self.enemy: (0, 0, 8)})
        self.assertTrue(immunity.phased_out(self.enemy, self.enemy.body))
        before = self.health(self.run, self.enemy).total
        position = tuple(self.enemy.mover.position)
        self.detonate()
        self.assertFalse(self.fight.junkrat.mines)
        self.assertEqual(self.health(self.run, self.enemy).total, before)
        self.assertEqual(tuple(self.enemy.mover.position), position)

    def test_fortify_reduces_mine_damage_and_blocks_its_displacement(self):
        self.run.match.switch_hero(self.enemy, find_hero("Orisa"))
        self.throw()
        self.run.step({self.enemy: (0, 0, 8)})
        scale = immunity.damage_scale(self.enemy, self.enemy.body)
        self.assertLess(scale, 1)
        before = self.health(self.run, self.enemy).total
        position = tuple(self.enemy.mover.position)
        self.detonate()
        expected = max(120 * scale - 5, 120 * scale / 2)
        self.assertAlmostEqual(before - self.health(self.run, self.enemy).total, expected)
        self.assertEqual(tuple(self.enemy.mover.position), position)

    def test_authored_two_charges_refuse_third_throw_until_recovery(self):
        with patch.object(self.fight.junkrat, "cast", wraps=self.fight.junkrat.cast) as casts:
            self.throw()
            self.throw()
            self.throw()
            self.assertEqual(casts.call_count, 2)
            for _ in range(520):
                self.run.step({})
            self.assertEqual(casts.call_count, 2)  # do not revive a rejected third press after8s
            self.throw()
            self.assertEqual(casts.call_count, 3)

    def test_enemy_weapon_destroys_mine_without_damaging_its_owner(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        yaw, pitch = aim(self.fight._eye(self.enemy), mine.flight.position)
        for _ in range(15):
            self.run.step({self.enemy: (yaw, pitch, FIRE)})
        self.assertFalse(self.fight.junkrat.mines)
        self.assertTrue(mine.vitals.dead)
        self.assertEqual(mine.vitals.killer, self.enemy.body)
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_enemy_destruction_retires_queued_remote_mine_detonation(self):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hostile_team = self.enemy.team
        self.enemy.team = self.player.team
        match.switch_hero(self.enemy, heroes()[0x02E0000000000042])
        x, y, z = self.player.mover.position
        self.place(self.run, self.enemy, (x, y, z + 4.5))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        direction = aim(self.fight._eye(self.enemy), mine.flight.position)
        self.run.step({self.enemy: (*direction, FIRE)})
        for _ in range(30):
            self.run.step({})
        self.assertEqual(mine.vitals.total, 25)
        self.assertTrue(mine.parked)

        self.enemy.team = hostile_team
        before = self.health(self.run, self.enemy).total
        self.run.frame += 1
        match.update(self.run.frame * combat.TICK, self.run.frame)
        self.player.take_commands([Command(self.run.frame, buttons=2)])
        self.assertIn((mine.entity, junkrat.DETONATE, self.player.body), self.fight.junkrat.pending)
        self.enemy.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1], buttons=FIRE)])
        self.assertTrue(mine.vitals.dead)
        self.assertEqual(mine.vitals.killer, self.enemy.body)
        self.assertNotIn(mine.entity, self.fight.junkrat.mines)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(self.health(self.run, self.enemy).total, before)
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.assertFalse(self.player.client.messages(combat.HIT))

    def test_owner_leaving_retires_queued_remote_mine_before_next_tick(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        self.run.step({self.player: (0, 0, 2)})
        self.assertIn((mine.entity, junkrat.DETONATE, self.player.body), self.fight.junkrat.pending)
        frame = self.run.frame
        match = self.run.match
        match.leave(self.player)
        self.assertEqual(self.fight.tick, frame)
        self.assertNotIn(mine.entity, self.fight.junkrat.mines)
        self.assertNotIn(mine.flight, self.fight.rockets)
        with patch.object(self.fight, "_explode", wraps=self.fight._explode) as explode:
            match.update((frame + 1) * combat.TICK, frame + 1, frames=False)
            explode.assert_not_called()
        self.assertFalse(self.fight.junkrat.pending)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_owner_killed_in_detonation_frame_cancels_pending_mine_blast(self):
        match = self.run.match
        x, y, z = self.player.mover.position
        match.switch_hero(self.enemy, heroes()[0x02E0000000000042])
        self.place(self.run, self.enemy, (x, y, z + 4.5))
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        health = self.health(self.run, self.player)
        health.now[combat.HEALTH_POOL] = 50
        direction = aim(self.fight._eye(self.enemy), self.fight._eye(self.player))
        self.run.frame += 1
        match.update(self.run.frame * combat.TICK, self.run.frame)
        self.player.take_commands([Command(self.run.frame, buttons=2)])
        pending = (mine.entity, junkrat.DETONATE, self.player.body)
        self.assertIn(pending, self.fight.junkrat.pending)
        self.enemy.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1], buttons=FIRE)])
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, self.enemy.body)
        with patch.object(self.fight, "_explode", wraps=self.fight._explode) as explode:
            for _ in range(25):
                self.run.step({})
            self.assertFalse(any(call.args[0] is mine.flight for call in explode.call_args_list))
        self.assertNotIn(pending, self.fight.junkrat.pending)
        self.assertIn(mine.entity, self.fight.junkrat.mines)

    def test_remote_mine_range(self):
        self.throw()
        x, y, z = self.enemy.mover.position
        self.place(self.run, self.enemy, (x + 100, y, z))
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_remote_mine_wall_occlusion(self):
        # A fresh throw keeps the owner pose independent of remote self-launch.
        self.throw()
        x, y, z = self.player.mover.position
        self.place(self.run, self.enemy, (x, y, z + 4.8))
        helpers.JunkratProjectileTests.install_wall(self, self.run, self.player)
        mine, = self.fight.junkrat.mines.values()
        self.assertLess(mine.flight.position[2], z + 4)
        radius = mover_data(self.enemy.hero.body).stand_radius
        self.assertGreater(self.enemy.mover.position[2] - radius, z + 4)
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_mine_message_rejects_another_owner_and_dead_owner(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertFalse(self.fight.junkrat.message(mine.entity, junkrat.DETONATE, self.enemy.body))
        target = next(t for t in self.fight._targets() if t.owner is self.player and t.construct is None)
        self.fight._damage(self.enemy, target, 200, False, None, self.run.frame)
        self.assertFalse(self.fight.junkrat.message(mine.entity, junkrat.DETONATE, self.player.body))
        self.assertIn(mine.entity, self.fight.junkrat.mines)

    def test_actual_owner_death_preserves_parked_mine_until_body_retires(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.assertTrue(mine.parked)
        x, y, z = self.player.mover.position
        self.place(self.run, self.enemy, (x, y, z + 8))
        for _ in range(25):
            self.run.step({})
        facing = aim(self.fight._eye(self.enemy), self.fight._eye(self.player))
        old_body = self.player.body
        for _ in range(100):
            self.run.step({self.enemy: (*facing, FIRE)})
            if self.fight.is_dead(self.player):
                break
        self.assertTrue(self.fight.is_dead(self.player))
        self.assertIn(mine.entity, self.fight.junkrat.mines)
        self.detonate()
        self.assertIn(mine.entity, self.fight.junkrat.mines)
        for _ in range(round(combat.respawn_seconds(self.run.match) / combat.TICK) + 5):
            self.run.step({})
            if self.player.body != old_body:
                break
        self.assertNotEqual(self.player.body, old_body)
        self.run.step({})
        self.assertNotIn(mine.entity, self.fight.junkrat.mines)
        self.assertNotIn(mine.flight, self.fight.rockets)
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

        x, y, z = self.player.mover.position
        self.place(self.run, self.enemy, (x, y, z + 4.5))
        self.throw()
        fresh, = self.fight.junkrat.mines.values()
        self.assertEqual(fresh.source, self.player.body)
        self.detonate()
        self.assertNotIn(fresh.entity, self.fight.junkrat.mines)
        self.assertEqual(self.health(self.run, self.enemy).total, 80)

    def test_hero_change_retires_old_body_mine_without_explosion(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        self.run.match.switch_hero(self.player, self.enemy.hero)
        self.run.step({})
        self.assertFalse(self.fight.junkrat.mines)
        self.assertNotIn(mine.flight, self.fight.rockets)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_mine_self_launch_moves_the_hero_without_self_damage(self):
        self.throw(pitch=8192)
        position = tuple(self.player.mover.position)
        self.detonate()
        self.assertEqual(self.health(self.run, self.player).total, 200)
        self.assertGreater(self.player.mover.position[1], position[1] + .2)
        self.assertLess(self.player.mover.position[2], position[2])

    def test_mine_enemy_knockback_moves_the_damaged_body(self):
        self.throw()
        position = tuple(self.enemy.mover.position)
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 80)
        self.assertGreater(self.enemy.mover.position[1], position[1])
        self.assertGreater(self.enemy.mover.position[2], position[2])

    def test_mine_has_no_critical_hitbox_and_cannot_be_healed(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        yaw, pitch = aim(self.fight._eye(self.enemy), mine.flight.position)
        self.run.step({self.enemy: (yaw, pitch, FIRE)})
        self.assertEqual(mine.vitals.total, 5)  # Soldier's20, no hero headshot multiplier
        self.fight.heal_area(self.player, mine.flight.position, 10, 100)
        self.assertEqual(mine.vitals.total, 5)

    def test_mine_does_not_damage_or_displace_a_teammate(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.enemy.team = self.player.team
        position = tuple(self.enemy.mover.position)
        self.throw()
        self.detonate()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertEqual(tuple(self.enemy.mover.position), position)

    def test_death_during_throw_delay_cancels_the_mine(self):
        self.run.step({self.player: (0, 0, 8)})
        target = next(t for t in self.fight._targets() if t.owner is self.player)
        self.fight._damage(self.enemy, target, 200, False, None, self.run.frame)
        for _ in range(60):
            self.run.step({})
        self.assertFalse(self.fight.junkrat.mines)

    def test_secondary_detonates_an_airborne_mine(self):
        for offset in range(20):
            self.run.step({self.player: (0, 0, 8 if offset == 0 else 0)})
        mine, = self.fight.junkrat.mines.values()
        self.assertFalse(mine.parked)
        self.detonate()
        self.assertFalse(self.fight.junkrat.mines)
        self.assertEqual(self.health(self.run, self.enemy).total, 80)

    def test_mine_authored_damage_falloff_is_75_at_mid_falloff_distance(self):
        self.throw()
        mine, = self.fight.junkrat.mines.values()
        x, y, z = mine.flight.position
        radius = mover_data(self.enemy.hero.body).stand_radius
        # Arrange2.25m from the capsule surface, midway between authored1.5/3m.
        feet = self.enemy.mover.position[1]
        vertical = feet + radius - y
        lateral = math.sqrt((2.25 + radius)**2 - vertical**2)
        self.place(self.run, self.enemy, (x + lateral, feet, z))
        self.detonate()
        self.assertAlmostEqual(self.health(self.run, self.enemy).total, 125, delta=.1)


if __name__ == "__main__":
    unittest.main()
