"""Real combat/graph charge bridge; event eligibility and host clock reconstructed."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.native_replay import snapshot_hash
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040


class RoadhogChargeTests(unittest.TestCase):
    def session(self, distance=2):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + distance]
        hog.body_script.ult_seconds = 0
        targets = {t.owner: t for t in match.combat._targets()}
        return run, hog, enemy, targets

    def charge(self, hog):
        return hog.body_script.component.entity_var(ULT_CHARGE).value()

    def test_authored_passive_interval_multipliers_remainder_and_duplicate_input(self):
        run, hog, _, _ = self.session()
        script = hog.body_script
        script.ult_seconds = None
        script.component.set(9532, 2.0)
        script.component.set(12918, 3.0)
        for _ in range(61):
            run.step({})
        self.assertEqual(self.charge(hog), 0)
        for _ in range(2):
            run.step({})
        self.assertEqual(self.charge(hog), 30)
        self.assertEqual(script.passive_charge_ms, 8)
        before = snapshot_hash(script)
        script.passive_charge_ms = 0
        self.assertNotEqual(snapshot_hash(script), before)
        script.passive_charge_ms = 8
        hog.take_commands([Command(run.frame), Command(run.frame - 1)])
        self.assertEqual(self.charge(hog), 30)
        self.assertEqual(script.passive_charge_ms, 8)

    def test_actual_primary_slug_hook_and_melee_generate_charge_from_damage(self):
        for button, damage, ticks in ((1, 165, 20), (2, 50, 20), (8, 30, 100), (0x800, 30, 30)):
            with self.subTest(button=button):
                run, hog, enemy, targets = self.session(1.5)
                vitals = run.match.combat.vitals_of(targets[enemy])
                run.step({hog: (0, 0, button)})
                for _ in range(ticks):
                    run.step({})
                self.assertAlmostEqual(200 - vitals.total, damage, places=3)
                self.assertAlmostEqual(self.charge(hog), damage, places=3)

    def test_mitigated_actual_pool_loss_and_overkill_bound_charge(self):
        for pools in ((200, 50, 25), (10, 0, 0)):
            with self.subTest(pools=pools):
                run, hog, enemy, _ = self.session()
                vitals = combat.Vitals(enemy.body, *pools)
                run.match.combat.vitals[enemy.body] = vitals
                run.step({hog: (0, 0, 1)})
                for _ in range(20):
                    run.step({})
                lost = sum(pools) - vitals.total
                self.assertAlmostEqual(self.charge(hog), lost, places=3)
                self.assertLess(lost, 165)
                self.assertAlmostEqual(vitals.ult_healing_deficit, lost)
                if pools[0] == 10:
                    self.assertTrue(vitals.dead)
                    self.assertEqual(self.charge(hog), 10)
                    for _ in range(80):
                        run.step({hog: (0, 0, 1)})
                    self.assertEqual(self.charge(hog), 10)

    def test_miss_ally_wall_cannot_generate_points(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run, hog, enemy, targets = self.session()
                fight = run.match.combat
                if mode == "ally":
                    # The fixture map is FFA, so explicitly select team mode.
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = hog.team
                x, y, z = hog.mover.position
                wall = World([
                    (x - 5, y, z + 1, x + 5, y, z + 1, x + 5, y + 5, z + 1),
                    (x - 5, y, z + 1, x + 5, y + 5, z + 1, x - 5, y + 5, z + 1),
                ])
                with patch.object(fight, "_collision", return_value=wall if mode == "wall" else None):
                    run.step({hog: (16384 if mode == "miss" else 0, 0, 1)})
                    for _ in range(25):
                        run.step({})
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)
                self.assertEqual(self.charge(hog), 0)

    def test_actual_breather_damage_reduction_reduces_attackers_charge(self):
        run, hog, enemy, _ = self.session()
        run.match.switch_hero(enemy, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 2]
        enemy.body_script.ult_seconds = 0
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        vitals = run.match.combat.vitals_of(target)
        run.step({enemy: (-32768, 0, 16)})
        run.step({hog: (0, 0, 1)})
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(600 - vitals.total, 82.5, places=3)
        self.assertAlmostEqual(self.charge(hog), 82.5, places=3)

    def test_authored_damage_multiplier_and_explicit_zero_apply_to_actual_hits(self):
        run, hog, enemy, targets = self.session()
        component = hog.body_script.component
        component.set(9532, 2.0)
        component.set(13022, 3.0)
        run.step({hog: (0, 0, 2)})
        for _ in range(60):
            run.step({})
        self.assertEqual(self.charge(hog), 300)
        component.set(13022, 0.0)
        run.step({hog: (0, 0, 2)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.charge(hog), 300)
        self.assertEqual(run.match.combat.vitals_of(targets[enemy]).total, 100)

    def test_breather_earns_only_eligible_enemy_missing_hp_and_no_overheal(self):
        for cause in ("enemy", "self", "environment"):
            with self.subTest(cause=cause):
                run, hog, enemy, targets = self.session()
                fight = run.match.combat
                vitals = fight.vitals_of(targets[hog])
                if cause == "environment":
                    vitals.damage(100)
                    fight._sync_body_health(hog, vitals)
                else:
                    fight._damage(enemy if cause == "enemy" else hog,
                                  targets[hog], 100, False, None, run.frame)
                run.step({hog: (0, 0, 16)})
                for _ in range(180):
                    run.step({})
                self.assertEqual(vitals.total, 600)
                self.assertAlmostEqual(self.charge(hog), 100 if cause == "enemy" else 0, places=3)
                self.assertEqual(vitals.ult_healing_deficit, 0)

    def test_breather_charge_uses_authored_healing_multipliers(self):
        run, hog, enemy, targets = self.session()
        fight = run.match.combat
        hog.body_script.component.set(9532, 2.0)
        hog.body_script.component.set(13022, 3.0)
        fight._damage(enemy, targets[hog], 100, False, None, run.frame)
        run.step({hog: (0, 0, 16)})
        for _ in range(180):
            run.step({})
        self.assertEqual(fight.vitals_of(targets[hog]).total, 600)
        self.assertAlmostEqual(self.charge(hog), 600, places=3)

    def test_full_cost_whole_hog_spend_no_gain_while_active_then_resume(self):
        run, hog, enemy, targets = self.session()
        fight = run.match.combat
        # A live high-health fixture lets primary input earn the real cost.
        vitals = combat.Vitals(enemy.body, 20000)
        fight.vitals[enemy.body] = vitals
        cost = hog.body_script.component.entity_var(ULT_COST).value()
        for _ in range(2000):
            run.step({hog: (0, 0, 1)})
            if self.charge(hog) == cost:
                break
        self.assertEqual(self.charge(hog), cost)
        self.assertGreater(20000 - vitals.total, cost)
        for _ in range(60):
            run.step({})
        self.assertEqual(self.charge(hog), cost)
        hog.body_script.ult_seconds = None
        run.step({hog: (0, 0, 32)})
        self.assertEqual(self.charge(hog), 0)
        before = vitals.total
        for _ in range(350):
            run.step({})
        self.assertLess(vitals.total, before)
        self.assertEqual(self.charge(hog), 0)
        for _ in range(180):
            run.step({})
        self.assertGreater(self.charge(hog), 0)
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)
        run.step({hog: (0, 0, 2)})
        for _ in range(20):
            run.step({})
        self.assertGreater(self.charge(hog), 0)
        # Death guards the passive and late combat callbacks on this body.
        fight._damage(enemy, targets[hog], 2000, False, None, run.frame)
        ended = self.charge(hog)
        hog.body_script.ult_seconds = None
        for _ in range(80):
            run.step({})
        self.assertEqual(self.charge(hog), ended)


if __name__ == "__main__":
    unittest.main()
