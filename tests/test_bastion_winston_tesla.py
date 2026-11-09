"""Actual Winston primary input and damage against all Bastion configurations."""

import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, stats, winston_barrier, zarya_barriers
from ow174.game.content import heroes


class BastionTeslaTests(unittest.TestCase):
    def arena(self, mode="recon", distance=5):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, distance), enemy_hero=winston_barrier.HERO,
                     bystander_position=(12, 1, 12))
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        return case

    @staticmethod
    def fire(case, frames=12, *, miss=False):
        winston = case.enemy
        direction = (0, 0) if miss else aim(case.fight._eye(winston), (0, 2, 0))
        for _ in range(frames):
            case.run.step({winston: (*direction, 1)})

    def test_tesla_uses_ironclad_before_armour_and_stops_on_release(self):
        for form, loss in (("recon", 0.48), ("sentry", 0.384), ("tank", 0.384)):
            with self.subTest(form=form):
                case = self.arena(form)
                health = case.health(case.player)
                before = health.total
                self.fire(case)
                hits = case.enemy.client.messages(combat.HIT)
                self.assertEqual(len(hits), 12)
                self.assertAlmostEqual(before - health.total, loss * len(hits), places=5)
                released = health.total
                case.step(0, 25)
                self.assertEqual(health.total, released)
                self.assertEqual(case.enemy.client.messages(combat.HIT), hits)
                self.fire(case, 5)
                self.assertAlmostEqual(released - health.total, loss * 5, places=5)

    def test_actual_flashbang_interrupts_held_tesla_until_stun_ends(self):
        case = self.arena()
        winston, mccree = case.enemy, case.bystander
        self.assertTrue(case.run.match.switch_hero(mccree, heroes()[0x02E0000000000042]))
        mccree.mover.position[:] = (2.5, 1, 5)
        mccree.mover.body = None
        case.step(0, 15)
        self.fire(case)
        own = case.health(case.player)
        self.assertLess(own.total, 300)
        tesla = aim(case.fight._eye(winston), (0, 2, 0))
        grenade = aim(case.fight._eye(mccree), (0, 2, 5))
        for offset in range(30):
            case.run.step({mccree: (*grenade, 16 if offset == 0 else 0), winston: (*tesla, 1)})
            if case.fight.is_stunned(winston):
                break
        self.assertTrue(case.fight.is_stunned(winston))
        stunned_health = own.total
        hit_count = len(winston.client.messages(combat.HIT))
        deadline = case.fight.stuns[winston.body]
        while case.run.frame < deadline - 1:
            case.run.step({winston: (*tesla, 1)})
            self.assertEqual(own.total, stunned_health)
            self.assertEqual(len(winston.client.messages(combat.HIT)), hit_count)
        self.fire(case, 8)
        self.assertFalse(case.fight.is_stunned(winston))
        self.assertLess(own.total, stunned_health)

    def test_real_bastion_and_tesla_attacks_kill_and_retire_return_fire(self):
        for winston_dies in (False, True):
            with self.subTest(winston_dies=winston_dies):
                case = self.arena()
                winston = case.enemy
                victim, killer = (winston, case.player) if winston_dies else (case.player, winston)
                pools = case.health(victim)
                pools.damage(pools.total - 0.5)
                case.fight._sync_body_health(victim, pools)
                self.assertFalse(pools.dead)
                if winston_dies:
                    case.step(1, 10)
                else:
                    self.fire(case, 12)
                self.assertTrue(pools.dead)
                self.assertEqual(pools.killer, killer.body)
                self.assertEqual(killer.stats.value(stats.ELIMINATIONS), 1)
                before = case.health(killer).total
                if winston_dies:
                    self.fire(case, 20)
                else:
                    case.step(1, 20)
                self.assertEqual(case.health(killer).total, before)
                self.assertFalse(victim.client.messages(combat.HIT))
                self.assertEqual(killer.stats.value(stats.ELIMINATIONS), 1)

    def test_projected_barrier_spends_raw_tesla_before_ironclad_and_armour(self):
        case = self.arena("sentry")
        match = case.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya = case.bystander
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        case.player.team = zarya.team = 0
        case.enemy.team = 1
        zarya.mover.position[:] = (4, 1, 0)
        zarya.mover.body = None
        case.step(0, 15)
        case.run.step({zarya: (-16384, 0, 0)})
        case.run.step({zarya: (-16384, 0, 16)})
        self.fire(case)
        health = case.health(case.player)
        self.assertEqual(health.total, 300)
        self.assertAlmostEqual(case.fight.zarya_barriers.projected[zarya.body].remaining,
                               200 - 12 * 0.96, places=5)
        case.step(0, 130)
        self.fire(case, 5)
        self.assertAlmostEqual(300 - health.total, 5 * 0.384, places=5)

    def test_tesla_rejects_range_wall_rear_and_friendly_targets(self):
        for reason in ("range", "wall", "rear", "friendly"):
            with self.subTest(reason=reason):
                case = self.arena(distance=10 if reason == "range" else 5)
                if reason == "wall":
                    case.wall(2.5)
                if reason == "friendly":
                    match = case.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    case.player.team = case.enemy.team = 0
                self.fire(case, miss=reason == "rear")
                self.assertEqual(case.health(case.player).total, 300)
                self.assertFalse(case.enemy.client.messages(combat.HIT))


if __name__ == "__main__":
    unittest.main()
