"""Soldier attacks against real authored defensive states, reconstructed consumers."""

import unittest

import test_soldier_combat_lane as lane
from test_game_combat import aim

from ow174.game import combat, defenses, stats
from ow174.game.collision import flat_world
from ow174.game.content import heroes


class SoldierDefensesTests(unittest.TestCase):
    arena = lane.SoldierCombatLaneTests.arena
    input = lane.SoldierCombatLaneTests.input
    vitals = lane.SoldierCombatLaneTests.vitals
    fly = lane.SoldierCombatLaneTests.fly
    add_bot = lane.SoldierCombatLaneTests.add_bot

    def opponent(self, hero):
        run, soldier, other = self.arena()
        self.assertTrue(run.match.switch_hero(other, heroes()[hero]))
        other.team = 1 - soldier.team
        other.mover.position = (0.0, 0.0, 1.0)
        run.match.collision_world = flat_world(0.0)
        soldier.body_script.ult_seconds = 0
        other.body_script.ult_seconds = 0
        self.input(run, other)
        self.input(run, soldier)
        run.match.combat.history.clear()  # fixture teleports have no earlier viewed pose
        return run, soldier, other

    def test_fortify_body_head_and_visor_rifle_use_same_reduction_before_armour(self):
        for head, visor in ((False, False), (True, False), (False, True)):
            with self.subTest(head=head, visor=visor):
                run, soldier, other = self.opponent(defenses.ORISA)
                self.input(run, other, 8)
                self.assertAlmostEqual(defenses.fortify_scale(other), 0.6)
                if visor:
                    lane.SoldierCombatLaneTests.start_visor(self, run, soldier)
                before = self.vitals(run, other).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.9 if head else 0.8, 1.0))
                self.input(run, soldier, 1, yaw, pitch)
                self.assertAlmostEqual(before - self.vitals(run, other).total, 7, places=4)
                self.assertFalse(soldier.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)
                self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(),
                                       0 if visor else 7, places=4)

    def test_fortify_helix_reduces_each_direct_and_splash_hit_then_armour(self):
        run, soldier, other = self.opponent(defenses.ORISA)
        self.input(run, other, 8)
        before = self.vitals(run, other).total
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        self.input(run, soldier, 2, yaw, pitch)
        rocket, = run.match.combat.rockets
        self.fly(run, rocket)
        self.assertAlmostEqual(before - self.vitals(run, other).total, 62, places=4)

    def test_fortify_melee_expires_and_does_not_leak_across_body_replacement(self):
        run, soldier, other = self.opponent(defenses.ORISA)
        other.mover.position = (0.0, 0.0, -1.5)
        self.input(run, other, 8)
        before = self.vitals(run, other).total
        self.input(run, soldier, 0x800)
        for _ in range(40):
            self.input(run, soldier)
        self.assertAlmostEqual(before - self.vitals(run, other).total, 13, places=4)
        for _ in range(270):
            self.input(run, other)
        self.assertIsNone(defenses.fortify_scale(other))
        self.assertTrue(run.match.switch_hero(other, heroes()[defenses.ORISA]))
        self.assertIsNone(defenses.fortify_scale(other))

    def test_transcendence_blocks_rifle_helix_melee_without_charge_then_expires(self):
        for button in (1, 2, 0x800):
            with self.subTest(button=button):
                run, soldier, other = self.opponent(defenses.ZENYATTA)
                other.mover.position = (0.0, 0.0, -1.5)
                script = other.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.input(run, other, 0x20)
                self.assertTrue(defenses.invulnerable(other))
                before = self.vitals(run, other).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, -1.5))
                self.input(run, soldier, button, yaw, pitch)
                for _ in range(45):
                    self.input(run, soldier)
                self.assertEqual(self.vitals(run, other).total, before)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                for _ in range(380):
                    self.input(run, other)
                self.assertFalse(defenses.invulnerable(other))
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(self.vitals(run, other).total, before - 20)

    def test_transcendence_requires_charge_blocks_visor_and_retires_with_body(self):
        run, soldier, other = self.opponent(defenses.ZENYATTA)
        self.input(run, other, 0x20)
        self.assertFalse(defenses.invulnerable(other))
        self.input(run, other)
        script = other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.input(run, other, 0x20)
        self.assertTrue(defenses.invulnerable(other))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        lane.SoldierCombatLaneTests.start_visor(self, run, soldier)
        before = self.vitals(run, other).total
        self.input(run, soldier, 1)
        self.assertEqual(self.vitals(run, other).total, before)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
        self.assertTrue(run.match.switch_hero(other, heroes()[defenses.ZENYATTA]))
        self.assertFalse(defenses.invulnerable(other))

    def test_transcendent_body_stops_rifle_instead_of_passing_it_to_an_enemy_behind(self):
        run, soldier, other = self.opponent(defenses.ZENYATTA)
        bot = self.add_bot(run, other.team, (0.0, 0.0, 4.0))
        script = other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.input(run, other, 0x20)
        self.assertTrue(defenses.invulnerable(other))
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.1, 4.0))
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(self.vitals(run, other).total, 200)
        self.assertEqual(self.vitals(run, bot).total, 200)


if __name__ == "__main__":
    unittest.main()
