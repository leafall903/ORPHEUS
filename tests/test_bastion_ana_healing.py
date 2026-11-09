"""Actual Ana grenade and Bastion Self-Repair commands in 104319."""
import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import ana_grenade, stats, zarya_barriers
from ow174.game.content import heroes


class BastionAnaHealingTests(unittest.TestCase):
    def arena(self, friendly=False):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 6), enemy_hero=ana_grenade.HERO)
        case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
        case.player.team = 0
        case.enemy.team = 0 if friendly else 1
        case.bystander.team = 1
        health = case.health(case.player)
        health.damage(280 if friendly else 170)
        case.fight._sync_body_health(case.player, health)
        return case, health

    @staticmethod
    def throw(case, health):
        ana = case.enemy
        yaw, pitch = aim(case.fight._eye(ana), (0, 2, 0))
        case.run.step({ana: (yaw, pitch, 16)})
        for _ in range(60):
            if health.antiheal_until is not None or health.healing_boost is not None:
                break
            case.run.step({ana: (yaw, pitch, 0)})

    def test_hostile_grenade_blocks_repair_without_freezing_fuel_then_expires(self):
        case, health = self.arena()
        before = health.total
        self.throw(case, health)
        self.assertIsNotNone(health.antiheal_until)
        self.assertIsNone(health.healing_boost)
        self.assertEqual(health.total, before - 60)
        script = case.player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        case.step(2, 50)
        self.assertLess(repair.vars[87].value(), 100)
        self.assertEqual(health.total, before - 60)
        self.assertEqual(case.player.stats.value(stats.HEALING), 0)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        case.step(0, 5)
        while case.fight.now < health.antiheal_until:
            case.step()
        case.step(2, 45)
        self.assertGreater(health.total, before - 60)
        self.assertAlmostEqual(case.player.stats.value(stats.HEALING), health.total - (before - 60))
        self.assertAlmostEqual(script.component.entity_var(1732).value(), health.total)

    def test_friendly_grenade_heals_then_scales_repair_by_one_and_a_half(self):
        boosted, health = self.arena(friendly=True)
        before = health.total
        self.throw(boosted, health)
        self.assertIsNone(health.antiheal_until)
        self.assertIsNotNone(health.healing_boost)
        baseline = before + 100
        self.assertAlmostEqual(health.total, baseline)
        self.assertAlmostEqual(boosted.enemy.stats.value(stats.HEALING), 100)
        control, unboosted = self.arena(friendly=True)
        self.assertAlmostEqual(unboosted.heal(100), 100)
        control.fight._sync_body_health(control.player, unboosted)
        boosted.step(0, 5)
        control.step(0, 5)
        boosted.step(2, 45)
        control.step(2, 45)
        self.assertGreater(unboosted.total, baseline)
        self.assertAlmostEqual(health.total - baseline, 1.5 * (unboosted.total - baseline), places=5)
        while boosted.fight.now < health.healing_boost[1]:
            boosted.step()
            control.step()
        first, second = health.total, unboosted.total
        boosted.step(2, 35)
        control.step(2, 35)
        self.assertGreater(health.total, first)
        self.assertAlmostEqual(health.total - first, unboosted.total - second, places=5)

    def test_wall_prevents_grenade_damage_and_healing_filter(self):
        case, health = self.arena()
        case.wall(3)
        before = health.total
        self.throw(case, health)
        self.assertEqual(health.total, before)
        self.assertIsNone(health.antiheal_until)
        self.assertIsNone(health.healing_boost)
        case.step(2, 45)
        self.assertGreater(health.total, before)

    def test_projected_barrier_blocks_grenade_damage_and_antiheal(self):
        case, health = self.arena()
        zarya = case.bystander
        self.assertTrue(case.run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        zarya.team = case.player.team
        zarya.mover.position[:] = (4, 1, 0)
        zarya.mover.body = None
        case.step(0, 15)
        case.run.step({zarya: (-16384, 0, 0)})
        case.run.step({zarya: (-16384, 0, 16)})
        instance = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x025C)
        self.assertTrue(instance.state(10).active)
        before = health.total
        self.throw(case, health)
        self.assertEqual(health.total, before)
        self.assertIsNone(health.antiheal_until)
        self.assertEqual(case.fight.zarya_barriers.projected[zarya.body].remaining, 140)
        case.step(2, 40)
        self.assertGreater(health.total, before)


if __name__ == "__main__":
    unittest.main()
