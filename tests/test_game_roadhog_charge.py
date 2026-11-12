"""Real combat/graph charge bridge; event eligibility and host clock reconstructed."""

import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

ROADHOG = 0x02E0000000000040


def snapshot_hash(script):
    """Use the actual combined strict body domain for the original hash probe."""
    return snapshot_digest(export_body_vm(script))


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

    def torso_aim(self, run, hog, enemy):
        x, y, z = enemy.mover.position
        return aim(run.match.combat._eye(hog), (x, y + 0.7, z))

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

    def test_roadhog_victim_passive_reduces_enemy_damage_credit(self):
        run, shooter, victim, _ = self.session()
        match = run.match
        position = tuple(victim.mover.position)
        match.switch_hero(victim, heroes()[ROADHOG])
        victim.mover.position[:] = position
        victim.mover.body = None
        victim.body_script.ult_seconds = None
        target = next(t for t in match.combat._targets() if t.owner is victim)
        vitals = match.combat.vitals_of(target)
        run.step({shooter: (*self.torso_aim(run, shooter, victim), 2)})
        for _ in range(125):
            run.step({})
        self.assertEqual(vitals.total, 550)
        self.assertEqual(self.charge(shooter), 37.5)
        self.assertEqual(self.charge(victim), 10)
        self.assertEqual(vitals.ult_healing_deficit, 50)

    def test_actual_primary_slug_hook_and_melee_generate_charge_from_damage(self):
        for button, damage, ticks in ((1, 165, 20), (2, 50, 20), (8, 30, 100), (0x800, 30, 30)):
            with self.subTest(button=button):
                run, hog, enemy, targets = self.session(1.5)
                vitals = run.match.combat.vitals_of(targets[enemy])
                angles = self.torso_aim(run, hog, enemy) if button == 1 else (0, 0)
                run.step({hog: (*angles, button)})
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
                run.step({hog: (*self.torso_aim(run, hog, enemy), 1)})
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
        run.step({hog: (*self.torso_aim(run, hog, enemy), 1)})
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(600 - vitals.total, 82.5, places=3)
        self.assertAlmostEqual(self.charge(hog), 61.875, places=3)

    def test_breather_reduction_precedes_shields_armor_and_health_for_attacker_charge(self):
        for button, shield, armor, health_loss in ((1, 6, 16, 43.7), (2, 6, 12, 2)):
            with self.subTest(button=button):
                run, hog, enemy, _ = self.session()
                match = run.match
                fight = match.combat
                match.switch_hero(enemy, heroes()[ROADHOG])
                x, y, z = hog.mover.position
                enemy.mover.position[:] = (x, y, z + 2)
                enemy.mover.body = None
                enemy.body_script.ult_seconds = 0
                vitals = combat.Vitals(enemy.body, 600, armor, shield)
                fight.vitals[enemy.body] = vitals
                fight._sync_body_health(enemy, vitals)

                run.step({enemy: (-32768, 0, 16)})
                self.assertEqual(fight.incoming_damage_scale(enemy), 0.5)
                run.step({hog: (*self.torso_aim(run, hog, enemy), button)})
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(vitals.now[SHIELDS_POOL], 0, places=3)
                self.assertAlmostEqual(vitals.now[ARMOUR_POOL], 0, places=3)
                self.assertAlmostEqual(vitals.now[HEALTH_POOL], 600 - health_loss, places=3)
                lost = shield + armor + health_loss
                self.assertAlmostEqual(self.charge(hog), lost * .75, places=3)
                self.assertAlmostEqual(vitals.ult_healing_deficit, lost, places=3)

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

    def test_breather_gap_spends_two_actual_damage_events_in_frame_order(self):
        run, hog, enemy, targets = self.session()
        fight = run.match.combat
        pool = fight.vitals_of(targets[hog])
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        aim_at_hog = self.torso_aim(run, enemy, hog)
        run.step({enemy: (*aim_at_hog, 1)})
        for _ in range(30):
            run.step({})
        first = 200 - pool.total
        self.assertGreater(first, 0)
        self.assertAlmostEqual(pool.ult_healing_deficit, first)
        run.step({hog: (0, 0, 16)})
        command_frame = hog.body_script.frame
        before = pool.total
        with patch.object(hog, "take_commands"):
            for _ in range(54):
                run.step({})
            run.step({enemy: (*aim_at_hog, 1)})
            for _ in range(25):
                run.step({})
        self.assertEqual(hog.body_script.frame, command_frame)
        self.assertEqual(self.charge(hog), 0)
        second = before - pool.total
        self.assertGreater(second, 0)
        self.assertAlmostEqual(second, first / 2)
        self.assertAlmostEqual(pool.ult_healing_deficit, first + second)
        impact, _ = pool.healing_damage[-1]

        heal = fight.heal_self
        samples = []

        def observe(entity, amount, *, tick=None):
            heal(entity, amount, tick=tick)
            samples.append((fight.tick if tick is None else tick, self.charge(hog)))

        with patch.object(fight, "heal_self", side_effect=observe):
            run.step({})
            for _ in range(100):
                run.step({})
        early = [charge for tick, charge in samples if tick < impact]
        self.assertTrue(early)
        self.assertAlmostEqual(max(early), first, places=4)
        self.assertAlmostEqual(self.charge(hog), first + second, places=4)
        self.assertAlmostEqual(pool.ult_healing_deficit, 0)
        self.assertFalse(pool.healing_damage)
        self.assertAlmostEqual(pool.total, 500 - first - second)

    def test_breather_gap_cannot_heal_damage_taken_after_the_channel(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                run, hog, enemy, targets = self.session()
                fight = run.match.combat
                pool = fight.vitals_of(targets[hog])
                aim_at_hog = self.torso_aim(run, enemy, hog)
                run.step({hog: (0, 0, 16)})
                command_frame = hog.body_script.frame
                omit = patch.object(hog, "take_commands") if gap else nullcontext()
                with omit:
                    for _ in range(145):
                        run.step({})
                    run.step({enemy: (*aim_at_hog, 1)})
                    for _ in range(15):
                        run.step({})
                if gap:
                    self.assertEqual(hog.body_script.frame, command_frame)
                before_recovery = pool.total
                run.step({})
                with self.subTest(phase="live hit before recovery"):
                    self.assertEqual(before_recovery, 580)
                with self.subTest(phase="recovered body"):
                    self.assertEqual((pool.total, self.charge(hog), pool.ult_healing_deficit),
                                     (before_recovery, 0, 600 - before_recovery))

    def test_breather_gap_mitigation_matches_graph_at_each_expiry_boundary(self):
        run, hog, _, _ = self.session()
        run.step({hog: (0, 0, 16)})
        start = run.frame
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        wait = instance.state(1)
        self.assertTrue(wait.active)
        for _ in range(180):
            run.step({})
            if not wait.active:
                break
        self.assertFalse(wait.active)
        duration = run.frame - start
        for offset, expected in ((-1, 10), (0, 10), (1, 20)):
            for gap in (False, True):
                with self.subTest(offset=offset, gap=gap):
                    self.assertEqual(self.breather_boundary_damage(duration + offset, gap), expected)

    def breather_boundary_damage(self, offset, gap):
        run, hog, enemy, _ = self.session()
        # The enemy shoots before Roadhog's command for this world tick.
        run.players.reverse()
        look = self.torso_aim(run, enemy, hog)
        run.step({hog: (0, 0, 16)})
        command_frame = hog.body_script.frame
        omit = patch.object(hog, "take_commands") if gap else nullcontext()
        with omit:
            for _ in range(offset - 1):
                run.step({})
            run.step({enemy: (*look, 1)})
        if gap:
            self.assertEqual(hog.body_script.frame, command_frame)
        return enemy.stats.value(stats.HERO_DAMAGE)

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

    def test_actual_damage_healing_and_passive_each_unlock_whole_hog_at_full_cost(self):
        for source, earned in (("damage", 37.5), ("buffered damage", 37.5), ("healing", 50), ("passive", 5)):
            with self.subTest(source=source):
                self.check_earned_whole_hog_readiness(source, earned)

    def check_earned_whole_hog_readiness(self, source, earned):
        from ow174.game.script import nodes

        run, hog, enemy, _ = self.session()
        match = run.match
        fight = match.combat
        position = tuple(enemy.mover.position)
        self.assertTrue(match.switch_hero(enemy, heroes()[ROADHOG]))
        enemy.mover.position[:] = position
        enemy.mover.body = None
        enemy.body_script.ult_seconds = 0
        fight.vitals[enemy.body] = combat.Vitals(enemy.body, 20000)
        target = next(target for target in fight._targets() if target.owner is hog)
        own = fight.vitals_of(target)
        for _ in range(30):
            run.step({})

        script = hog.body_script
        ultimate, = script.ultimates
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2240)
        script.set_ult_charge(cost - earned)
        facing = self.torso_aim(run, hog, enemy)
        run.step({hog: (*facing, 32)})
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(hog), cost - earned)
        run.step({hog: (*facing, 0)})

        if source in ("damage", "buffered damage"):
            if source == "damage":
                for _ in range(15):
                    run.step({hog: (*facing, 0)})
                self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                self.assertEqual(self.charge(hog), cost - earned)
            run.step({hog: (*facing, 2)})
        elif source == "healing":
            run.step({enemy: (*self.torso_aim(run, enemy, hog), 2)})
            for _ in range(20):
                run.step({})
                if own.total < 600:
                    break
            self.assertEqual(own.total, 550)
            self.assertEqual(own.ult_healing_deficit, 50)
            self.assertEqual(self.charge(hog), cost - earned)
            run.step({hog: (*facing, 16)})
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
        else:
            script.ult_seconds = None

        buffered = source == "buffered damage"
        for _ in range(150):
            if ultimate.flags & nodes.Ability.RUNNING or self.charge(hog) == cost:
                break
            self.assertLess(self.charge(hog), cost)
            run.step({hog: (*facing, 0)})
        self.assertEqual(bool(ultimate.flags & nodes.Ability.RUNNING), buffered)
        self.assertEqual(self.charge(hog), 0 if buffered else cost)
        enemy_pool = fight.vitals[enemy.body]
        if source in ("damage", "buffered damage"):
            self.assertEqual(enemy_pool.total, 19950)
        elif source == "healing":
            self.assertEqual(own.total, 600)
            self.assertEqual(own.ult_healing_deficit, 0)
            self.assertAlmostEqual(hog.stats.value(stats.HEALING), earned, places=5)
            for _ in range(150):
                if fight.incoming_damage_scale(hog) == 1:
                    break
                run.step({hog: (*facing, 0)})
            self.assertEqual(fight.incoming_damage_scale(hog), 1)
        else:
            self.assertLess(script.passive_charge_ms, script.frame_ms)

        before = enemy_pool.total
        if not buffered:
            run.step({hog: (*facing, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(hog), 0)
        for _ in range(65):
            run.step({hog: (*facing, 0)})
            if enemy.body in fight.knockbacks.active:
                break
        self.assertIn(enemy.body, fight.knockbacks.active)
        self.assertLess(enemy_pool.total, before)
        self.assertEqual(self.charge(hog), 0)
        for _ in range(475):
            if not ultimate.flags & nodes.Ability.RUNNING:
                break
            run.step({hog: (16384, 0, 0)})
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(hog), 0)

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
        # Delivered Whole Hog force moved this target out of the old fixed ray.
        # Reestablish the close resource fixture before testing damage charge.
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x, y, z+2)
        enemy.mover.body = None
        run.step({})
        resumed_health = vitals.total
        run.step({hog: (0, 0, 2)})
        for _ in range(20):
            run.step({})
        self.assertGreater(self.charge(hog), 0)
        self.assertLess(vitals.total, resumed_health)
        # Death guards the passive and late combat callbacks on this body.
        fight._damage(enemy, targets[hog], 2000, False, None, run.frame)
        ended = self.charge(hog)
        hog.body_script.ult_seconds = None
        for _ in range(80):
            run.step({})
        self.assertEqual(self.charge(hog), ended)


if __name__ == "__main__":
    unittest.main()
