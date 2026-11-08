"""Ultimate points from executed combat, eligible healing and lifecycle gates.

The authored cost/spend is resource-backed; 5 passive points/s and 1 point/HP
are explicitly reconstructed server rules rather than native-server proof.
"""

import unittest

import test_soldier_combat_lane as fixture
from test_game_combat import aim

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command


class SoldierUltimateChargeTests(unittest.TestCase):
    input = fixture.SoldierCombatLaneTests.input
    vitals = fixture.SoldierCombatLaneTests.vitals
    add_bot = fixture.SoldierCombatLaneTests.add_bot
    start_visor = fixture.SoldierCombatLaneTests.start_visor

    def arena(self):
        run, soldier, other = fixture.SoldierCombatLaneTests().arena()
        run.match.collision_world = flat_world(0.0)
        other.mover.position = (0.0, 0.0, 1.0)
        return run, soldier, other

    def charge(self, actor):
        return actor.body_script.component.vars[342].value()

    def test_default_passive_is_points_per_second_and_duplicate_input_cannot_add_points(self):
        run, soldier, _ = self.arena()
        script = soldier.body_script
        self.assertIsNone(script.ult_seconds)
        for _ in range(625):
            self.input(run, soldier)
        self.assertAlmostEqual(self.charge(soldier), 50, delta=0.001)
        before = self.charge(soldier)
        script.command(script.frame, 0)
        run.match.combat.command(soldier, Command(script.frame))
        self.assertEqual(self.charge(soldier), before)

    def test_rifle_mitigated_damage_generates_only_actual_pool_loss_once(self):
        run, soldier, other = self.arena()
        soldier.body_script.ult_seconds = 0
        fight = run.match.combat
        pool = combat.Vitals(other.body, 200, armour=50, shields=25)
        fight.vitals[other.body] = pool
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 1.0))
        for _ in range(15):
            self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(pool.total, 225)
        self.assertEqual(self.charge(soldier), 50)
        self.assertEqual(pool.ult_healing_deficit, 50)
        script = soldier.body_script
        script.command(script.frame, 1)
        fight.command(soldier, Command(script.frame, yaw=yaw, pitch=pitch, buttons=1))
        self.assertEqual(self.charge(soldier), 50)
        self.assertEqual(pool.total, 225)

    def test_overkill_grants_only_remaining_health_and_dead_targets_grant_no_more(self):
        run, soldier, other = self.arena()
        soldier.body_script.ult_seconds = 0
        pool = combat.Vitals(other.body, 10)
        run.match.combat.vitals[other.body] = pool
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        self.input(run, soldier, 1, yaw, pitch)
        self.assertTrue(pool.dead)
        self.assertEqual(self.charge(soldier), 10)
        for _ in range(25):
            self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(self.charge(soldier), 10)

    def test_miss_friendly_and_static_wall_cannot_generate_combat_charge(self):
        for case in ("miss", "ally", "wall"):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                soldier.body_script.ult_seconds = 0
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                if case == "miss":
                    yaw = 16384
                elif case == "ally":
                    other.team = soldier.team
                else:
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(self.vitals(run, other).total, 200)
                self.assertEqual(self.charge(soldier), 0)

    def test_authored_field_self_healing_awards_only_enemy_caused_missing_hp(self):
        for cause in ("enemy", "environment", "self"):
            with self.subTest(cause=cause):
                run, soldier, other = self.arena()
                soldier.body_script.ult_seconds = 0
                other.body_script.ult_seconds = 0
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is soldier)
                if cause == "enemy":
                    yaw, pitch = aim(fight._eye(other), (0.0, 0.8, -3.0))
                    self.input(run, other, 1, yaw, pitch)
                elif cause == "self":
                    fight._damage(soldier, target, 20, False, None, fight.tick)
                else:
                    fight.vitals_of(target).damage(20)
                self.assertEqual(fight.vitals_of(target).total, 180)
                self.input(run, soldier, 0x10)
                for _ in range(180):
                    self.input(run, soldier)
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertAlmostEqual(self.charge(soldier), 20 if cause == "enemy" else 0, places=5)
                self.assertEqual(fight.vitals_of(target).ult_healing_deficit, 0)
                charge = self.charge(soldier)
                for _ in range(80):
                    self.input(run, soldier)
                self.assertEqual(self.charge(soldier), charge)  # overhealing cannot farm charge

    def test_deployed_field_heals_after_caster_death_but_cannot_charge_the_dead(self):
        run, soldier, ally = self.arena()
        soldier.body_script.ult_seconds = 0
        ally.team = soldier.team
        ally.mover.position = soldier.mover.position
        attacker = self.add_bot(run, 1 - soldier.team, (20.0, 0.0, 20.0))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is ally)
        fight._damage(attacker, target, 100, False, None, fight.tick)
        self.input(run, soldier, 0x10)
        for _ in range(80):
            self.input(run, soldier)
        pool = fight.vitals_of(target)
        self.assertGreater(pool.total, 100)
        before = pool.total
        own = next(target for target in fight._targets() if target.owner is soldier)
        fight._damage(attacker, own, 200, False, None, fight.tick)
        self.assertTrue(fight.is_dead(soldier))
        charge = self.charge(soldier)
        for _ in range(80):
            self.input(run, soldier)
        self.assertGreater(pool.total, before)
        self.assertEqual(self.charge(soldier), charge)

    def test_field_healing_during_visor_cannot_charge_or_defer_points_until_expiry(self):
        run, soldier, ally = self.arena()
        ally.team = soldier.team
        ally.mover.position = soldier.mover.position
        attacker = self.add_bot(run, 1 - soldier.team, (20.0, 0.0, 20.0))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is ally)
        fight._damage(attacker, target, 100, False, None, fight.tick)
        self.start_visor(run, soldier)
        self.input(run, soldier, 0x10)
        for _ in range(240):
            self.input(run, soldier)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(self.charge(soldier), 0)
        self.assertEqual(fight.vitals_of(target).ult_healing_deficit, 0)
        for _ in range(400):
            self.input(run, soldier)
        self.assertEqual(self.charge(soldier), 0)


if __name__ == "__main__":
    unittest.main()
