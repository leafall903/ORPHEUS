"""Actual Winston Barrier Projector against Bastion 104319 attacks."""

import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, reinhardt_barrier, winston_barrier
from ow174.game.content import heroes


class BastionWinstonBarrierTests(unittest.TestCase):
    def arena(self, mode="recon", distance=9):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, distance), bystander_position=(0, 1, 16),
                     enemy_hero=winston_barrier.HERO)
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        return case

    def deploy(self, case):
        winston = case.enemy
        case.run.step({winston: (0, 0, 16)})
        self.assertNotIn(winston.body, case.fight.winston_barriers.actors)
        case.step(0, 3)
        bubble = case.fight.winston_barriers.actors[winston.body]
        self.assertEqual((bubble.health, bubble.radius), (700, 5))
        self.assertAlmostEqual(bubble.expires - case.fight.now, 9)
        return bubble

    def test_recon_sentry_and_tank_damage_hostile_sphere_before_winston(self):
        for mode, frames, damage in (("recon", 10, 40), ("sentry", 10, 75), ("tank", 1, 205)):
            with self.subTest(mode=mode):
                case = self.arena(mode)
                bubble = self.deploy(case)
                own = case.health(case.enemy)
                other = case.health(case.bystander)
                before, behind = own.total, other.total
                case.step(1, frames)
                case.step(0, 22)
                self.assertAlmostEqual(bubble.health, 700 - damage, places=5)
                self.assertEqual((own.total, other.total), (before, behind))
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)

    def test_exit_crossing_hits_sphere_before_a_body_outside(self):
        case = self.arena(distance=1.4)
        bubble = self.deploy(case)
        winston = case.enemy
        winston.mover.position[:] = (0, 1, 9)
        winston.mover.body = None
        case.command_aim = aim(case.fight._eye(case.player), (0, 2, 9))
        case.step(0, 5)
        own, behind = case.health(winston), case.health(case.bystander)
        before = (own.total, behind.total)
        case.step(1, 10)
        self.assertEqual(bubble.health, 660)
        self.assertEqual((own.total, behind.total), before)

    def test_bastion_melee_spends_sphere_durability_only_at_the_surface(self):
        for distance, expected_sphere, expected_body in ((6.5, 670, 0), (1.4, 700, 25)):
            with self.subTest(distance=distance):
                case = self.arena(distance=distance)
                bubble = self.deploy(case)
                health = case.health(case.enemy)
                before = health.total
                case.step(0x800)
                case.step(0, 18)
                self.assertEqual(bubble.health, expected_sphere)
                self.assertEqual(before - health.total, expected_body)

    def test_tank_breaking_impact_spends_last_durability_without_spill(self):
        case = self.arena("tank")
        bubble = self.deploy(case)
        health = case.health(case.enemy)
        before = health.total
        case.step(1, 201)
        case.step(0, 22)
        self.assertEqual(bubble.health, 0)
        self.assertNotIn(case.enemy.body, case.fight.winston_barriers.actors)
        self.assertEqual(health.total, before)
        self.assertFalse(case.player.client.messages(combat.HIT))
        case.step(0, 70)
        case.step(1)
        case.step(0, 22)
        self.assertLess(health.total, before)

    def test_expiry_rejects_early_recast_then_cooldown_reuse_restores_sphere(self):
        case = self.arena()
        bubble = self.deploy(case)
        winston = case.enemy
        instance = next(i for i in winston.body_script.component.instances.values()
                        if i.graph.index == 0x00FA)
        ability = instance.states[2]
        self.assertGreater(ability.remaining(), 12.9)
        while case.fight.now + combat.TICK < bubble.expires:
            case.step()
        self.assertIn(winston.body, case.fight.winston_barriers.actors)
        case.step()
        self.assertNotIn(winston.body, case.fight.winston_barriers.actors)
        self.assertGreater(ability.remaining(), 0)
        case.run.step({winston: (0, 0, 16)})
        case.step(0, 10)
        self.assertNotIn(winston.body, case.fight.winston_barriers.actors)
        before = case.health(winston).total
        case.step(1, 10)
        self.assertLess(case.health(winston).total, before)
        for _ in range(320):
            if ability.remaining() == 0:
                break
            case.step()
        self.assertEqual(ability.remaining(), 0)
        self.assertIsNot(self.deploy(case), bubble)

    def test_nearer_enemy_barrier_absorbs_the_entire_attack(self):
        for mode, damage in (("recon", 40), ("sentry", 75), ("tank", 205)):
            for nearer_mesh in (False, True):
                with self.subTest(mode=mode, nearer_mesh=nearer_mesh):
                    case = self.arena(mode)
                    rein = case.bystander
                    self.assertTrue(case.run.match.switch_hero(rein, heroes()[reinhardt_barrier.REINHARDT]))
                    rein.mover.position[:] = (0, 1, 3 if nearer_mesh else 13)
                    rein.mover.body = None
                    case.step(0, 15)
                    bubble = self.deploy(case)
                    for _ in range(12):
                        case.run.step({rein: (-32768, 0, 2)})
                    shield = reinhardt_barrier.active(rein)
                    self.assertIsNotNone(shield)
                    start = shield.vars[586].value()
                    health = (case.health(case.enemy), case.health(rein))
                    before = tuple(vitals.total for vitals in health)
                    for frame in range(24):
                        buttons = int(frame < (1 if mode == "tank" else 10))
                        case.run.step({case.player: (*case.command_aim, buttons), rein: (-32768, 0, 2)})
                    self.assertAlmostEqual(700 - bubble.health, 0 if nearer_mesh else damage, places=5)
                    self.assertAlmostEqual(start - shield.vars[586].value(), damage if nearer_mesh else 0,
                                           places=5)
                    self.assertEqual(tuple(vitals.total for vitals in health), before)
                    self.assertFalse(case.player.client.messages(combat.HIT))

    def test_world_wall_and_friendly_sphere_keep_normal_ordering(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                case = self.arena()
                if friendly:
                    match = case.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    case.player.team = case.enemy.team = 0
                    case.bystander.team = 1
                else:
                    case.wall(2)
                bubble = self.deploy(case)
                behind = case.health(case.bystander)
                before = behind.total
                case.step(1, 10)
                self.assertEqual(bubble.health, 700)
                self.assertEqual(before - behind.total, 40 if friendly else 0)


if __name__ == "__main__":
    unittest.main()
