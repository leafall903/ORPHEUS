"""Actual 104319 Orisa Fortify and Fusion Driver against Bastion."""

import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, defensive_casts, world


class BastionFortifyTests(unittest.TestCase):
    def arena(self, mode):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 1.4 if mode == "melee" else 8),
                     enemy_hero=defensive_casts.ORISA)
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        case.run.step({case.enemy: (0, 0, 8)})
        self.assertAlmostEqual(defensive_casts.fortify_scale(case.enemy), 0.6, places=5)
        self.assertTrue(defensive_casts.fortify_force_immune(case.enemy))
        return case

    def test_fortify_mitigates_all_four_attacks_before_armour(self):
        for form, buttons, frames, expected in (
            ("recon", 1, 10, 14), ("sentry", 1, 10, 22.5),
            ("melee", 0x800, 1, 13), ("tank", 1, 1, 113),
        ):
            with self.subTest(form=form):
                case = self.arena(form)
                pools = case.health(case.enemy)
                before = pools.total
                feet = tuple(case.enemy.mover.position)
                case.step(buttons, frames)
                case.step(0, 22)
                self.assertAlmostEqual(before - pools.total, expected, places=4)
                self.assertEqual(tuple(case.enemy.mover.position), feet)
                self.assertTrue(case.player.client.messages(combat.HIT))

    def test_tank_impulse_is_rejected_only_while_fortify_is_active(self):
        case = self.arena("tank")
        enemy = case.enemy
        pools = case.health(enemy)
        start = tuple(enemy.mover.position)
        before = pools.total
        case.step(1)
        case.step(0, 22)
        self.assertAlmostEqual(before - pools.total, 113, places=4)
        self.assertEqual(tuple(enemy.mover.position), start)
        for _ in range(280):
            if not defensive_casts.fortify_force_immune(enemy):
                break
            case.step()
        self.assertFalse(defensive_casts.fortify_force_immune(enemy))
        self.assertEqual(tuple(enemy.mover.position), start)
        before = pools.total
        case.step(1)
        case.step(0, 22)
        self.assertAlmostEqual(before - pools.total, 195, places=4)
        self.assertGreater(enemy.mover.position[2], start[2])

    def test_fortify_removes_recon_head_critical_then_expires(self):
        case = self.arena("recon")
        enemy = case.enemy
        pools = case.health(enemy)
        x, y, z = enemy.mover.position
        case.command_aim = aim(case.fight._eye(case.player), (x, y + case.target(enemy).eye - 0.05, z))
        before = pools.total
        case.step(1)
        self.assertAlmostEqual(before - pools.total, 7, places=4)
        hit = case.player.client.messages(combat.HIT)[-1]
        self.assertFalse(hit["+0x78"]["+0x8"] & combat.CRITICAL)
        for _ in range(280):
            if defensive_casts.fortify_scale(enemy) is None:
                break
            case.step()
        self.assertIsNone(defensive_casts.fortify_scale(enemy))
        self.assertFalse(defensive_casts.fortify_force_immune(enemy))
        before = pools.total
        case.step(1)
        self.assertEqual(before - pools.total, 35)
        hit = case.player.client.messages(combat.HIT)[-1]
        self.assertTrue(hit["+0x78"]["+0x8"] & combat.CRITICAL)
        instance = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x0CF4)
        ability = instance.states[0]
        self.assertGreater(ability.remaining(), 0)
        case.run.step({enemy: (0, 0, 8)})
        self.assertIsNone(defensive_casts.fortify_scale(enemy))
        for _ in range(800):
            if ability.remaining() == 0:
                break
            case.step()
        self.assertEqual(ability.remaining(), 0)
        case.run.step({enemy: (0, 0, 8)})
        self.assertAlmostEqual(defensive_casts.fortify_scale(enemy), 0.6, places=5)


if __name__ == "__main__":
    unittest.main()


class BastionFusionDriverTests(unittest.TestCase):
    def arena(self):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 10), enemy_hero=defensive_casts.ORISA)
        return case

    @staticmethod
    def fire(case, yaw=-32768):
        for frame in range(36):
            case.run.step({case.enemy: (yaw, 0, 1 if frame == 0 else 0)})

    def test_fusion_driver_hits_each_bastion_form_and_spends_only_armour(self):
        for mode, expected in (("recon", 6), ("sentry", 4.4), ("tank", 4.4)):
            with self.subTest(mode=mode):
                case = self.arena()
                if mode == "sentry":
                    case.configure()
                elif mode == "tank":
                    case.tank()
                pools = case.health(case.player)
                self.fire(case)
                self.assertAlmostEqual(pools.now[world.ARMOUR_POOL], 100 - expected)
                self.assertEqual(pools.now[world.HEALTH_POOL], 200)
                self.assertEqual(len(case.enemy.client.messages(combat.HIT)), 1)

    def test_fusion_driver_obeys_aim_world_and_team_filters(self):
        for reason in ("aim", "wall", "team"):
            with self.subTest(reason=reason):
                case = self.arena()
                if reason == "wall":
                    case.wall(5)
                elif reason == "team":
                    match = case.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    case.player.team = case.enemy.team
                self.fire(case, yaw=0 if reason == "aim" else -32768)
                self.assertEqual(case.health(case.player).total, 300)
                self.assertFalse(case.enemy.client.messages(combat.HIT))
