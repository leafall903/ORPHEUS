"""Actual command, authored charge graph and pool execution (static world only)."""

import copy
import unittest
from unittest.mock import patch

from test_symmetra_combat import arena, fire, health

from ow174.game import combat, npc, symmetra
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class PrimaryChargeTests(unittest.TestCase):
    def fixture(self):
        run, owner, enemy = arena()
        # A durable explicit test pool lets the same target survive every level.
        # This is not a claim about Soldier's authored maximum health.
        vitals = health(run, enemy)
        vitals.most[combat.HEALTH_POOL] = vitals.now[combat.HEALTH_POOL] = 10000
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == symmetra.WEAPON)
        return run, owner, enemy, weapon

    def test_real_sustained_input_advances_authored_levels_and_applies_each_rate(self):
        run, owner, enemy, weapon = self.fixture()
        rates, transitions = [], []
        previous = None
        for frame in range(1, 301):
            before = health(run, enemy).total
            fire(run, owner)
            rate = weapon.find_var(0, 499).value()
            self.assertAlmostEqual(before - health(run, enemy).total, rate * .016, places=5)
            if rate != previous:
                transitions.append(frame)
                rates.append(rate)
                previous = rate
        self.assertEqual(rates, [60, 120, 180])
        self.assertEqual(transitions, [1, 85, 169])
        self.assertEqual(weapon.find_var(0, 59).value(), 2)
        self.assertEqual(weapon.find_var(0, 2004).value(), 1)

    def test_release_waits_then_decays_one_level_at_a_time_and_rehit_charges_again(self):
        run, owner, enemy, weapon = self.fixture()
        fire(run, owner, 300)
        before = health(run, enemy).total
        fire(run, owner, 125, buttons=0)  # authored two-second wait
        self.assertEqual(weapon.find_var(0, 499).value(), 180)
        self.assertEqual(weapon.find_var(0, 2004).value(), 1)
        fire(run, owner, 130, buttons=0)  # authored .5/s decay, then one level removed
        self.assertEqual(weapon.find_var(0, 499).value(), 120)
        fire(run, owner, 125, buttons=0)
        self.assertEqual(weapon.find_var(0, 499).value(), 60)
        self.assertEqual(health(run, enemy).total, before)
        fire(run, owner, 130, buttons=0)  # finish the residual normalized progress at base level
        self.assertEqual(weapon.find_var(0, 2004).value(), 0)
        fire(run, owner, 90)
        self.assertEqual(weapon.find_var(0, 499).value(), 120)
        self.assertLess(health(run, enemy).total, before)

    def test_miss_range_wall_and_ally_do_not_charge_or_damage(self):
        for reason in ("miss", "range", "wall", "ally"):
            with self.subTest(reason=reason):
                run, owner, enemy, weapon = self.fixture()
                if reason == "range":
                    enemy.mover.position[2] = 30
                if reason == "ally":
                    run.match.game_map = run.match.game_map.__class__(
                        **{**run.match.game_map.__dict__, "free_for_all": False})
                    enemy.team = owner.team
                wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                              (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                collision = wall if reason == "wall" else run.match.collision_world
                with patch.object(run.match.combat, "_collision", return_value=collision):
                    fire(run, owner, 200, yaw=-32768 if reason == "miss" else 0)
                self.assertEqual(health(run, enemy).total, 10000)
                self.assertEqual(weapon.find_var(0, 499).value(), 60)
                self.assertEqual(expr.to_int(weapon.find_var(0, 59).value()), 0)
                self.assertEqual(expr.to_float(weapon.find_var(0, 2004).value()), 0)

    def test_target_change_retains_level_but_hero_switch_reinitializes_it(self):
        run, owner, enemy, weapon = self.fixture()
        fire(run, owner, 180)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        fight._damage(owner, target, 20000, False, None, run.frame)
        fire(run, owner, 30, buttons=0)
        soldier = heroes()[SOLDIER]
        other = npc.spawn(run.match, npc.Definition(SOLDIER, soldier.body, "Durable fixture", 10000),
                          (0, 1, 6), team=1-owner.team)
        fire(run, owner, 2, buttons=0)  # register the new body's pose for a command trace
        before = health(run, other).total
        fire(run, owner)
        self.assertAlmostEqual(before - health(run, other).total, 180 * .016, places=5)
        self.assertEqual(weapon.find_var(0, 16627).value(), expr.Entity(other.entity))
        run.match.switch_hero(owner, heroes()[SOLDIER])
        run.match.switch_hero(owner, heroes()[symmetra.HERO])
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == symmetra.WEAPON)
        self.assertEqual(weapon.find_var(0, 499).value(), 60)
        self.assertEqual(expr.to_int(weapon.find_var(0, 59).value()), 0)

    def test_world_adapter_checks_membership_team_life_and_exact_query_profile(self):
        run, owner, enemy, weapon = self.fixture()
        fire(run, owner, 2)
        world = owner.body_script.world
        exists, selected, other_exists = weapon.graph.state(25).field("m_condition")["m_configVars"]
        self.assertTrue(symmetra.target_query(world, exists, weapon))
        self.assertTrue(symmetra.target_query(world, selected, weapon))
        altered = copy.deepcopy(selected)
        altered["m_filter"]["m_84065FD1"] ^= 1
        self.assertIs(symmetra.target_query(world, altered, weapon), NotImplemented)
        weapon.write((0, 38), expr.Entity(0xAFFFFFF0))
        self.assertFalse(symmetra.target_query(world, other_exists, weapon))
        fire(run, owner, 3, yaw=-32768)
        self.assertFalse(symmetra.target_query(world, exists, weapon))
        self.assertFalse(symmetra.target_query(world, selected, weapon))
        fire(run, owner, 3)
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        run.match.combat._damage(owner, target, 20000, False, None, run.frame)
        self.assertTrue(symmetra.target_query(world, exists, weapon))  # retained dead body exists
        self.assertFalse(symmetra.target_query(world, selected, weapon))


if __name__ == "__main__":
    unittest.main()
