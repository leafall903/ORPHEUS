"""Actual Ana dart input and Bastion control timing for 104319."""
import unittest

import test_bastion_ana_healing as healing
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class BastionAnaSleepTests(unittest.TestCase):
    @staticmethod
    def throw(case, buttons=0):
        ana = case.enemy
        yaw, pitch = aim(case.fight._eye(ana), (0, 2, 0))
        case.run.step({ana: (yaw, pitch, 8), case.player: (*case.command_aim, buttons)})
        for _ in range(60):
            if case.player.body in case.fight.sleeps:
                return case.fight.sleeps[case.player.body]
            case.run.step({ana: (yaw, pitch, 0), case.player: (*case.command_aim, buttons)})
        return None

    def test_dart_interrupts_active_repair_and_suppresses_input_until_natural_wake(self):
        case, health = healing.BastionAnaHealingTests().arena()
        case.step(2, 50)
        script = case.player.body_script
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        self.assertTrue(repair.states[0].active)
        sleep = self.throw(case, 2)
        self.assertIsNotNone(sleep)
        self.assertEqual(sleep.end - sleep.start, 313)
        self.assertFalse(repair.states[0].active)
        self.assertEqual(script.sleep_windows, [(sleep.start, sleep.end)])
        before, healed = health.total, case.player.stats.value(stats.HEALING)
        fuel = repair.vars[87].value()
        mode = script.component.entity_var(1216).value()
        while case.run.frame < sleep.end - 1:
            case.step(1 | 2 | 8 | 0x800)
        self.assertEqual(health.total, before)
        self.assertEqual(case.player.stats.value(stats.HEALING), healed)
        self.assertLess(fuel, 100)
        self.assertEqual(repair.vars[87].value(), 100)
        self.assertEqual(script.component.entity_var(1216).value(), mode)
        self.assertFalse(script.self_repairs)
        self.assertFalse(case.player.client.messages(combat.HIT))
        self.assertTrue(case.fight.is_stunned(case.player))
        case.step(2, 50)
        self.assertFalse(case.fight.is_stunned(case.player))
        self.assertGreater(health.total, before)
        self.assertFalse(script.sleep_windows)

    def test_sleep_spans_tank_expiry_and_exit_without_retaining_tank_fire(self):
        case, _ = healing.BastionAnaHealingTests().arena()
        script = case.player.body_script
        mode = script.component.entity_var(1216)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        case.step(32)
        case.step(0, 93)
        self.assertEqual(mode.value(), 6)
        active = case.run.frame
        case.step(0, 450)
        sleep = self.throw(case)
        self.assertIsNotNone(sleep)
        self.assertLess(sleep.start, active + 500)
        self.assertGreater(sleep.end, active + 563)
        case.step(1, active + 499 - case.run.frame)
        self.assertEqual(mode.value(), 6)
        self.assertTrue(case.fight.is_stunned(case.player))
        case.step(1)
        self.assertEqual(mode.value(), 7)
        case.step(1, 62)
        self.assertEqual(mode.value(), 7)
        case.step(1)
        self.assertEqual(mode.value(), 0)
        self.assertTrue(case.fight.is_stunned(case.player))
        self.assertFalse(case.fight.rockets)
        self.assertFalse(case.player.client.messages(combat.HIT))
        case.step(0, sleep.end - case.run.frame + 1)
        self.assertFalse(case.fight.is_stunned(case.player))
        before = case.health(case.enemy).total
        case.step(1, 10)
        self.assertLess(case.health(case.enemy).total, before)
        self.assertFalse(case.fight.rockets)

    def test_early_damage_respects_grace_and_wake_does_not_extend_on_a_later_hit(self):
        case, health = healing.BastionAnaHealingTests().arena()
        sleep = self.throw(case)
        self.assertIsNotNone(sleep)
        self.assertEqual(health.total, 130)
        original = sleep.end
        rifle = case.bystander
        yaw, pitch = aim(case.fight._eye(rifle), (0, 2, 0))

        def damage():
            before = health.total
            case.run.step({rifle: (yaw, pitch, 1)})
            case.step(0, 2)
            self.assertLess(health.total, before)

        damage()
        self.assertFalse(sleep.waking)
        self.assertEqual(sleep.end, original)
        while case.run.frame < sleep.start + 34:
            case.step()
        damage()
        self.assertTrue(sleep.waking)
        self.assertLess(sleep.end, original)
        self.assertEqual(sleep.end - case.fight.tick, 30)
        deadline = sleep.end
        case.step(0, 12)
        damage()
        self.assertEqual(sleep.end, deadline)
        script = case.player.body_script
        self.assertTrue(script.controlled_at(deadline - 1))
        self.assertFalse(script.controlled_at(deadline))
        while case.run.frame < deadline - 1:
            case.step(1)
        self.assertTrue(case.fight.is_stunned(case.player))
        before = case.health(case.enemy).total
        case.step(1, 10)
        self.assertLess(case.health(case.enemy).total, before)

    def test_active_sentry_tank_and_in_progress_reconfigure_use_authored_timers(self):
        for form in ("sentry", "tank", "transforming"):
            with self.subTest(form=form):
                case, health = healing.BastionAnaHealingTests().arena()
                if form == "sentry":
                    case.configure()
                elif form == "tank":
                    case.tank()
                else:
                    case.step(8)
                script = case.player.body_script
                self.assertEqual(script.component.entity_var(1216).value(),
                                 {"sentry": 2, "tank": 6, "transforming": 1}[form])
                before = health.total
                sleep = self.throw(case)
                self.assertIsNotNone(sleep)
                self.assertLess(health.total, before)
                enemy_health = case.health(case.enemy)
                enemy_before = enemy_health.total
                while case.run.frame < sleep.end - 1:
                    case.step(1)
                self.assertEqual(enemy_health.total, enemy_before)
                self.assertEqual(script.component.entity_var(1216).value(), 6 if form == "tank" else 2)
                case.step(1, 24)
                self.assertLess(enemy_health.total, enemy_before)

    def test_flashbang_wake_preserves_the_independent_stun_deadline(self):
        case, _ = healing.BastionAnaHealingTests().arena()
        caster = case.bystander
        self.assertTrue(case.run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.mover.position[:] = (3, 1, 0)
        caster.mover.body = None
        case.step(0, 30)
        sleep = self.throw(case)
        self.assertIsNotNone(sleep)
        while case.run.frame < sleep.start + 34:
            case.step()
        yaw, pitch = aim(case.fight._eye(caster), (0, 2, 0))
        case.run.step({caster: (yaw, pitch, 16)})
        for _ in range(50):
            if case.player.body in case.fight.stuns:
                break
            case.run.step({caster: (yaw, pitch, 0)})
        stun_end = case.fight.stuns[case.player.body]
        self.assertTrue(sleep.waking)
        self.assertLess(sleep.end, stun_end)
        script = case.player.body_script
        while case.run.frame < sleep.end:
            case.step(1)
        self.assertNotIn(case.player.body, case.fight.sleeps)
        self.assertTrue(case.fight.is_stunned(case.player))
        self.assertTrue(script.controlled_at(stun_end - 1))
        self.assertFalse(script.controlled_at(stun_end))
        before = case.health(case.enemy).total
        while case.run.frame < stun_end - 1:
            case.step(1)
        self.assertEqual(case.health(case.enemy).total, before)
        case.step(1, 10)
        self.assertLess(case.health(case.enemy).total, before)

    def test_death_discards_the_sleep_and_its_pending_input_window(self):
        case, health = healing.BastionAnaHealingTests().arena()
        health.damage(health.total - 10)
        case.fight._sync_body_health(case.player, health)
        sleep = self.throw(case)
        self.assertIsNotNone(sleep)
        self.assertEqual(health.total, 5)
        rifle = case.bystander
        yaw, pitch = aim(case.fight._eye(rifle), (0, 2, 0))
        script = case.player.body_script
        case.run.step({rifle: (yaw, pitch, 1)})
        case.step(2, 5)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, rifle.body)
        self.assertNotIn(case.player.body, case.fight.sleeps)
        self.assertFalse(script.sleep_windows)
        self.assertEqual(health.total, 0)

    def test_wall_and_friendly_dart_do_not_apply_sleep(self):
        for reason in ("wall", "friendly"):
            with self.subTest(reason=reason):
                case, health = healing.BastionAnaHealingTests().arena(friendly=reason == "friendly")
                if reason == "wall":
                    case.wall(3)
                before = health.total
                self.assertIsNone(self.throw(case))
                self.assertEqual(health.total, before)
                self.assertFalse(case.fight.is_stunned(case.player))


if __name__ == "__main__":
    unittest.main()
