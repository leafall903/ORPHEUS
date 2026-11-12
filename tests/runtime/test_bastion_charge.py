"""Executed Bastion charge sources; resource expressions, reconstructed host policy."""

import unittest
from dataclasses import replace

import test_bastion_combat as kit

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.script import expr


class BastionChargeTests(unittest.TestCase):
    @staticmethod
    def charge(case):
        return expr.to_float(case.player.body_script.component.entity_var(342).value())

    def test_default_passive_uses_authored_five_points_per_second(self):
        case = kit.BastionCombatTests()
        script = case.fixture()
        before = self.charge(case)
        case.step(0, 625)
        self.assertAlmostEqual(self.charge(case) - before, 50, places=4)
        before = self.charge(case)
        script.command(script.frame, 0)
        case.fight.command(case.player, Command(script.frame))
        self.assertEqual(self.charge(case), before)

    def test_real_recon_sentry_and_melee_damage_generate_actual_hp_points(self):
        for mode, buttons, frames, expected in (("recon", 1, 10, 40),
                                                ("sentry", 1, 10, 75),
                                                ("melee", 0x800, 30, 30)):
            with self.subTest(mode=mode):
                case = kit.BastionCombatTests()
                script = case.fixture(enemy_position=(0, 1, 1.4 if mode == "melee" else 10))
                if mode == "sentry":
                    case.configure()
                script.ult_seconds = 0
                script.set_ult_charge(0)
                before = case.health(case.enemy).total
                case.step(buttons, frames)
                self.assertEqual(before - case.health(case.enemy).total, expected)
                self.assertEqual(self.charge(case), expected)

    def test_mitigation_overkill_and_replayed_commands_use_actual_loss_once(self):
        for armour in (False, True):
            with self.subTest(armour=armour):
                case = kit.BastionCombatTests()
                script = case.fixture()
                script.ult_seconds = 0
                script.set_ult_charge(0)
                pool = combat.Vitals(case.enemy.body, 200 if armour else 10, 50 if armour else 0)
                case.fight.vitals[case.enemy.body] = pool
                before = pool.total
                case.step(1, 10)
                expected = 30 if armour else 10
                self.assertEqual(before - pool.total, expected)
                self.assertEqual(self.charge(case), expected)
                script.command(script.frame, 1)
                case.fight.command(case.player, Command(script.frame, buttons=1))
                self.assertEqual(self.charge(case), expected)
                self.assertEqual(before - pool.total, expected)

    def test_miss_wall_and_friendly_cannot_generate_damage_charge(self):
        for mode in ("miss", "wall", "friendly"):
            with self.subTest(mode=mode):
                case = kit.BastionCombatTests()
                script = case.fixture()
                script.ult_seconds = 0
                script.set_ult_charge(0)
                if mode == "wall":
                    case.wall(5)
                elif mode == "friendly":
                    case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                    case.enemy.team = case.player.team
                if mode == "miss":
                    for _ in range(10):
                        case.run.step({case.player: (16384, 0, 1)})
                else:
                    case.step(1, 10)
                self.assertEqual(case.health(case.enemy).total, 200)
                self.assertEqual(self.charge(case), 0)

    def test_authored_damage_multipliers_and_cost_cap(self):
        case = kit.BastionCombatTests()
        script = case.fixture()
        script.ult_seconds = 0
        script.set_ult_charge(0)
        script.component.set(9532, 2.0)
        script.component.set(13022, 3.0)
        case.step(1)
        self.assertEqual(case.health(case.enemy).total, 180)
        self.assertEqual(self.charge(case), 120)
        cost = expr.to_float(script.component.entity_var(338).value())
        script.set_ult_charge(cost - 1)
        case.step(0, 10)
        case.step(1)
        self.assertEqual(case.health(case.enemy).total, 160)
        self.assertEqual(self.charge(case), cost)

    def test_authored_passive_multipliers_and_death_retirement_gates(self):
        case = kit.BastionCombatTests()
        script = case.fixture()
        script.set_ult_charge(0)
        script.component.set(9532, 2.0)
        script.component.set(12918, 3.0)
        case.step(0, 625)
        self.assertEqual(self.charge(case), 300)
        case.fight._damage(case.enemy, case.target(case.player), 10000,
                           False, None, case.run.frame)
        before = self.charge(case)
        case.step(0, 125)
        self.assertEqual(self.charge(case), before)
        for value in (20, 0, -1, float("nan"), float("inf")):
            self.assertEqual(script.gain_ult_charge(value), 0)
        script.retire()
        self.assertEqual(script.gain_ult_charge(20), 0)

    def test_default_self_repair_earns_final_tank_charge_and_spends_it(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                case = kit.BastionCombatTests()
                script = case.fixture(enemy_hero=0x02E0000000000040)
                if sentry:
                    case.configure()
                mode = script.component.entity_var(1216)
                cost = expr.to_float(script.component.entity_var(338).value())
                script.ult_seconds = 0
                script.set_ult_charge(cost - 10)
                initial = mode.value()
                case.step(32)
                case.step(0, 10)
                self.assertEqual(mode.value(), initial)
                self.assertEqual(self.charge(case), cost - 10)

                own = case.health(case.player)
                own.damage(105)
                case.fight._sync_body_health(case.player, own)
                before = own.total
                case.step(2, 40)
                self.assertGreater(own.total - before, 10)
                self.assertEqual(self.charge(case), cost)
                case.step(0, 20)
                case.step(32)
                self.assertEqual(mode.value(), 5 if sentry else 4)
                self.assertEqual(self.charge(case), 0)
                case.step(0, 94)
                self.assertEqual(mode.value(), 6)

                enemy = case.health(case.enemy)
                before = enemy.total
                case.step(1)
                case.step(0, 25)
                self.assertEqual(before - enemy.total, 205)
                self.assertEqual(self.charge(case), 0)
                case.step(0, 600)
                self.assertEqual(mode.value(), 0)
                case.step(32)
                self.assertEqual(mode.value(), 0)
                self.assertEqual(self.charge(case), 0)

    def test_tank_fire_at_expiry_boundary_retires_after_recon_without_charge(self):
        for offset, prior_mode, resulting_mode, accepted in (
            (499, 6, 6, True), (500, 6, 7, True), (501, 7, 7, False),
        ):
            with self.subTest(offset=offset):
                case = kit.BastionCombatTests()
                script = case.fixture(enemy_position=(0, 1, 90))
                script.ult_seconds = 0
                mode = script.component.entity_var(1216)
                script.set_ult_charge(script.component.entity_var(kit.ULT_COST).value())
                case.step(32)
                case.step(0, 93)
                self.assertEqual(mode.value(), 6)
                case.step(0, offset - 1)
                self.assertEqual(mode.value(), prior_mode)
                case.step(1)
                self.assertEqual(mode.value(), resulting_mode)
                self.assertEqual(len(case.fight.rockets), int(accepted))
                if accepted:
                    self.assertFalse(case.fight.rockets[0].charge_eligible)
                case.step(0, 65)
                self.assertEqual(mode.value(), 0)
                self.assertEqual(case.health(case.enemy).total, 200)
                case.step(0, 40)
                self.assertEqual(case.health(case.enemy).dead, accepted)
                self.assertFalse(case.fight.rockets)
                self.assertEqual(self.charge(case), 0)

    def test_declared_charge_gate_and_suppression_block_points_then_recover(self):
        for field, blocked, restored in ((2426, False, True), (842, True, False)):
            with self.subTest(field=field):
                case = kit.BastionCombatTests()
                script = case.fixture()
                script.set_ult_charge(0)
                # Declared profile inputs, not proof of native gate initialization.
                script.component.set(field, blocked)
                case.step(1, 10)
                case.step(0, 63)
                self.assertEqual(case.health(case.enemy).total, 160)
                self.assertEqual(self.charge(case), 0)
                script.component.set(field, restored)
                case.step(0, 20)
                case.step(1, 10)
                self.assertEqual(case.health(case.enemy).total, 120)
                self.assertEqual(self.charge(case), 40)
                case.step(0, 63)
                self.assertEqual(self.charge(case), 45)

    def test_actual_self_repair_notification_runs_authored_qualified_healing_route(self):
        for profile in ("default", "closed", "suppressed", "target_blocked", "unknown"):
            with self.subTest(profile=profile):
                case = kit.BastionCombatTests()
                script = case.fixture()
                script.ult_seconds = 0
                script.set_ult_charge(0)
                self.assertIs(script.component.entity_var(2426).value(), True)
                if profile == "closed":
                    script.component.set(2426, False)
                if profile == "unknown":
                    script.component.set(2426, None)
                if profile == "suppressed":
                    script.component.set(842, True)
                if profile == "target_blocked":
                    script.component.set(17625, True)
                case.fight._damage(case.enemy, case.target(case.player), 90,
                                   False, None, case.run.frame)
                before = case.health(case.player).total
                case.step(2, 40)
                restored = case.health(case.player).total - before
                self.assertAlmostEqual(restored, 36, places=4)
                self.assertAlmostEqual(self.charge(case), restored if profile == "default" else 0,
                                       places=4)

    def test_qualified_self_repair_caps_to_real_healing_and_honors_graph_multipliers(self):
        case = kit.BastionCombatTests()
        script = case.fixture()
        script.ult_seconds = 0
        script.set_ult_charge(0)
        script.component.set(2426, True)
        script.component.set(9532, 2.0)
        script.component.set(13022, 3.0)
        own = case.health(case.player)
        case.fight._damage(case.enemy, case.target(case.player), 15,
                           False, None, case.run.frame)
        self.assertEqual(own.total, 290)  #15 incoming minus5 armour
        case.step(2, 60)
        self.assertEqual(own.total, 300)
        self.assertAlmostEqual(self.charge(case), 60, places=4)
        case.step(2, 100)
        self.assertAlmostEqual(self.charge(case), 60, places=4)

    def test_actual_tank_suppression_blocks_healing_charge_and_dead_source_emits_nothing(self):
        case = kit.BastionCombatTests()
        script = case.fixture(enemy_position=(10, 1, 0))
        script.ult_seconds = 0
        case.tank()
        script.component.set(2426, True)
        self.assertTrue(expr.truthy(script.component.entity_var(842).value()))
        case.fight._damage(case.enemy, case.target(case.player), 90,
                           False, None, case.run.frame)
        before = case.health(case.player).total
        case.step(2, 40)
        self.assertAlmostEqual(case.health(case.player).total - before, 36, places=4)
        self.assertEqual(self.charge(case), 0)
        case.fight._damage(case.enemy, case.target(case.player), 10000,
                           False, None, case.run.frame)
        sequence = script.component.seq.next_value
        script.report_self_healing(20)
        self.assertEqual(script.component.seq.next_value, sequence)


if __name__ == "__main__":
    unittest.main()
