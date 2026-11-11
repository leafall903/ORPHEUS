"""Real Ana hits into Roadhog channels and scoped receiver effects."""

import math
import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import snapshot_digest


class RoadhogAnaTests(unittest.TestCase):
    def session(self):
        run, hog, ana, rear = phases.RoadhogDefenseTests().session("Ana", distance=6)
        rear.mover.position[0] = 20
        for _ in range(40):
            run.step({})
        return run, hog, ana

    def pool(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def step(self, run, hog, ana, *, fire=0, cast=0, miss=False, hog_aim=None):
        fight = run.match.combat
        x, y, z = hog.mover.position
        yaw, pitch = aim(fight._eye(ana), (x, y + 1, z))
        if miss:
            yaw = yaw % 65536 - 32768
        x, y, z = ana.mover.position
        facing = aim(fight._eye(hog), (x, y + 0.8, z)) if hog_aim is None else hog_aim
        run.step({hog: (*facing, fire), ana: (yaw, pitch, cast)})

    def land(self, run, hog, ana, button, *, hog_aim=None):
        fight = run.match.combat
        table = fight.sleep if button == 8 else fight.antiheal
        self.step(run, hog, ana, cast=button, hog_aim=hog_aim)
        for _ in range(70):
            if hog.body in table:
                return table[hog.body]
            self.step(run, hog, ana, hog_aim=hog_aim)
        self.fail("Ana effect did not reach Roadhog")

    def test_sleep_interrupts_active_whole_hog_and_expires_without_refund(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        hog.body_script.set_ult_charge(2240)
        ultimate, = hog.body_script.ultimates
        self.step(run, hog, ana, fire=32)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        deadline = self.land(run, hog, ana, 8)
        self.assertEqual(deadline - run.frame, math.ceil(5 / combat.TICK))
        self.assertEqual(self.pool(run, hog).total, 595)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        enemy_health = self.pool(run, ana).total
        while run.frame < deadline - 1:
            self.step(run, hog, ana, fire=32 | 1 | 2 | 8 | 16)
            self.assertTrue(fight.is_stunned(hog))
            self.assertFalse(hog.body_script.component.held)
        self.step(run, hog, ana)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.sleep)
        self.assertEqual(self.pool(run, ana).total, enemy_health)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.step(run, hog, ana, fire=2)
        for _ in range(25):
            self.step(run, hog, ana)
        self.assertLess(self.pool(run, ana).total, enemy_health)

    def test_sleep_dart_cancels_hook_in_flight_or_during_pull_and_keeps_cooldown(self):
        for dart_delay, caught in ((0, False), (10, True)):
            with self.subTest(caught=caught):
                self.check_sleep_dart_hook(dart_delay, caught)

    def check_sleep_dart_hook(self, dart_delay, caught):
        run, hog, ana = self.session()
        victim = run.players[2]
        fight = run.match.combat
        y = hog.mover.position[1]
        ana.mover.position[:] = (3, y, 0)
        victim.mover.position[:] = (0, y, 17)
        ana.mover.body = victim.mover.body = None
        for _ in range(10):
            run.step({})
        sight = aim(fight._eye(ana), (0, y + 1, 0))
        pool = self.pool(run, victim)
        before = pool.total
        arrived = []
        effect = fight._hit_effect

        def record(target, effect_id, **kwargs):
            if target.entity == hog.body and effect_id == combat.ANA_SLEEP_EFFECT:
                flight = fight.hooks.flights.get(hog.body)
                arrived.append(None if flight is None else (flight.target is not None, flight.arrived_at))
            return effect(target, effect_id, **kwargs)

        with patch.object(fight, "_hit_effect", side_effect=record):
            for frame in range(65):
                run.step({hog: (0, 0, 8 if frame == 0 else 0),
                          ana: (*sight, 8 if frame == dart_delay else 0)})
                if hog.body in fight.sleep:
                    break
        self.assertEqual(arrived, [(caught, None)])
        self.assertIn(hog.body, fight.sleep)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(victim.body))
        expected = before - (30 if caught else 0)
        self.assertEqual(pool.total, expected)

        hook = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C8)
        cooldown = next(state for state in hook.states.values() if state.node.pos == 11)
        sleep_frames = fight.sleep[hog.body] - run.frame
        self.assertEqual(sleep_frames, math.ceil(5 / combat.TICK))
        position = tuple(victim.mover.position)
        for _ in range(sleep_frames):
            run.step({hog: (0, 0, 8)})
        self.assertNotIn(hog.body, fight.sleep)
        self.assertEqual(tuple(victim.mover.position), position)
        run.step({})
        self.assertGreater(cooldown.remaining(), 0)
        run.step({hog: (0, 0, 8)})
        for _ in range(40):
            run.step({})
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(pool.total, expected)
        for _ in range(510):
            if cooldown.remaining() == 0:
                break
            run.step({})
        self.assertEqual(cooldown.remaining(), 0)
        run.step({hog: (0, 0, 8)})
        for _ in range(50):
            run.step({})
        self.assertEqual(pool.total, expected - 30)

    def test_hook_damage_wakes_sleeping_enemy_on_recovery_clock_and_still_pulls(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.check_hook_wakes_sleeping_enemy(late)

    def check_hook_wakes_sleeping_enemy(self, late):
        run, hog, ana = self.session()
        victim = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = ana.team = 0
        victim.team = 1
        y = hog.mover.position[1]
        ana.mover.position[:] = (0, y, 14)
        victim.mover.position[:] = (0, y, 10)
        ana.mover.body = victim.mover.body = None
        for _ in range(10):
            run.step({})
        sight = aim(fight._eye(ana), (0, y + 1.45, 10))
        run.step({ana: (*sight, 8)})
        for _ in range(60):
            if victim.body in fight.sleep:
                break
            run.step({ana: (*sight, 0)})
        self.assertIn(victim.body, fight.sleep)
        full = fight.sleep[victim.body]
        floor = fight.sleep_wake_floor[victim.body]
        pool = self.pool(run, victim)
        before = pool.total
        if late:
            for _ in range(floor - run.frame):
                run.step({})
        run.step({hog: (0, 0, 8)})
        for _ in range(60):
            if fight.hooks.controlled(victim.body):
                break
            run.step({})
        self.assertTrue(fight.hooks.controlled(victim.body))
        self.assertEqual(pool.total, before - 30)
        wake = max(floor, run.frame + math.ceil(combat.ANA_SLEEP_WAKE_RECOVERY / combat.TICK))
        self.assertEqual(fight.sleep[victim.body], wake)
        self.assertLess(wake, full)
        self.assertEqual(wake == floor, not late)

        weapon = victim.body_script.component.instances[11]
        ammo = weapon.vars[53].value()
        start = victim.mover.position[2]
        for _ in range(wake - run.frame - 1):
            run.step({victim: (-32768, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertLess(victim.mover.position[2], start)
        for _ in range(100):
            run.step({})
            if not fight.is_stunned(victim) and not fight.hooks.controlled(victim.body):
                break
        self.assertNotIn(victim.body, fight.sleep)
        self.assertFalse(fight.hooks.controlled(victim.body))
        self.assertEqual(weapon.vars[53].value(), ammo)
        run.step({victim: (-32768, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), ammo - 1)

    def test_friendly_grenade_heals_and_boosts_real_breather_until_expiry(self):
        run, hog, ana = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ana.team = hog.team
        fight = run.match.combat
        pool = self.pool(run, hog)
        pool.damage(540)
        fight._sync_body_health(hog, pool)
        deadline = self.land_friendly_grenade(run, hog, ana)
        self.assertEqual(pool.total, 120)
        self.assertNotIn(hog.body, fight.antiheal)
        self.assertEqual(deadline-run.frame, 250)
        self.step(run, hog, ana, fire=16)
        while run.frame < deadline:
            self.step(run, hog, ana)
        self.assertAlmostEqual(pool.total, 570, places=3)
        self.assertNotIn(hog.body, fight.heal_boost)

    def land_friendly_grenade(self, run, hog, ana):
        fight = run.match.combat
        self.step(run, hog, ana, cast=16)
        for _ in range(70):
            if hog.body in fight.heal_boost:
                return fight.heal_boost[hog.body]
            self.step(run, hog, ana)
        self.fail("Actual friendly Ana grenade did not reach Roadhog")

    def test_grenade_deadlines_change_healing_during_actual_breather(self):
        for overlap in (False, True):
            with self.subTest(overlap=overlap):
                run, hog, ana = self.session()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = ana.team = 0
                pool = self.pool(run, hog)
                pool.damage(500)
                fight._sync_body_health(hog, pool)
                if overlap:
                    enemy = run.players[2]
                    run.match.switch_hero(enemy, ana.hero)
                    enemy.team = 1
                    x, y, z = hog.mover.position
                    enemy.mover.position[:] = (x+6, y, z)
                    enemy.mover.body = None
                    for _ in range(40):
                        self.step(run, hog, ana)
                    deadline = self.land(run, hog, enemy, 16)
                    self.assertEqual(pool.total, 50)
                    for _ in range(80):
                        self.step(run, hog, ana)
                    boosted_until = self.land_friendly_grenade(run, hog, ana)
                    self.assertEqual(pool.total, 50)
                    self.assertEqual(fight.antiheal[hog.body], deadline)
                    self.assertGreater(boosted_until, deadline)
                else:
                    deadline = boosted_until = self.land_friendly_grenade(run, hog, ana)
                    self.assertEqual(pool.total, 160)
                while run.frame < deadline - 60:
                    self.step(run, hog, ana)
                observe = fight.heal_self
                samples = []

                def heal(entity, amount):
                    before = pool.total
                    observe(entity, amount)
                    samples.append((fight.tick, amount, pool.total-before))

                with patch.object(fight, "heal_self", side_effect=heal):
                    self.step(run, hog, ana, fire=16)
                    for _ in range(145):
                        self.step(run, hog, ana)
                self.assertTrue(any(tick < deadline for tick, _, _ in samples))
                self.assertTrue(any(tick >= deadline for tick, _, _ in samples))
                for tick, amount, given in samples:
                    scale = 0 if overlap and tick < deadline else (1.5 if tick < boosted_until else 1)
                    self.assertAlmostEqual(given, amount * scale, places=4)
                while run.frame < boosted_until:
                    self.step(run, hog, ana)
                self.assertNotIn(hog.body, fight.antiheal)
                self.assertNotIn(hog.body, fight.heal_boost)

    def test_breather_gap_matches_continuous_healing_across_grenade_expiry(self):
        for friendly in (False, True):
            results = []
            for gap in (0, 40, 80):
                with self.subTest(friendly=friendly, gap=gap):
                    run, hog, ana = self.session()
                    fight = run.match.combat
                    pool = self.pool(run, hog)
                    pool.damage(500)
                    fight._sync_body_health(hog, pool)
                    if friendly:
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        ana.team = hog.team
                        deadline = self.land_friendly_grenade(run, hog, ana)
                    else:
                        deadline = self.land(run, hog, ana, 16)
                    while run.frame < deadline - 60:
                        self.step(run, hog, ana)
                    self.step(run, hog, ana, fire=16)
                    for _ in range(42):
                        self.step(run, hog, ana)
                    command_frame = hog.body_script.frame
                    with patch.object(hog, "take_commands"):
                        for _ in range(gap):
                            run.step({})
                    self.assertEqual(hog.body_script.frame, command_frame)
                    while run.frame < deadline + 100:
                        self.step(run, hog, ana)
                    if results:
                        self.assertAlmostEqual(pool.total, results[0], places=4)
                    results.append(pool.total)

    def test_breather_real_command_gaps_match_continuous_across_grenade_onset(self):
        for friendly in (False, True):
            results = []
            for gap in (0, 40, 80):
                with self.subTest(friendly=friendly, gap=gap):
                    results.append(self.breather_grenade_onset(friendly, gap))
                    for observed, expected in zip(results[-1], results[0], strict=True):
                        self.assertAlmostEqual(observed, expected, places=4)

    def breather_grenade_onset(self, friendly, gap):
        run, hog, ana = self.session()
        fight = run.match.combat
        if friendly:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            ana.team = hog.team
        pool = self.pool(run, hog)
        pool.damage(520)
        fight._sync_body_health(hog, pool)
        self.step(run, hog, ana, fire=16)
        for _ in range(42):
            self.step(run, hog, ana)
        before = pool.total
        self.assertGreater(before, 80)
        command_frame = hog.body_script.frame
        gap_start = run.frame
        omit_commands = patch.object(hog, "take_commands") if gap else nullcontext()
        with omit_commands:
            deadline = self.land_friendly_grenade(run, hog, ana) if friendly else self.land(run, hog, ana, 16)
            impact = run.frame
            if gap:
                self.assertLess(impact, gap_start + gap)
            while run.frame < gap_start + gap:
                run.step({})
        if gap:
            self.assertEqual(hog.body_script.frame, command_frame)
            self.assertAlmostEqual(pool.total, before + (60 if friendly else -25), places=4)
        while run.frame < impact + 160:
            self.step(run, hog, ana)
        self.assertLess(run.frame, deadline)
        self.assertGreater(pool.total, before - 25)
        self.assertLess(pool.total, 600)
        charge = hog.body_script.component.entity_var(ULT_CHARGE).value()
        return pool.total, hog.stats.value(stats.HEALING), charge, pool.ult_healing_deficit

    def test_friendly_grenade_wall_and_body_replacement(self):
        for wall_blocks in (True, False):
            with self.subTest(wall=wall_blocks):
                run, hog, ana = self.session()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = ana.team = 0
                pool = self.pool(run, hog)
                pool.damage(200)
                fight._sync_body_health(hog, pool)
                if wall_blocks:
                    x, y, z = hog.mover.position
                    wall = World([
                        (x-6, y-3, z+1, x+6, y-3, z+1, x+6, y+7, z+1),
                        (x-6, y-3, z+1, x+6, y+7, z+1, x-6, y+7, z+1),
                    ])
                    with patch.object(fight, "_collision", return_value=wall):
                        self.step(run, hog, ana, cast=16)
                        for _ in range(70):
                            self.step(run, hog, ana)
                    self.assertEqual(pool.total, 400)
                    self.assertNotIn(hog.body, fight.heal_boost)
                else:
                    self.land_friendly_grenade(run, hog, ana)
                    old = hog.body
                    self.assertTrue(run.match.switch_hero(hog, hog.hero))
                    self.assertNotEqual(hog.body, old)
                    self.assertNotIn(old, fight.heal_boost)
                    self.assertNotIn(hog.body, fight.heal_boost)

    def test_friendly_grenade_does_not_duplicate_roadhog_healing_charge(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        enemy = run.players[2]
        run.match.switch_hero(enemy, hog.hero)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = ana.team = 0
        enemy.team = 1
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x+2, y, z)
        enemy.mover.body = None
        for _ in range(40):
            self.step(run, hog, ana)
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)
        pool = self.pool(run, hog)
        angles = aim(fight._eye(enemy), (x, y+0.7, z))
        run.step({enemy: (*angles, 1)})
        for _ in range(20):
            self.step(run, hog, ana)
        loss = 600-pool.total
        self.assertGreater(loss, 60)
        self.assertAlmostEqual(pool.ult_healing_deficit, loss, places=4)
        enemy.mover.position[0] = x+20
        enemy.mover.body = None
        self.land_friendly_grenade(run, hog, ana)
        self.assertAlmostEqual(pool.total, 660-loss, places=4)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.step(run, hog, ana, fire=16)
        for _ in range(180):
            self.step(run, hog, ana)
        self.assertEqual(pool.total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), loss-60, places=3)

    def test_same_tick_friendly_grenade_and_breather_share_only_one_healing_deficit(self):
        for capped in (False, True):
            with self.subTest(capped=capped):
                self.check_same_tick_grenade_breather(capped)

    def test_breather_catchup_does_not_recredit_friendly_grenade_healing(self):
        for gap in (40, 80):
            for capped in (False, True):
                with self.subTest(gap=gap, capped=capped):
                    self.check_same_tick_grenade_breather(capped, gap=gap)

    def check_same_tick_grenade_breather(self, capped, *, gap=0):
        run, hog, ana = self.session()
        match = run.match
        fight = match.combat
        enemy = run.players[2]
        self.assertTrue(match.switch_hero(enemy, hog.hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = ana.team = 0
        enemy.team = 1
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x + 2, y, z)
        enemy.mover.body = None
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)
        pool = self.pool(run, hog)
        look = aim(fight._eye(enemy), (x, y + .7, z))
        for _ in range(1 if capped else 2):
            run.step({enemy: (*look, 1)})
            for _ in range(64):
                run.step({})
        loss = 600 - pool.total
        self.assertGreater(loss, 100 if capped else 250)
        self.assertAlmostEqual(pool.ult_healing_deficit, loss, places=4)
        self.assertFalse(pool.dead)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        events = []
        replayed = []
        heal = fight._heal_target

        def observe(healer, target, amount, **kwargs):
            before = pool.total
            eligible = pool.ult_healing_deficit
            result = heal(healer, target, amount, **kwargs)
            if target.owner is hog:
                healing_tick = kwargs.get("tick")
                if healer is hog and healing_tick is not None and healing_tick < fight.tick and pool.total > before:
                    replayed.append(healing_tick)
                events.append((fight.tick, healer, pool.total - before,
                               eligible, pool.ult_healing_deficit))
            return result

        with patch.object(fight, "_heal_target", side_effect=observe):
            self.step(run, hog, ana, fire=16)
            for _ in range(65):
                if any(event[2] > 0 for event in events):
                    break
                self.step(run, hog, ana)
            self.assertGreater(pool.total, 600 - loss)
            if capped:
                for _ in range(110):
                    if pool.total >= 510:
                        break
                    self.step(run, hog, ana)
                self.assertGreaterEqual(pool.total, 510)
                self.assertLess(pool.total, 600)
            if gap:
                gap_start = run.frame
                command_frame = hog.body_script.frame
                before_gap = pool.total
                deficit_before_gap = pool.ult_healing_deficit
                with patch.object(hog, "take_commands"):
                    deadline = self.land_friendly_grenade(run, hog, ana)
                    self.assertLess(run.frame, gap_start + gap)
                    while run.frame < gap_start + gap:
                        run.step({})
                self.assertEqual(hog.body_script.frame, command_frame)
                self.assertAlmostEqual(pool.total, before_gap + 60, places=4)
                self.assertAlmostEqual(pool.ult_healing_deficit, deficit_before_gap - 60, places=4)
                self.assertGreater(deadline, run.frame)
            else:
                self.step(run, hog, ana, cast=16)
            for _ in range(155):
                self.step(run, hog, ana)
        grenade, = [event for event in events if event[1] is ana]
        tick, _, given, _, _ = grenade
        same_tick = [event for event in events if event[0] == tick]
        self.assertIs(same_tick[0][1], ana)
        breather_heals = same_tick[1:]
        self.assertTrue(all(event[1] is hog for event in breather_heals))
        if breather_heals:
            self.assertAlmostEqual(grenade[4], breather_heals[0][3], places=4)
        if gap:
            self.assertEqual(given, 60)
            self.assertEqual(breather_heals, [])
            self.assertTrue(replayed)
            self.assertTrue(all(gap_start < frame <= gap_start + gap for frame in replayed))
        elif capped:
            self.assertTrue(any(frame == tick - 1 and healer is hog and actual > 0
                                for frame, healer, actual, _, _ in events))
            self.assertGreater(given, 0)
            self.assertLess(given, 60)
            self.assertEqual(sum(event[2] for event in breather_heals), 0)
        else:
            self.assertEqual(given, 60)
            self.assertGreater(sum(event[2] for event in breather_heals), 0)
        for _, _, actual, eligible, after in events:
            self.assertAlmostEqual(eligible - after, actual, places=4)
        self.assertEqual(pool.total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), loss - given, places=3)
        self.assertAlmostEqual(ana.stats.value(stats.HEALING), given, places=4)

    def test_real_grenade_damage_wakes_sleeper_after_recovery_and_denies_healing(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        sleep_deadline = self.land(run, hog, ana, 8)
        for _ in range(50):
            self.step(run, hog, ana)
        grenade_deadline = self.land(run, hog, ana, 16)
        wake = fight.sleep[hog.body]
        self.assertLess(wake, sleep_deadline)
        self.assertEqual(wake, max(fight.sleep_wake_floor.get(hog.body, 0),
                                   run.frame + math.ceil(0.5 / combat.TICK)))
        self.assertEqual(grenade_deadline - run.frame, math.ceil(4 / combat.TICK))
        pool = self.pool(run, hog)
        self.assertEqual(pool.total, 545)
        while run.frame < wake:
            self.assertTrue(fight.is_stunned(hog))
            self.step(run, hog, ana)
        self.assertFalse(fight.is_stunned(hog))
        self.step(run, hog, ana, fire=16)
        for _ in range(130):
            self.step(run, hog, ana)
        self.assertEqual(pool.total, 545)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_grenade_expires_during_breather_and_heals_only_remaining_ticks(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        deadline = self.land(run, hog, ana, 16)
        while run.frame < deadline - 60:
            self.step(run, hog, ana)
        self.step(run, hog, ana, fire=16)
        while run.frame < deadline - 1:
            self.step(run, hog, ana)
        pool = self.pool(run, hog)
        self.assertEqual(pool.total, 550)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        for _ in range(35):
            self.step(run, hog, ana)
        self.assertNotIn(hog.body, fight.antiheal)
        self.assertEqual(pool.total, 600)
        self.assertAlmostEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 50, places=4)

    def test_sleep_and_grenade_miss_or_friendly_do_not_create_status(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, hog, ana = self.session()
                fight = run.match.combat
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    ana.team = hog.team
                for button in (8, 16):
                    self.step(run, hog, ana, cast=button, miss=not friendly)
                    for _ in range(90):
                        self.step(run, hog, ana, miss=not friendly)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertNotIn(hog.body, fight.sleep)
                self.assertNotIn(hog.body, fight.antiheal)

    def test_sleep_cancels_breather_while_preserving_cooldown(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        pool = self.pool(run, hog)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        self.step(run, hog, ana, fire=16)
        for _ in range(48):
            self.step(run, hog, ana)
        self.assertGreater(pool.total, 200)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.land(run, hog, ana, 8)
        ability = next(state for instance in hog.body_script.component.instances.values()
                       if instance.graph.index == 0x0240 for state in instance.states.values()
                       if isinstance(state, nodes.Ability))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        stopped = pool.total
        for _ in range(45):
            self.step(run, hog, ana, fire=16)
        self.assertEqual(pool.total, stopped)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_wall_blocks_dart_and_grenade_status(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        x, y, z = hog.mover.position
        wall = World([
            (x-5, y-1, z+3, x+5, y-1, z+3, x+5, y+5, z+3),
            (x-5, y-1, z+3, x+5, y+5, z+3, x-5, y+5, z+3),
        ])
        with patch.object(fight, "_collision", return_value=wall):
            for button in (8, 16):
                self.step(run, hog, ana, cast=button)
                for _ in range(90):
                    self.step(run, hog, ana)
        self.assertEqual(self.pool(run, hog).total, 600)
        self.assertNotIn(hog.body, fight.sleep)
        self.assertNotIn(hog.body, fight.antiheal)

    def test_replacement_clears_both_deadlines(self):
        run, hog, ana = self.session()
        fight = run.match.combat
        self.land(run, hog, ana, 8)
        for _ in range(50):
            self.step(run, hog, ana)
        self.land(run, hog, ana, 16)
        body = hog.body
        self.assertIn(body, fight.sleep)
        self.assertIn(body, fight.antiheal)
        run.match.switch_hero(hog, next(hero for hero in heroes().values() if hero.name == "Reaper"))
        self.assertNotIn(body, fight.sleep)
        self.assertNotIn(body, fight.antiheal)
        self.assertFalse(fight.is_stunned(hog))

    def projected_scene(self):
        run, hog, ana = self.session()
        caster = run.players[2]
        run.match.switch_hero(caster, next(hero for hero in heroes().values() if hero.name == "Zarya"))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        ana.team = 1
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x - 4, y, z)
        caster.mover.body = None
        for _ in range(35):
            run.step({})
        return run, hog, ana, caster

    def project(self, run, hog, caster, *, hog_aim=(0, 0)):
        fight = run.match.combat
        x, y, z = hog.mover.position
        angles = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (*angles, 0), hog: (*hog_aim, 0)})
        run.step({caster: (*angles, 16), hog: (*hog_aim, 0)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, caster)
        return bubble

    def test_projected_barrier_cleanses_antiheal_and_restores_actual_breather(self):
        run, hog, ana, caster = self.projected_scene()
        fight = run.match.combat
        deadline = self.land(run, hog, ana, 16)
        pool = self.pool(run, hog)
        self.assertEqual(pool.total, 550)
        self.step(run, hog, ana, fire=16)
        for _ in range(55):
            self.step(run, hog, ana)
        self.assertEqual(pool.total, 550)
        self.assertIn(hog.body, fight.antiheal)
        self.project(run, hog, caster)
        self.assertNotIn(hog.body, fight.antiheal)
        for _ in range(25):
            self.step(run, hog, ana)
        self.assertLess(run.frame, deadline)
        self.assertEqual(pool.total, 600)
        self.assertAlmostEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 50, places=4)

    def test_whole_hog_through_antiheal_cleanse_and_projected_dart_contact(self):
        for grenade_first in (False, True):
            with self.subTest(grenade_first=grenade_first):
                self.check_whole_hog_antiheal(grenade_first)

    def test_whole_hog_cancels_antihealed_breather_and_keeps_its_cooldown(self):
        run, hog, ana, caster = self.projected_scene()
        fight = run.match.combat
        pool = self.pool(run, hog)
        self.land(run, hog, ana, 16)
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(run, hog, ana, fire=16)
        for _ in range(33):
            self.step(run, hog, ana)
        breather = next(
            state for instance in script.component.instances.values() if instance.graph.index == 0x0240
            for state in instance.states.values() if isinstance(state, nodes.Ability)
        )
        self.assertTrue(breather.flags & nodes.Ability.RUNNING)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.assertEqual(pool.total, 550)
        self.step(run, hog, ana, fire=32)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        started = run.frame
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(breather.flags & nodes.Ability.RUNNING)
        self.assertGreater(breather.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertIn(hog.body, fight.antiheal)
        self.project(run, hog, caster)
        self.assertNotIn(hog.body, fight.antiheal)

        for _ in range(460):
            self.step(run, hog, ana)
            self.assertEqual(pool.total, 550)
            if not ultimate.flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(run.frame - started, 437)
        self.assertGreater(breather.remaining(), 0)
        for _ in range(520):
            self.step(run, hog, ana, fire=16)
            self.assertEqual(pool.total, 550)
            if breather.flags & nodes.Ability.RUNNING:
                self.assertEqual(breather.remaining(), 0)
                break
            self.assertGreater(breather.remaining(), 0)
        self.assertTrue(breather.flags & nodes.Ability.RUNNING)
        self.assertEqual(run.frame - started, 500)
        for _ in range(45):
            self.step(run, hog, ana)
        self.assertGreater(pool.total, 550)

    def check_whole_hog_antiheal(self, grenade_first):
        from test_game_mover import box

        run, hog, ana, caster = self.projected_scene()
        match = run.match
        fight = match.combat
        x, y, z = ana.mover.position
        match.collision_world = World(
            box(x - 35, x + 35, y - 1, y, z - 35, z + 35)
            + box(x - 12, x + 12, y, y + 8, z + .9, z + 1.2),
        )
        pool = self.pool(run, hog)
        enemy = self.pool(run, ana)
        pool.damage(250)
        fight._sync_body_health(hog, pool)
        script = hog.body_script
        ultimate, = script.ultimates
        away = (16384, 0)  # Keep Ana's firing position stable until the dart lands.
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        if grenade_first:
            self.land(run, hog, ana, 16, hog_aim=away)
        self.step(run, hog, ana, fire=32, hog_aim=away)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        if not grenade_first:
            self.land(run, hog, ana, 16, hog_aim=away)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertIn(hog.body, fight.antiheal)
        self.assertEqual(pool.total, 300)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

        self.step(run, hog, ana, fire=16, hog_aim=away)
        breather = next(
            state for instance in script.component.instances.values() if instance.graph.index == 0x0240
            for state in instance.states.values() if isinstance(state, nodes.Ability)
        )
        self.assertFalse(breather.flags & nodes.Ability.RUNNING)
        self.assertEqual(breather.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(pool.total, 300)

        for _ in range(45):
            self.step(run, hog, ana, hog_aim=away)
        self.assertIn(hog.body, fight.antiheal)
        bubble = self.project(run, hog, caster, hog_aim=away)
        self.assertNotIn(hog.body, fight.antiheal)
        if grenade_first:
            bubble.health = 5  # Exercise a dart that spends the final shield points.
        shield_before = bubble.health
        self.step(run, hog, ana, cast=8, hog_aim=away)
        for _ in range(40):
            self.step(run, hog, ana, hog_aim=away)
            self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
            self.assertNotIn(hog.body, fight.sleep)
            self.assertEqual(pool.total, 300)
            if bubble.health < shield_before:
                break
        self.assertEqual(bubble.health, shield_before - 5)
        self.assertEqual(fight.zarya_bubbles.for_target(hog) is None, grenade_first)
        enemy_before = enemy.total
        for _ in range(25):
            self.step(run, hog, ana)
            if enemy.total < enemy_before:
                break
        self.assertLess(enemy.total, enemy_before)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(400):
            self.step(run, hog, ana)
            if not ultimate.flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.step(run, hog, ana, fire=16)
        for _ in range(45):
            self.step(run, hog, ana)
        self.assertGreater(pool.total, 300)
        self.assertTrue(breather.flags & nodes.Ability.RUNNING)

    def test_breather_command_gap_matches_continuous_healing_across_cleanse(self):
        results = []
        for gap in (0, 40, 80):
            with self.subTest(gap=gap):
                run, hog, ana, caster = self.projected_scene()
                fight = run.match.combat
                pool = self.pool(run, hog)
                pool.damage(500)
                fight._sync_body_health(hog, pool)
                deadline = self.land(run, hog, ana, 16)
                self.assertEqual(pool.total, 50)
                self.step(run, hog, ana, fire=16)
                for _ in range(42):
                    self.step(run, hog, ana)

                command_frame = hog.body_script.frame
                omit_commands = patch.object(hog, "take_commands") if gap else nullcontext()
                with omit_commands:
                    for _ in range(11):
                        run.step({})
                    self.project(run, hog, caster)
                    self.assertNotIn(hog.body, fight.antiheal)
                    for _ in range(max(0, gap - 13)):
                        run.step({})
                if gap:
                    self.assertEqual(hog.body_script.frame, command_frame)
                    self.assertEqual(pool.total, 50)
                while run.frame < deadline - 1:
                    self.step(run, hog, ana)
                self.assertGreater(pool.total, 50)
                self.assertLess(pool.total, 350)
                if results:
                    self.assertAlmostEqual(pool.total, results[0], places=4)
                results.append(pool.total)

    def test_breather_gap_preserves_grenade_damage_credit_until_after_cleanse(self):
        results = []
        for gap in (0, 40, 80):
            with self.subTest(gap=gap):
                run, hog, ana, caster = self.projected_scene()
                fight = run.match.combat
                pool = self.pool(run, hog)
                pool.damage(500)
                fight._sync_body_health(hog, pool)
                charge = hog.body_script.component.entity_var(ULT_CHARGE)
                self.step(run, hog, ana, fire=16)
                for _ in range(42):
                    self.step(run, hog, ana)

                command_frame = hog.body_script.frame
                omit_commands = patch.object(hog, "take_commands") if gap else nullcontext()
                with omit_commands:
                    deadline = self.land(run, hog, ana, 16)
                    self.assertAlmostEqual(pool.ult_healing_deficit, 25)
                    self.assertEqual(charge.value(), 0)
                    self.project(run, hog, caster)
                    self.assertNotIn(hog.body, fight.antiheal)
                    gap_end = command_frame + gap
                    if gap:
                        self.assertLess(run.frame, gap_end)
                    while run.frame < gap_end:
                        run.step({})
                if gap:
                    self.assertEqual(hog.body_script.frame, command_frame)
                    self.assertEqual(pool.ult_healing_deficit, 25)
                    self.assertEqual(charge.value(), 0)
                while run.frame < deadline - 1:
                    self.step(run, hog, ana)
                self.assertAlmostEqual(charge.value(), 25, places=4)
                self.assertAlmostEqual(pool.ult_healing_deficit, 0, places=4)
                self.assertGreater(pool.total, 100)
                self.assertLess(pool.total, 375)
                if results:
                    self.assertAlmostEqual(pool.total, results[0], places=4)
                results.append(pool.total)

    def test_projected_barrier_keeps_existing_sleep_until_its_own_expiry(self):
        run, hog, ana, caster = self.projected_scene()
        fight = run.match.combat
        deadline = self.land(run, hog, ana, 8)
        self.project(run, hog, caster)
        self.assertEqual(fight.sleep[hog.body], deadline)
        self.assertTrue(fight.is_stunned(hog))
        while run.frame < deadline:
            self.step(run, hog, ana)
        self.assertFalse(fight.is_stunned(hog))

    def test_preexisting_projected_barrier_blocks_both_ana_effects(self):
        for button, damage in ((8, 5), (16, 50)):
            with self.subTest(button=button):
                run, hog, ana, caster = self.projected_scene()
                fight = run.match.combat
                bubble = self.project(run, hog, caster)
                self.step(run, hog, ana, cast=button)
                for _ in range(45):
                    self.step(run, hog, ana)
                self.assertEqual(self.pool(run, hog).total, 600)
                self.assertAlmostEqual(bubble.health, 200 - damage)
                self.assertNotIn(hog.body, fight.sleep)
                self.assertNotIn(hog.body, fight.antiheal)

    def test_projected_barrier_preserves_melee_buffer_even_when_dart_breaks_it(self):
        for capacity in (0, 4, 5, 200):
            with self.subTest(capacity=capacity):
                self.check_projected_sleep_against_melee_buffer(capacity)

    def check_projected_sleep_against_melee_buffer(self, capacity):
        run, hog, ana, zarya = self.projected_scene()
        fight = run.match.combat
        x, y, z = hog.mover.position
        ana.mover.position[:] = (x, y, z + 1.5)
        ana.mover.body = None
        look = aim(fight._eye(zarya), (x, y + 1, z))
        for _ in range(8):
            run.step({zarya: (*look, 0)})
        hog_pool, ana_pool = self.pool(run, hog), self.pool(run, ana)
        component = hog.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        melee = next(s for i in component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        run.step({hog: (0, 0, 0x800), zarya: (*look, 0)})
        first = run.frame
        self.assertEqual(ana_pool.total, 19970)
        run.step({zarya: (*look, 0)})
        run.step({hog: (0, 0, 0x800), zarya: (*look, 16 if capacity else 0)})
        self.assertTrue(melee.presses)
        bubble = fight.zarya_bubbles.for_target(hog)
        if capacity:
            self.assertIsNotNone(bubble)
            self.assertIs(bubble.caster, zarya)
            self.assertEqual(bubble.health, 200)
            # Seed the exact break boundary after the real projected cast.
            bubble.health = capacity
        else:
            self.assertIsNone(bubble)
        energy = zarya.body_script.component.entity_var(87)
        before_energy = energy.value() or 0

        self.step(run, hog, ana, fire=0x800, cast=8, hog_aim=(0, 0))
        for _ in range(45):
            landed = bubble.health < capacity if bubble is not None else hog.body in fight.sleep
            if landed:
                break
            self.step(run, hog, ana, fire=0x800, hog_aim=(0, 0))
        self.assertTrue(landed)
        self.assertLess(run.frame, first + 62)
        self.assertEqual(ana_pool.total, 19970)

        if bubble is None:
            self.assertEqual(hog_pool.total, 595)
            self.assertFalse(melee.presses)
            deadline = fight.sleep[hog.body]
            while run.frame < deadline - 1:
                self.step(run, hog, ana, fire=0x800, hog_aim=(0, 0))
                self.assertEqual(ana_pool.total, 19970)
            run.step({})
            self.assertEqual(run.frame, deadline)
            self.assertFalse(fight.is_stunned(hog))
            self.assertFalse(melee.presses)
            run.step({hog: (0, 0, 0x800)})
        else:
            remaining = max(0, capacity - 5)
            self.assertEqual(bubble.health, remaining)
            self.assertIs(fight.zarya_bubbles.for_target(hog), bubble if remaining else None)
            self.assertAlmostEqual(energy.value() - before_energy, min(5, capacity) * 0.2, places=3)
            self.assertEqual(hog_pool.total, 600)
            self.assertTrue(melee.presses)
            while run.frame < first + 61:
                self.step(run, hog, ana, fire=0x800, hog_aim=(0, 0))
                self.assertNotIn(hog.body, fight.sleep)
                self.assertEqual(ana_pool.total, 19970)
            run.step({hog: (0, 0, 0x800)})
            self.assertEqual(run.frame, first + 62)
            self.assertNotIn(hog.body, fight.sleep)
            self.assertEqual(hog_pool.total, 600)
        self.assertEqual(ana_pool.total, 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_personal_barrier_cleanses_antiheal_on_its_owner(self):
        run, actor, ana = self.session()
        position = tuple(actor.mover.position)
        run.match.switch_hero(actor, next(hero for hero in heroes().values() if hero.name == "Zarya"))
        actor.mover.position[:] = position
        actor.mover.body = None
        for _ in range(35):
            self.step(run, actor, ana)
        fight = run.match.combat
        self.land(run, actor, ana, 16)
        self.assertIn(actor.body, fight.antiheal)
        self.step(run, actor, ana, fire=8)
        bubble = fight.zarya_bubbles.for_target(actor)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, actor)
        self.assertNotIn(actor.body, fight.antiheal)

    def flash_scene(self):
        run, hog, ana = self.session()
        caster = run.players[2]
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 2.5, y, z)
        caster.mover.body = None
        for _ in range(35):
            run.step({})
        return run, hog, ana, caster

    def flash(self, run, hog, caster):
        fight = run.match.combat
        x, y, z = hog.mover.position
        angles = aim(fight._eye(caster), (x, y + 1, z))
        pool = self.pool(run, hog)
        before = pool.total
        run.step({caster: (*angles, 16)})
        for _ in range(50):
            if pool.total < before:
                self.assertEqual(pool.total, before - 25)
                return run.frame
            run.step({caster: (*angles, 0)})
        self.fail("Actual Flashbang did not reach Roadhog")

    def test_early_flashbang_damage_cannot_cut_sleep_below_minimum(self):
        run, hog, ana, caster = self.flash_scene()
        fight = run.match.combat
        self.land(run, hog, ana, 8)
        floor = fight.sleep_wake_floor[hog.body]
        impact = self.flash(run, hog, caster)
        self.assertLess(impact + math.ceil(0.8 / combat.TICK), floor)
        self.assertEqual(fight.sleep[hog.body], floor)
        self.assertNotIn(hog.body, fight.stuns)
        while run.frame < floor:
            self.assertTrue(fight.is_stunned(hog))
            self.step(run, hog, ana, fire=1)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.sleep_wake_floor)

    def test_late_flashbang_replaces_shorter_wake_recovery(self):
        run, hog, ana, caster = self.flash_scene()
        fight = run.match.combat
        self.land(run, hog, ana, 8)
        for _ in range(math.ceil(1.5 / combat.TICK)):
            self.step(run, hog, ana)
        impact = self.flash(run, hog, caster)
        self.assertNotIn(hog.body, fight.sleep)
        self.assertNotIn(hog.body, fight.sleep_wake_floor)
        end = impact + math.ceil(0.8 / combat.TICK)
        self.assertEqual(fight.stuns[hog.body], end)
        self.assertTrue(fight.is_stunned(hog, tick=end-1))
        self.assertFalse(fight.is_stunned(hog, tick=end))

    def test_sleep_replaces_shorter_flashbang_and_restarts_minimum(self):
        run, hog, ana, caster = self.flash_scene()
        fight = run.match.combat
        impact = self.flash(run, hog, caster)
        flash_end = fight.stuns[hog.body]
        self.land(run, hog, ana, 8)
        self.assertLess(run.frame, flash_end)
        self.assertGreater(run.frame, impact)
        self.assertNotIn(hog.body, fight.stuns)
        self.assertEqual(fight.sleep_wake_floor[hog.body], run.frame + math.ceil(1.5 / combat.TICK))

    def test_sleep_and_earthshatter_keep_longer_knockdown_in_both_orders(self):
        for sleep_first in (False, True):
            with self.subTest(sleep_first=sleep_first):
                self.check_sleep_earthshatter_overlap(sleep_first)

    def check_sleep_earthshatter_overlap(self, sleep_first):
        run, hog, ana = self.session()
        match = run.match
        fight = match.combat
        reinhardt = run.players[2]
        self.assertTrue(match.switch_hero(reinhardt, heroes()[0x02E0000000000007]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, ana.team, reinhardt.team = 0, 1, 1
        x, y, z = hog.mover.position
        reinhardt.mover.position[:] = (x + 8, y, z)
        reinhardt.mover.body = None
        angles = aim(fight._eye(reinhardt), fight._eye(hog))
        for _ in range(20):
            run.step({reinhardt: (*angles, 0)})
        pool = self.pool(run, hog)
        if sleep_first:
            self.land(run, hog, ana, 8)
            sleep_floor = fight.sleep_wake_floor[hog.body]
        before = pool.total
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({reinhardt: (*angles, 32)})
        for _ in range(85):
            if hog.body in fight.stuns:
                break
            run.step({reinhardt: (*angles, 0)})
        self.assertEqual(pool.total, before - 50)
        deadline = fight.stuns[hog.body]
        self.assertEqual(deadline - run.frame, 157)
        if sleep_first:
            self.assertLess(run.frame, sleep_floor)
        else:
            before = pool.total
            self.step(run, hog, ana, cast=8)
            for _ in range(40):
                if pool.total < before:
                    break
                self.step(run, hog, ana)
            self.assertLess(pool.total, before)
            self.assertEqual(fight.stuns[hog.body], deadline)
        self.assertNotIn(hog.body, fight.sleep)
        self.assertNotIn(hog.body, fight.sleep_wake_floor)
        while run.frame < deadline - 1:
            run.step({hog: (0, 0, 8 | 16)})
            self.assertFalse(hog.body_script.component.held)
        run.step({hog: (0, 0, 0)})
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)

    def test_server_snapshot_includes_control_and_healing_deadlines(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        fight = player.match.combat
        baseline = snapshot_digest(case.capture(session))
        fight.sleep[player.body] = fight.tick + 32
        sleeping = snapshot_digest(case.capture(session))
        self.assertNotEqual(sleeping, baseline)
        self.assertEqual(snapshot_digest(case.capture(session)), sleeping)
        fight.sleep_wake_floor[player.body] = fight.tick + 16
        bounded = snapshot_digest(case.capture(session))
        self.assertNotEqual(bounded, sleeping)
        fight.antiheal[player.body] = fight.tick + 250
        denied = snapshot_digest(case.capture(session))
        self.assertNotEqual(denied, bounded)
        fight.heal_boost[player.body] = fight.tick + 250
        boosted = snapshot_digest(case.capture(session))
        self.assertNotEqual(boosted, denied)
        self.assertEqual(snapshot_digest(case.capture(session)), boosted)
        fight.healing_status_history[player.body] = [(True, fight.tick - 40, fight.tick + 250)]
        historical = snapshot_digest(case.capture(session))
        self.assertNotEqual(historical, boosted)
        self.assertEqual(snapshot_digest(case.capture(session)), historical)
