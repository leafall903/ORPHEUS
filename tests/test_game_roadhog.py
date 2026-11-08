"""Roadhog lane regressions; these do not establish complete hero/client acceptance."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040


class RoadhogProjectileTests(unittest.TestCase):
    def test_real_primary_volley_hits_with_25_pellets_and_consumes_one_round(self):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 2]
        fight = match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step({hog: (0, 0, 1)})
        self.assertEqual(len(fight.rockets), 25)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        for _ in range(15):
            run.step({})
        self.assertAlmostEqual(200 - vitals.total, 25 * 6.6, places=4)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 25)

    def test_primary_miss_team_filter_and_real_wall_do_not_damage(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Roadhog", "Target"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                hog, enemy = run.players
                match.switch_hero(hog, heroes()[ROADHOG])
                if mode == "ally":
                    enemy.team = hog.team
                x, y, z = hog.mover.position
                enemy.mover.position = [x, y, z + 2]
                fight = match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
                wall = World([
                    (x - 5, y, z + 1, x + 5, y, z + 1, x + 5, y + 5, z + 1),
                    (x - 5, y, z + 1, x + 5, y + 5, z + 1, x - 5, y + 5, z + 1),
                ])
                with patch.object(fight, "_collision", return_value=wall if mode == "wall" else None):
                    run.step({hog: (-32768 if mode == "miss" else 0, 0, 1)})
                    for _ in range(20):
                        run.step({})
                self.assertEqual(vitals.total, 200.0)
                self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_real_melee_does_not_damage_through_triangle_wall(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                hog, enemy = run.players
                match.switch_hero(hog, heroes()[ROADHOG])
                x, y, z = hog.mover.position
                enemy.mover.position = [x, y, z + 1.5]
                fight = match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
                wall = World([
                    (x - 5, y, z + 0.5, x + 5, y, z + 0.5, x + 5, y + 5, z + 0.5),
                    (x - 5, y, z + 0.5, x + 5, y + 5, z + 0.5, x - 5, y + 5, z + 0.5),
                ])
                with patch.object(fight, "_collision", return_value=wall if blocked else None):
                    run.step({hog: (0, 0, 0x800)})
                    for _ in range(25):
                        run.step({})
                self.assertEqual(vitals.total, 200.0 if blocked else 170.0)

    def pellet_hit(self, distance, *, critical=1.0, head=False, gap=False):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, enemy = run.players
        match.switch_hero(shooter, heroes()[ROADHOG])
        x, y, z = shooter.mover.position
        enemy.mover.position = [x, y, z + distance]
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x01C6)
        state = next(s for s in weapon.states.values() if s.node.pos == 26)
        direct = combat.hit_def(state, state.node.field("m_6396149F"))
        direct.critical = critical
        origin = (x, y + (combat.EYE if head else 1.0), z)
        rocket = combat.Rocket(shooter, shooter.team, origin, (0.0, 0.0, 1.0), 80.0, 1.25,
                               direct, None, last_tick=run.frame)
        fight.rockets.append(rocket)
        frames = (100,) if gap else range(1, 101)
        for advance in frames:
            frame = run.frame + advance
            match.update(frame * combat.TICK, frame)
        self.assertNotIn(rocket, fight.rockets)
        return 200.0 - vitals.total, direct, rocket, shooter.client.messages(combat.HIT)

    def test_authored_primary_pellet_falloff_reaches_actual_player_pools(self):
        for distance in (8.0, 22.0, 35.0):
            with self.subTest(distance=distance):
                damage, definition, rocket, _ = self.pellet_hit(distance)
                self.assertAlmostEqual(definition.amount, 6.6, places=5)
                expected = definition.amount * definition.scale(rocket.speed * rocket.flown)
                self.assertAlmostEqual(damage, expected)
                if distance > 15:
                    self.assertLess(damage, definition.amount)

    def test_skipped_ticks_preserve_total_distance_falloff(self):
        continuous, _, _, _ = self.pellet_hit(22.0)
        skipped, _, _, _ = self.pellet_hit(22.0, gap=True)
        self.assertAlmostEqual(continuous, skipped)

    def test_projectile_critical_multiplier_and_hit_notice_agree(self):
        # Explicit shared-mechanism fixture: Roadhog node26 has no authored critical
        # multiplier in this extracted input, so this does not invent a hero default.
        body_damage, _, _, body_hits = self.pellet_hit(22.0, critical=2.0)
        head_damage, definition, rocket, head_hits = self.pellet_hit(22.0, critical=2.0, head=True)
        self.assertGreater(head_damage, body_damage)
        self.assertAlmostEqual(
            head_damage, 2.0 * definition.amount * definition.scale(rocket.speed * rocket.flown),
        )
        self.assertEqual(body_hits[-1]["+0x78"]["+0x8"] & combat.CRITICAL, 0)
        self.assertEqual(head_hits[-1]["+0x78"]["+0x8"] & combat.CRITICAL, combat.CRITICAL)


class RoadhogSelfHealTests(unittest.TestCase):
    def session(self, missing=400.0):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        hog, enemy = run.players
        run.match.switch_hero(hog, heroes()[ROADHOG])
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hog)
        vitals = fight.vitals_of(target)
        vitals.damage(missing)
        fight._sync_body_health(hog, vitals)
        return run, hog, enemy, target, vitals

    def advance(self, run, hog, count):
        for _ in range(count):
            run.step({hog: (0, 0, 0)})

    def test_real_breather_input_heals_only_self_and_respects_windup(self):
        run, hog, enemy, _, vitals = self.session()
        fight = run.match.combat
        other = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        other.damage(100)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 25)
        self.assertEqual(vitals.total, 200.0)
        self.advance(run, hog, 160)
        self.assertAlmostEqual(vitals.total, 500.0)
        self.assertEqual(other.total, 100.0)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 300.0)

    def test_overheal_is_capped_and_not_counted_as_healing(self):
        run, hog, _, _, vitals = self.session(missing=50)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 180)
        self.assertEqual(vitals.total, 600.0)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 50.0)

    def test_ending_heal_state_stops_future_healing(self):
        run, hog, _, _, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 60)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        state = next(s for s in instance.states.values() if s.node.pos == 18)
        self.assertTrue(state.active)
        self.assertGreater(vitals.total, 200)
        state.end(False)
        stopped = vitals.total
        self.advance(run, hog, 150)
        self.assertEqual(vitals.total, stopped)

    def test_death_during_heal_does_not_resurrect(self):
        run, hog, enemy, target, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 60)
        run.match.combat._damage(enemy, target, 2000, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.advance(run, hog, 150)
        self.assertEqual(vitals.total, 0)

    def test_breather_cooldown_rejects_repress_and_expires_before_reuse(self):
        run, hog, _, _, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 180)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        heal = next(s for s in instance.states.values() if s.node.pos == 18)
        ability = next(s for s in instance.states.values() if s.node.pos == 1)
        self.assertEqual(heal.counter, 1)
        self.assertEqual(instance.find_var(0, 61).base, 8.0)
        self.assertGreater(ability.remaining(), 0.0)
        for _ in range(5):
            run.step({hog: (0, 0, 16)})
            run.step({hog: (0, 0, 0)})
        self.assertEqual(heal.counter, 1)
        self.assertAlmostEqual(vitals.total, 500.0)
        self.advance(run, hog, 450)
        self.assertEqual(ability.remaining(), 0.0)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 180)
        self.assertEqual(heal.counter, 2)
        self.assertEqual(vitals.total, 600.0)


class WholeHogTests(unittest.TestCase):
    def session(self, *, charged=True):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 2]
        fight = match.combat
        targets = {t.owner: t for t in fight._targets()}
        # High-health fixture retains one target for the entire ultimate, without
        # claiming that a retail Soldier has this amount of health.
        vitals = combat.Vitals(enemy.body, 20000)
        fight.vitals[enemy.body] = vitals
        if charged:
            cost = hog.body_script.component.entity_var(ULT_COST).value()
            hog.body_script.component.set(ULT_CHARGE, cost)
        angles = aim(fight._eye(hog), (x, y + 0.9, z + 2))
        return run, hog, enemy, targets[hog], vitals, angles

    def advance(self, run, hog, angles, count, *, press=False):
        for frame in range(count):
            run.step({hog: (*angles, 32 if press and frame == 0 else 0)})

    def test_charge_consumption_multiple_pellets_end_and_reject_uncharged_recast(self):
        run, hog, _, _, vitals, angles = self.session()
        ultimate, = hog.body_script.ultimates
        self.advance(run, hog, angles, 1, press=True)
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.advance(run, hog, angles, 449)
        self.assertFalse(ultimate.flags & 1)
        hits = hog.client.messages(combat.HIT)
        self.assertGreater(len(hits), 100)  # The old single-pellet path produced only44.
        self.assertAlmostEqual(20000 - vitals.total, 7 * len(hits))
        ended = vitals.total
        self.advance(run, hog, angles, 100, press=True)
        self.assertEqual(vitals.total, ended)
        self.assertEqual(len(hog.client.messages(combat.HIT)), len(hits))

    def test_uncharged_ultimate_cannot_start_or_damage(self):
        run, hog, _, _, vitals, angles = self.session(charged=False)
        self.advance(run, hog, angles, 120, press=True)
        ultimate, = hog.body_script.ultimates
        self.assertFalse(ultimate.flags & 1)
        self.assertEqual(vitals.total, 20000)
        self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_owner_death_ends_ultimate_damage(self):
        run, hog, enemy, target, vitals, angles = self.session()
        self.advance(run, hog, angles, 80, press=True)
        self.assertLess(vitals.total, 20000)
        run.match.combat._damage(enemy, target, 2000, False, None, run.frame)
        self.assertTrue(run.match.combat.vitals_of(target).dead)
        ended = vitals.total
        self.advance(run, hog, angles, 100)
        self.assertEqual(vitals.total, ended)

    def test_charged_ultimate_miss_allies_and_triangle_wall(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run, hog, enemy, _, vitals, angles = self.session()
                fight = run.match.combat
                if mode == "miss":
                    angles = (-32768, 0)
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = hog.team
                x, y, z = hog.mover.position
                wall = World([
                    (x - 5, y, z + 1, x + 5, y, z + 1, x + 5, y + 5, z + 1),
                    (x - 5, y, z + 1, x + 5, y + 5, z + 1, x - 5, y + 5, z + 1),
                ])
                with patch.object(fight, "_collision", return_value=wall if mode == "wall" else None):
                    self.advance(run, hog, angles, 100, press=True)
                self.assertEqual(vitals.total, 20000)
                self.assertEqual(hog.client.messages(combat.HIT), [])
                self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)


if __name__ == "__main__":
    unittest.main()
