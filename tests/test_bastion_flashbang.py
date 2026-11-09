"""Actual McCree Flashbang input against Bastion; reconstructed control policy.

These checks do not execute native224E, prove immunity/cleansing, or cover client
status presentation. Existing airborne projectiles retain their normal lifetime.
"""

import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.commands import Command
from ow174.game.script.driver import ULT_COST

MCCREE = 0x02E0000000000042


class BastionFlashbangTests(unittest.TestCase):
    def fixture(self, mode="recon", *, distance=3, repair_frames=50):
        case = self.case = kit.BastionCombatTests()
        script = case.fixture(enemy_position=(0, 1, distance), bystander_position=(10, 1, 10),
                              enemy_hero=MCCREE)
        if mode in ("sentry", "exit_sentry", "enter_tank_sentry"):
            case.configure()
        if mode in ("tank", "exit_tank"):
            case.tank()
        if mode == "repair":
            own = case.health(case.player)
            own.damage(100)
            case.fight._sync_body_health(case.player, own)
            case.step(2, repair_frames)
        if mode in ("enter_sentry", "exit_sentry"):
            case.step(8)
        if mode in ("enter_tank", "enter_tank_sentry"):
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            case.step(32)
        if mode == "exit_tank":
            for _ in range(600):
                if script.component.entity_var(1216).value() == 7:
                    break
                case.step()
            self.assertEqual(script.component.entity_var(1216).value(), 7)
        return script

    def step(self, buttons=0, *, cast=0, yaw=16384, forward=0, caster_miss=False, skip_player=False):
        case, run = self.case, self.case.run
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        point = case.player.mover.position
        caster_yaw, caster_pitch = aim(case.fight._eye(case.enemy), (point[0], point[1] + 1, point[2]))
        for actor in run.players:
            actor.client.queue_entities(run.match.remote_movements(actor))
            if actor is case.player and skip_player:
                continue
            if actor is case.enemy:
                command = Command(run.frame, yaw=caster_yaw + (16384 if caster_miss else 0),
                                  pitch=caster_pitch, buttons=cast)
            elif actor is case.player:
                command = Command(run.frame, yaw=yaw, pitch=case.command_aim[1],
                                  buttons=buttons, forward=forward)
            else:
                command = Command(run.frame)
            actor.take_commands([command])

    def cast(self, buttons=0, *, skip_player=False):
        self.step(buttons, cast=16, skip_player=skip_player)
        for _ in range(50):
            if self.case.fight.is_stunned(self.case.player):
                return self.case.fight.stuns[self.case.player.body]
            self.step(buttons, skip_player=skip_player)
        self.fail("actual Flashbang input did not control Bastion")

    def test_recon_sentry_tank_reject_held_attacks_and_motion_until_deadline_then_fire(self):
        for mode, damage, scalar in (("recon", 20, 1), ("sentry", 15, 0.8), ("tank", 15, 0.8)):
            with self.subTest(mode=mode):
                script = self.fixture(mode)
                self.step(1)  # start the real volley away from the caster
                end = self.cast(1)
                case = self.case
                hits = case.enemy.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [damage])
                self.assertEqual(end - case.fight.tick, 50)
                own, enemy = case.health(case.player).total, case.health(case.enemy).total
                position = tuple(case.player.mover.position)
                old_rockets = tuple(case.fight.rockets)
                while case.run.frame < end - 1:
                    self.step(1 | 2 | 8 | 32 | 0x400 | 0x800 | 0xC0, yaw=0, forward=127)
                    self.assertEqual(case.health(case.player).total, own)
                    self.assertEqual(case.health(case.enemy).total, enemy)
                    self.assertEqual(tuple(case.player.mover.position), position)
                    self.assertFalse(script.component.held)
                    self.assertAlmostEqual(case.fight._bastion_damage_scalar(case.player), scalar)
                    self.assertTrue(all(any(rocket is old for old in old_rockets)
                                        for rocket in case.fight.rockets if rocket.shooter is case.player))
                self.assertTrue(case.fight.is_stunned(case.player))
                for _ in range(25):
                    self.step(1, yaw=0)
                self.assertFalse(case.fight.is_stunned(case.player))
                self.assertLess(case.health(case.enemy).total, enemy)

    def test_melee_tap_during_flashbang_expires_and_held_input_waits_for_release(self):
        for held in (False, True):
            with self.subTest(held=held):
                script = self.fixture(distance=1.4)
                case = self.case
                end = self.cast()
                target = case.health(case.enemy)
                before = target.total
                ability = next(i for i in script.component.instances.values() if i.graph.index == 0x43)
                state = ability.states[6]
                self.step(0x800, yaw=0)
                while case.run.frame < end - 1:
                    self.step(0x800 if held else 0, yaw=0)
                self.assertEqual(target.total, before)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(state.presses)
                self.step(0x800 if held else 0, yaw=0)
                self.assertFalse(case.fight.is_stunned(case.player))
                if not held:
                    for _ in range(15):
                        self.step(0, yaw=0)
                    self.assertEqual(target.total, before)
                    self.step(0x800, yaw=0)
                for _ in range(12):
                    self.step(0, yaw=0)
                self.assertEqual(target.total, before - 30)
                self.assertEqual(len(case.player.client.messages(combat.HIT)), 1)
                self.assertGreater(state.remaining(), 0)

    def test_repair_is_interrupted_without_heal_replay_and_can_be_used_again(self):
        script = self.fixture("repair")
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        self.assertTrue(repair.states[0].active)
        end = self.cast(2)
        case = self.case
        own = case.health(case.player).total
        fuel = repair.vars[87].value()
        healed = case.player.stats.value(stats.HEALING)
        self.assertFalse(repair.states[0].active)
        while case.run.frame < end - 1:
            self.step(2)
            self.assertEqual(case.health(case.player).total, own)
            self.assertEqual(case.player.stats.value(stats.HEALING), healed)
            self.assertEqual(repair.vars[87].value(), fuel)
            self.assertFalse(script.self_repairs)
        for _ in range(50):
            self.step(2)
        self.assertGreater(case.health(case.player).total, own)
        self.assertGreater(case.player.stats.value(stats.HEALING), healed)

    def test_authored_transform_timers_continue_and_resulting_weapon_recovers(self):
        for mode, entered, final, boundary in (("enter_sentry", 1, 2, 62), ("exit_sentry", 3, 0, 31),
                                                ("enter_tank", 4, 6, 93),
                                                ("enter_tank_sentry", 5, 6, 93),
                                                ("exit_tank", 7, 0, 63)):
            with self.subTest(mode=mode):
                script = self.fixture(mode)
                self.assertEqual(script.component.entity_var(1216).value(), entered)
                start = self.case.run.frame
                end = self.cast()
                # Authored 1/.5/1.5/1.5/1-second timers, sampled at16ms. The
                # Tank exit begins inside a frame, so its final boundary is63.
                while self.case.run.frame < start + boundary - 1:
                    self.step()
                    self.assertEqual(script.component.entity_var(1216).value(), entered)
                self.step()
                self.assertEqual(script.component.entity_var(1216).value(), final)
                before = self.case.health(self.case.enemy).total
                while self.case.run.frame < end - 1:
                    self.step(1, yaw=0)
                    self.assertEqual(self.case.health(self.case.enemy).total, before)
                before = self.case.health(self.case.enemy).total
                self.step(1, yaw=0)
                for _ in range(20):
                    self.step(yaw=0)
                self.assertLess(self.case.health(self.case.enemy).total, before)

    def test_wall_aim_range_and_friendly_casts_do_not_damage_or_control_any_configuration(self):
        for mode in ("recon", "sentry", "tank"):
            for reason in ("wall", "aim", "range", "friendly"):
                with self.subTest(mode=mode, reason=reason):
                    self.fixture(mode, distance=20 if reason == "range" else 3)
                    case = self.case
                    if reason == "wall":
                        case.wall(1.5)
                    if reason == "friendly":
                        case.run.match.game_map = replace(case.run.match.game_map,
                                                         free_for_all=False, team_sizes=(6, 6))
                        case.player.team = case.enemy.team = 0
                    before = case.health(case.player).total
                    self.step(cast=16, caster_miss=reason == "aim")
                    for _ in range(150):
                        self.step(caster_miss=reason == "aim")
                        self.assertFalse(case.fight.is_stunned(case.player))
                        self.assertEqual(case.health(case.player).total, before)
                    self.assertFalse(case.fight.rockets)

    def test_pending_real_heal_report_is_retired_on_hit_even_without_target_commands(self):
        script = self.fixture("repair")
        script.command(self.case.run.frame + 1, 2)  # produce, but do not consume, the next real report
        self.assertEqual(len(script.self_repairs), 1)
        end = self.cast(skip_player=True)
        own = self.case.health(self.case.player).total
        self.assertFalse(script.self_repairs)
        while self.case.run.frame < end:
            self.step(skip_player=True)
        self.step()
        self.assertEqual(self.case.health(self.case.player).total, own)

    def test_death_during_control_clears_control_and_rejects_bastion_skills(self):
        script = self.fixture("tank")
        self.cast()
        case = self.case
        case.fight._damage(case.enemy, case.target(case.player), 10000, False, None, case.run.frame)
        self.assertNotIn(case.player.body, case.fight.stuns)
        self.assertFalse(script.self_repairs)
        self.assertFalse(script.control_windows)
        for _ in range(70):
            self.step(1 | 2 | 8 | 32)
        self.assertTrue(case.health(case.player).dead)
        self.assertEqual(case.health(case.player).total, 0)
        self.assertEqual(script.component.entity_var(1216).value(), 0)

    def test_early_minimum_window_does_not_backfill_healing_from_controlled_frames(self):
        for early in (16, 30):
            with self.subTest(repair_frames=early):
                script = self.fixture("repair", repair_frames=early)
                before_cast = self.case.run.frame
                end = self.cast(2, skip_player=True)
                hit_frame = self.case.run.frame
                own = self.case.health(self.case.player).total
                self.assertTrue(script.control_windows)
                while self.case.run.frame < end:
                    self.step(skip_player=True)
                    self.assertEqual(self.case.health(self.case.player).total, own)
                self.step()
                # Missed frames before impact may still heal. Frames inside the
                # exclusive control interval cannot, even after its live map entry
                # has expired and the old minimum-heal timer is drained at catch-up.
                expected = own + (hit_frame - before_cast - 1) * 90 * combat.TICK
                self.assertAlmostEqual(self.case.health(self.case.player).total, expected)
                self.assertFalse(script.self_repairs)
                self.assertFalse(script.control_windows)

    def test_body_replacement_clears_old_control_history_and_new_body_has_none(self):
        script = self.fixture("repair")
        self.cast(2)
        case = self.case
        old_body = case.player.body
        self.assertTrue(script.control_windows)
        self.assertTrue(case.run.match.switch_hero(case.player, case.player.hero))
        self.assertNotEqual(case.player.body, old_body)
        self.assertNotIn(old_body, case.fight.stuns)
        self.assertTrue(script.retired)
        self.assertFalse(script.control_windows)
        self.assertFalse(script.self_repairs)
        self.assertFalse(case.player.body_script.control_windows)

    def test_late_command_keeps_old_control_window_after_a_later_actual_cast(self):
        script = self.fixture("repair", repair_frames=16)
        before_cast = self.case.run.frame
        first_end = self.cast(2, skip_player=True)
        first_hit = self.case.run.frame
        for _ in range(700):  # the real caster's cooldown expires; target sends no commands
            self.step(skip_player=True)
        second_end = self.cast(skip_player=True)
        self.assertGreater(second_end, first_end)
        self.assertTrue(any(low == first_hit and high == first_end
                            for low, high in script.control_windows))
        while self.case.run.frame <= second_end:
            self.step(skip_player=True)
        own = self.case.health(self.case.player).total
        # A monotonic but late command is accepted after both live deadlines
        # expired. It still must not recreate healing from the first stun.
        self.case.player.take_commands([Command(first_hit + 20)])
        expected = own + (first_hit - before_cast - 1) * 90 * combat.TICK
        self.assertAlmostEqual(self.case.health(self.case.player).total, expected)

    def test_late_controlled_fire_and_movement_are_rejected_after_live_expiry(self):
        script = self.fixture()
        end = self.cast(skip_player=True)
        hit = self.case.run.frame
        while self.case.run.frame <= end + 2:
            self.step(skip_player=True)
        case = self.case
        enemy, position = case.health(case.enemy).total, tuple(case.player.mover.position)
        case.player.take_commands([Command(hit + 20, yaw=0, buttons=1, forward=127)])
        self.assertEqual(case.health(case.enemy).total, enemy)
        self.assertEqual(tuple(case.player.mover.position), position)
        self.assertFalse(script.component.held)
        for _ in range(25):
            self.step(1, yaw=0)
        self.assertLess(case.health(case.enemy).total, enemy)


if __name__ == "__main__":
    unittest.main()
