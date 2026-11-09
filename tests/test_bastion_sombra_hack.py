"""Actual Sombra channel and Bastion skill/weapon input in 104319."""
import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import sombra_hack, stats
from ow174.game.script import expr, nodes
from ow174.game.script.driver import ULT_COST


class BastionSombraHackTests(unittest.TestCase):
    def arena(self, mode="recon", distance=7):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, distance), enemy_hero=sombra_hack.HERO)
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        instances = case.enemy.body_script.component.instances.values()
        weapon = next(i for i in instances if i.graph.index == 0x0B1B)
        return case, weapon

    @staticmethod
    def step(case, frames, *, cast=2, buttons=0, miss=False, stop_on_hack=False):
        angles = (16384, 0) if miss else aim(case.fight._eye(case.enemy), (0, 2, 0))
        for _ in range(frames):
            case.run.step({case.enemy: (*angles, cast), case.player: (*case.command_aim, buttons)})
            if stop_on_hack and case.player.body in case.fight.hacks:
                return case.fight.hacks[case.player.body]
        return None

    def test_real_hack_silences_skills_but_all_three_weapons_can_fire(self):
        for mode in ("recon", "sentry", "tank"):
            with self.subTest(mode=mode):
                case, weapon = self.arena(mode)
                script = case.player.body_script
                if mode != "tank":
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                own = case.health(case.player)
                own.damage(75)
                case.fight._sync_body_health(case.player, own)
                before = own.total
                hack = self.step(case, 70, stop_on_hack=True)
                self.assertIsNotNone(hack)
                self.assertEqual(hack.application.source_entity(), case.enemy.body)
                self.assertEqual(hack.until - case.fight.tick, 313)
                self.assertEqual(script.component.entity_var(6396).value(), expr.Entity(case.enemy.body))
                self.assertFalse(case.fight.is_stunned(case.player))
                self.assertGreater(weapon.state(53).remaining(), 6)
                self.assertEqual(own.total, before)
                form, charge = [script.component.entity_var(key).value() for key in (1216, 342)]
                self.step(case, 45, cast=0, buttons=2 | 8 | 32)
                self.assertEqual(own.total, before)
                self.assertEqual(script.component.entity_var(1216).value(), form)
                self.assertEqual(script.component.entity_var(342).value(), charge)
                self.assertEqual(case.player.stats.value(stats.HEALING), 0)
                enemy = case.health(case.enemy)
                before = enemy.total
                self.step(case, 12, cast=0, buttons=1)
                self.step(case, 15, cast=0)
                self.assertLess(enemy.total, before)
                self.assertIn(case.player.body, case.fight.hacks)

    def test_hack_during_all_five_transitions_preserves_authored_timers(self):
        transitions = (
            ("recon", 8, 1, 2, 62),
            ("sentry", 8, 3, 0, 31),
            ("recon", 32, 4, 6, 93),
            ("sentry", 32, 5, 6, 93),
            ("tank", 0, 7, 0, 63),
        )
        for initial, button, entered, final, boundary in transitions:
            with self.subTest(initial=initial, button=button):
                case, _ = self.arena(initial)
                script = case.player.body_script
                mode = script.component.entity_var(1216)
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                if button:
                    self.assertIsNone(self.step(case, 25, stop_on_hack=True))
                    self.step(case, 1, buttons=button)
                else:
                    for _ in range(600):
                        if mode.value() == entered:
                            break
                        case.step()
                start = case.run.frame
                self.assertEqual(mode.value(), entered)
                hack = self.step(case, 25 if button else 50, stop_on_hack=True)
                self.assertIsNotNone(hack)
                self.assertLess(case.run.frame, start + boundary)
                self.assertEqual(mode.value(), entered)
                health = case.health(case.enemy)
                before = health.total
                while case.run.frame < start + boundary - 1:
                    self.step(case, 1, cast=0)
                    self.assertEqual(mode.value(), entered)
                    self.assertEqual(health.total, before)
                self.step(case, 1, cast=0)
                self.assertEqual(mode.value(), final)
                self.step(case, 2, cast=0, buttons=8 | 32)
                self.assertEqual(mode.value(), final)
                self.step(case, 25, cast=0, buttons=1)
                self.assertLess(health.total, before)
                self.assertIs(case.fight.hacks[case.player.body], hack)

    def test_repair_is_interrupted_and_resumes_after_expiry(self):
        case, weapon = self.arena()
        script = case.player.body_script
        health = case.health(case.player)
        health.damage(160)
        case.fight._sync_body_health(case.player, health)
        case.step(2, 42)
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        self.assertTrue(repair.states[0].active)
        hack = self.step(case, 70, buttons=2, stop_on_hack=True)
        self.assertIsNotNone(hack)
        self.assertFalse(repair.states[0].active)
        before = health.total
        self.step(case, 2, cast=0)
        self.step(case, 65, buttons=2)
        self.assertIs(case.fight.hacks[case.player.body], hack)
        self.assertGreater(weapon.state(53).remaining(), 0)
        while case.run.frame < hack.until - 1:
            case.step(2)
        self.assertEqual(health.total, before)
        self.assertIn(case.player.body, case.fight.hacks)
        case.step(2, 45)
        self.assertNotIn(case.player.body, case.fight.hacks)
        self.assertGreater(health.total, before)
        self.assertEqual(script.component.entity_var(888).base, hack.base)

    def test_authored_cooldown_rejects_immediate_recast_and_allows_reuse(self):
        case, weapon = self.arena()
        first = self.step(case, 70, stop_on_hack=True)
        self.assertIsNotNone(first)
        self.step(case, 4, cast=0)
        self.step(case, 75)
        self.assertIs(case.fight.hacks[case.player.body], first)
        for _ in range(535):
            self.step(case, 1, cast=0)
            if weapon.state(53).remaining() == 0:
                break
        self.assertEqual(weapon.state(53).remaining(), 0)
        self.assertNotIn(case.player.body, case.fight.hacks)
        second = self.step(case, 70, stop_on_hack=True)
        self.assertIsNotNone(second)
        self.assertNotEqual(second.application.id, first.application.id)

    def test_miss_wall_range_team_and_release_do_not_apply_hack(self):
        for reason in ("miss", "wall", "range", "team", "release"):
            with self.subTest(reason=reason):
                case, weapon = self.arena(distance=18 if reason == "range" else 7)
                if reason == "wall":
                    case.wall(3)
                if reason == "team":
                    case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                    case.enemy.team = case.player.team
                self.step(case, 14 if reason == "release" else 90, miss=reason == "miss")
                self.step(case, 50 if reason == "release" else 2, cast=0, miss=reason == "miss")
                self.assertFalse(case.fight.hacks)
                if reason == "release":
                    self.assertFalse(weapon.state(53).flags & nodes.Ability.RUNNING)
                else:
                    self.assertFalse(weapon.vars[375].value())
                    self.assertEqual(weapon.state(53).remaining(), 0)

    def test_bastion_primary_interrupts_the_actual_channel(self):
        case, weapon = self.arena()
        self.step(case, 12)
        self.assertTrue(weapon.state(53).flags & nodes.Ability.RUNNING)
        before = case.health(case.enemy).total
        self.step(case, 1, buttons=1)
        self.step(case, 52)
        self.assertLess(case.health(case.enemy).total, before)
        self.assertFalse(case.fight.hacks)
        self.assertFalse(weapon.state(53).flags & nodes.Ability.RUNNING)
        self.assertGreater(weapon.state(53).remaining(), 0)
        self.assertLess(weapon.state(53).remaining(), 2)


if __name__ == "__main__":
    unittest.main()
