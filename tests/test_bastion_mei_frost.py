"""Actual Mei spray and Bastion movement and ability input."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, defensive_casts, stats
from ow174.game.commands import Command
from ow174.game.content import heroes


class BastionFrostTests(unittest.TestCase):
    def arena(self, form="recon"):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 5), enemy_hero=defensive_casts.MEI)
        if form == "sentry":
            case.configure()
        elif form == "tank":
            case.tank()
        return case

    @staticmethod
    def move(case, yaw=0):
        case.run.frame += 1
        frame = case.run.frame
        case.run.match.update(frame * combat.TICK, frame)
        case.player.take_commands([Command(frame, yaw=yaw, right=127)])

    @staticmethod
    def spray(case, frame_limit, buttons=0, until_frozen=False):
        yaw, pitch = aim(case.fight._eye(case.enemy), (0, 2, 0))
        for _ in range(frame_limit):
            case.run.step({case.enemy: (yaw, pitch, 1), case.player: (*case.command_aim, buttons)})
            sample = case.fight.frost.get(case.player.body)
            if until_frozen and sample is not None and sample.frozen(case.fight.tick):
                return sample
        return None

    def test_thirtieth_contact_freezes_all_forms_and_fresh_fire_recovers(self):
        for form in ("recon", "sentry", "tank"):
            with self.subTest(form=form):
                case = self.arena(form)
                with patch.object(case.fight, "_apply_frost", wraps=case.fight._apply_frost) as hits:
                    sample = self.spray(case, 135, until_frozen=True)
                self.assertIsNotNone(sample)
                self.assertEqual(hits.call_count, 30)
                self.assertEqual(sample.frozen_until - sample.frozen_from, 1300)
                deadline = (sample.frozen_until + 15) // 16
                self.spray(case, 10, buttons=1)
                self.assertEqual((sample.frozen_until + 15) // 16, deadline)
                script = case.player.body_script
                self.assertEqual(script.component.entity_var(2427).value(), 1.0)
                self.assertTrue(script.controlled_at(deadline - 1))
                self.assertFalse(script.controlled_at(deadline))
                position = tuple(case.player.mover.position)
                self.move(case, yaw=16384)
                self.assertEqual(tuple(case.player.mover.position), position)
                self.assertEqual(case.player.mover.yaw, sample.yaw)
                enemy = case.health(case.enemy)
                before = enemy.total
                while case.run.frame < deadline - 1:
                    case.step(1 | 2 | 8)
                self.assertEqual(enemy.total, before)
                self.assertFalse(case.player.client.messages(combat.HIT))
                case.step(1, 22)
                self.assertLess(enemy.total, before)
                self.assertNotIn(case.player.body, case.fight.frost)
                self.assertEqual(script.component.entity_var(2427).value(), 0)

    def test_frost_slows_actual_movement_and_partial_progress_expires(self):
        slowed, control = self.arena(), self.arena()
        self.spray(slowed, 15)
        sample = slowed.fight.frost[slowed.player.body]
        progress = sample.progress(slowed.fight.tick * 16)
        self.assertGreaterEqual(progress, 0.2)
        self.assertLess(progress, 1.0)
        before = [case.player.mover.position[0] for case in (slowed, control)]
        for case in (slowed, control):
            for _ in range(20):
                self.move(case)
        delta = [abs(case.player.mover.position[0] - x)
                 for case, x in zip((slowed, control), before, strict=True)]
        self.assertGreater(delta[0], 0)
        self.assertLess(delta[0], delta[1])
        slowed.step(0, 70)
        self.assertNotIn(slowed.player.body, slowed.fight.frost)

    def test_freeze_interrupts_repair_and_thaw_allows_healing(self):
        case = self.arena()
        health = case.health(case.player)
        health.damage(150)
        case.fight._sync_body_health(case.player, health)
        case.step(2, 45)
        script = case.player.body_script
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        self.assertTrue(repair.states[0].active)
        sample = self.spray(case, 135, buttons=2, until_frozen=True)
        self.assertIsNotNone(sample)
        self.assertFalse(repair.states[0].active)
        before = case.player.stats.value(stats.HEALING)
        deadline = (sample.frozen_until + 15) // 16
        while case.run.frame < deadline - 1:
            case.step(2)
        self.assertEqual(case.player.stats.value(stats.HEALING), before)
        case.step(2, 45)
        self.assertGreater(case.player.stats.value(stats.HEALING), before)

    def test_two_sources_default_to_maximum_and_explicit_stacking_combines(self):
        for stacking in (False, True):
            with self.subTest(stacking=stacking):
                case = self.arena()
                second = case.bystander
                self.assertTrue(case.run.match.switch_hero(second, heroes()[defensive_casts.MEI]))
                case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                case.player.team = 0
                case.enemy.team = second.team = 1
                second.mover.position[:] = (4, 1, 3)
                second.mover.body = None
                case.step(0, 20)
                if stacking:
                    # This slot is the declared proxy for the authored foreign source setting.
                    second.body_script.component.set(0x301F, True)
                first_aim = aim(case.fight._eye(case.enemy), (0, 2, 0))
                second_aim = aim(case.fight._eye(second), (0, 2, 0))
                for _ in range(72):
                    case.run.step({case.enemy: (*first_aim, 1), second: (*second_aim, 1)})
                    sample = case.fight.frost.get(case.player.body)
                    if sample is not None and sample.frozen(case.fight.tick):
                        break
                self.assertIsNotNone(sample)
                self.assertEqual(set(sample.sources), {(case.enemy.body, 0), (second.body, 0)})
                self.assertEqual(sample.frozen(case.fight.tick), stacking)
                if not stacking:
                    self.assertLess(sample.progress(case.fight.tick * 16), 1.0)

    def test_wall_and_friendly_spray_never_build_frost(self):
        for scenario in ("wall", "friendly"):
            with self.subTest(scenario=scenario):
                case = self.arena()
                if scenario == "wall":
                    case.wall(2.5)
                else:
                    case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                    case.enemy.team = case.player.team
                health = case.health(case.player)
                before = health.total
                self.spray(case, 100)
                self.assertEqual(health.total, before)
                self.assertNotIn(case.player.body, case.fight.frost)


if __name__ == "__main__":
    unittest.main()
