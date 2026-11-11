"""Actual pistol reload resource/input lifecycle; no direct ammo writes."""

import unittest
from unittest.mock import patch

import test_game_mercy_staff as staff
import test_mccree_flashbang as flashbang

from ow174.game import combat, stats
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes

RELOAD = 0x400
MCCREE = 0x02E0000000000042


class MercyReloadLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.f = staff.MercyStaffTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.step(35)
        self.step(1, action=2)
        self.step(35)
        self.weapon = next(i for i in self.f.mercy.body_script.component.instances.values()
                           if i.graph.index == 0x6D)

    def step(self, count, buttons=0, *, action=0):
        f = self.f
        for index in range(count):
            f.run.frame += 1
            f.match.update(f.run.frame * combat.TICK, f.run.frame)
            for player in f.run.players:
                player.take_commands([Command(f.run.frame, yaw=16384 if player is f.mercy else 0,
                    buttons=buttons if player is f.mercy else 0,
                    action=action if player is f.mercy and index == 0 else 0)])

    def ammo(self):
        return self.weapon.find_var(0, 53).value()

    def test_held_primary_empty_magazine_automatically_reloads_and_resumes_real_shots(self):
        empty = recovered = False
        with patch.object(self.f.fight, "_shoot", wraps=self.f.fight._shoot) as shots:
            for _ in range(500):
                self.step(1, FIRE)
                amount = self.ammo()
                self.assertGreaterEqual(amount, 0)
                self.assertLessEqual(amount, 20)
                empty |= amount == 0
                recovered |= empty and amount > 0
                if recovered and shots.call_count > 20:
                    break
            self.assertTrue(empty)
            self.assertTrue(recovered)
            self.assertGreater(shots.call_count, 20)
        self.assertFalse(self.f.mercy.body_script_failed)

    def test_partial_magazine_manual_reload_takes_time_and_restores_twenty(self):
        self.step(1, FIRE)
        self.step(20)
        self.assertEqual(self.ammo(), 19)
        self.step(1, RELOAD)
        self.step(1)
        self.assertLess(self.ammo(), 20)
        self.step(120)
        self.assertEqual(self.ammo(), 20)
        with patch.object(self.f.fight, "_shoot", wraps=self.f.fight._shoot) as shots:
            self.step(1, FIRE)
            self.assertEqual(shots.call_count, 1)
            self.assertEqual(self.ammo(), 19)

    def test_full_magazine_reload_is_rejected_without_blocking_a_fresh_shot(self):
        self.assertEqual(self.ammo(), 20)
        self.step(1, RELOAD)
        with patch.object(self.f.fight, "_shoot", wraps=self.f.fight._shoot) as shots:
            self.step(1, FIRE)
            self.assertEqual(shots.call_count, 1)
            self.assertEqual(self.ammo(), 19)

    def begin_reload_against_mccree(self):
        owner = self.f
        self.assertTrue(owner.match.switch_hero(owner.enemy, heroes()[MCCREE]))
        owner.enemy.mover.position[:] = (3, 1, 0)
        self.step(35)
        self.step(1, FIRE)
        self.step(20)
        self.step(1, RELOAD)
        self.step(8)
        reload_state = next(state for state in self.weapon.states.values() if state.node.pos == 54)
        self.assertTrue(reload_state.active)
        self.assertEqual(self.ammo(), 19)
        return reload_state

    def test_flashbang_blocks_switch_and_blaster_fire_while_reload_completes(self):
        owner = self.f
        reload_state = self.begin_reload_against_mccree()
        slot = owner.mercy.body_script.component.entity_var(31)
        self.assertEqual(slot.value(), 2)
        flash = flashbang.FlashbangTests()
        target = next(target for target in owner.fight._targets() if target.owner is owner.enemy)
        vitals = owner.fight.vitals_of(target)
        before = vitals.total
        with patch.object(owner.fight, "_shoot", wraps=owner.fight._shoot) as shots:
            flash.cast(owner.run, owner.enemy, owner.mercy, target_buttons=FIRE)
            self.assertTrue(reload_state.active)
            self.assertEqual(self.ammo(), 19)
            end = owner.fight.stuns[owner.mercy.body]
            while owner.run.frame < end - 1:
                self.step(1, FIRE, action=1)
                self.assertEqual(slot.value(), 2)
            self.assertTrue(owner.fight.is_stunned(owner.mercy))
            self.assertEqual(self.ammo(), 20)
            self.assertFalse(reload_state.active)
            self.assertEqual(vitals.total, before)
            self.assertFalse(any(call.args[0] is owner.mercy for call in shots.call_args_list))

            for _ in range(42):
                flash.step(owner.run, owner.enemy, owner.mercy)
            self.assertFalse(owner.fight.is_stunned(owner.mercy))
            self.assertEqual(slot.value(), 2)
            flash.step(owner.run, owner.enemy, owner.mercy, target_buttons=FIRE)
            self.assertEqual(self.ammo(), 19)
            for _ in range(12):
                flash.step(owner.run, owner.enemy, owner.mercy)
            [shot] = [call for call in shots.call_args_list if call.args[0] is owner.mercy]
            self.assertIs(shot.args[2].instance, self.weapon)
            self.assertEqual(shot.args[2].node.pos, 10)
            self.assertEqual(vitals.total, before - 40)

            self.step(1, action=1)
            self.assertEqual(slot.value(), 1)
            before_healing = owner.vitals.total
            owner.step(55)
            self.assertGreater(owner.vitals.total, before_healing)
            self.assertEqual([call for call in shots.call_args_list if call.args[0] is owner.mercy], [shot])

    def test_lethal_flashbang_cancels_reload_and_respawn_starts_a_fresh_magazine(self):
        owner = self.f
        reload_state = self.begin_reload_against_mccree()
        old_body = owner.mercy.body
        old_script = owner.mercy.body_script
        target = next(target for target in owner.fight._targets() if target.owner is owner.mercy)
        vitals = owner.fight.vitals_of(target)
        owner.fight._damage(owner.enemy, target, 180, False, None, owner.run.frame)
        self.assertEqual(vitals.total, 20)
        before_eliminations = owner.enemy.stats.value(stats.ELIMINATIONS)
        flash = flashbang.FlashbangTests()

        with patch.object(owner.fight, "_shoot", wraps=owner.fight._shoot) as shots:
            flash.step(owner.run, owner.enemy, owner.mercy, cast=16, target_buttons=FIRE)
            for _ in range(30):
                if owner.fight.is_dead(owner.mercy):
                    break
                flash.step(owner.run, owner.enemy, owner.mercy, target_buttons=FIRE)
            self.assertTrue(vitals.dead)
            self.assertTrue(old_script.component.owner_dead)
            self.assertFalse(reload_state.active)
            self.assertEqual(owner.enemy.stats.value(stats.ELIMINATIONS), before_eliminations + 1)

            # Cross the original 910 ms refill and 1400 ms reload-end deadlines.
            self.step(100, FIRE)
            self.assertEqual(owner.mercy.body, old_body)
            self.assertEqual(self.ammo(), 19)
            self.assertFalse(reload_state.active)
            self.assertFalse(any(call.args[0] is owner.mercy for call in shots.call_args_list))

            for _ in range(250):
                if owner.mercy.body != old_body:
                    break
                self.step(1)
            self.assertNotEqual(owner.mercy.body, old_body)
            self.assertIsNot(owner.mercy.body_script, old_script)
            self.assertFalse(owner.fight.is_dead(owner.mercy))
            self.step(35)
            self.step(1, action=2)
            self.step(35)
            fresh_weapon = next(instance for instance in owner.mercy.body_script.component.instances.values()
                                if instance.graph.index == 0x6D)
            magazine = fresh_weapon.find_var(0, 53)
            self.assertEqual(magazine.value(), 20)
            self.step(1, FIRE)
            self.assertEqual(magazine.value(), 19)
            [shot] = [call for call in shots.call_args_list if call.args[0] is owner.mercy]
            self.assertIs(shot.args[2].instance, fresh_weapon)
            self.assertEqual(shot.args[2].node.pos, 10)
            self.assertEqual(self.ammo(), 19)

    def test_reload_refills_at_authored_checkpoint_but_blocks_fire_until_animation_end(self):
        self.step(1, FIRE)
        self.step(20)
        self.step(1, RELOAD)
        # Commands finish at the end of their16ms prediction window: the
        # refill at reload-start+910ms falls in the56th subsequent frame.
        self.step(55)
        self.assertEqual(self.ammo(), 19)
        self.step(1)
        self.assertEqual(self.ammo(), 20)
        with patch.object(self.f.fight, "_shoot", wraps=self.f.fight._shoot) as shots:
            self.step(1, FIRE)
            self.assertEqual(shots.call_count, 0)
            self.assertEqual(self.ammo(), 20)
            self.step(35)
            self.step(1, FIRE)
            self.assertEqual(shots.call_count, 1)
            self.assertEqual(self.ammo(), 19)

    def test_empty_magazine_reloads_after_primary_is_released(self):
        for _ in range(300):
            self.step(1, FIRE)
            if self.ammo() == 0:
                break
        self.assertEqual(self.ammo(), 0)
        self.step(120)
        self.assertEqual(self.ammo(), 20)
        with patch.object(self.f.fight, "_shoot", wraps=self.f.fight._shoot) as shots:
            self.step(1, FIRE)
            self.assertEqual(shots.call_count, 1)
            self.assertEqual(self.ammo(), 19)
