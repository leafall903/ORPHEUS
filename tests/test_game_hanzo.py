"""Hanzo's actual command/volley damage and reconstructed ballistic collision.

These are headless server checks, not original-client or five-Talon-NPC acceptance.
"""

import unittest
from dataclasses import replace
from itertools import product
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bodytrace, combat, movelog
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes

HANZO = 0x02E0000000000005


class HanzoCombatTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, distance=5):
        run = Run(KANEZAKA_DM, ("Hanzo", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, victim = run.players
        match.switch_hero(shooter, heroes()[HANZO])
        x, y, z = shooter.mover.position
        victim.mover.position = [x, y, z + distance]
        for _ in range(20):
            run.step({})
        return run, shooter, victim

    def test_full_draw_releases_one_damaging_ballistic_arrow(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
        for _ in range(70):
            run.step({shooter: (yaw, pitch, 1)})
        self.assertFalse(fight.rockets)
        run.step({shooter: (yaw, pitch, 0)})
        arrow, = fight.rockets
        self.assertAlmostEqual(arrow.gravity, 9.8, places=5)
        self.assertEqual(arrow.direct.amount, 125)
        self.assertEqual(arrow.speed, 110)
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 75)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 1)

    def test_orisa_fortify_reduces_full_bow_until_authored_expiry(self):
        self.verify_fortify_bow(head=False, after_expiry=260)

    def test_orisa_fortify_suppresses_headshot_until_authored_expiry(self):
        self.verify_fortify_bow(head=True, after_expiry=135)

    def test_bow_catchup_respects_fortify_expiry_at_contact(self):
        for skipped, (remaining, expected) in product((False, True), ((0.1, 330), (0.3, 380))):
            with self.subTest(skipped=skipped, remaining=remaining):
                run, hero, orisa = self.arena(distance=20)
                match = run.match
                fight = match.combat
                position = tuple(orisa.mover.position)
                self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = position
                orisa.mover.body = None
                for _ in range(25):
                    run.step({})
                x, y, z = orisa.mover.position
                angles = aim(fight._eye(hero), (x, y + 1.1, z))
                run.step({orisa: (0, 0, 8)})
                fortify = combat._active_fortify(orisa)
                self.assertIsNotNone(fortify)
                for _ in range(250):
                    if fortify.instance.vars[215].value() < remaining:
                        break
                    button = int(fortify.instance.vars[215].value() < remaining + 1.3)
                    run.step({hero: (*angles, button)})
                self.assertTrue(fortify.active)
                run.step({hero: (*angles, 0)})
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)
                run.step({})
                self.assertIn(arrow, fight.rockets)
                if skipped:
                    tick = run.frame + 25
                    match.update(tick * combat.TICK, tick, frames=False)
                else:
                    for _ in range(25):
                        run.step({})
                self.assertNotIn(arrow, fight.rockets)
                self.assertAlmostEqual(fight.vitals[orisa.body].total, expected, places=4)

    def test_fresh_fortify_cannot_reduce_earlier_catchup_bow(self):
        run, hero, orisa = self.arena(distance=20)
        match = run.match
        fight = match.combat
        position = tuple(orisa.mover.position)
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = position
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        x, y, z = orisa.mover.position
        angles = aim(fight._eye(hero), (x, y + 1.1, z))
        for _ in range(70):
            run.step({hero: (*angles, 1)})
        run.step({hero: (*angles, 0)})
        arrow, = fight.rockets
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertIn(arrow, fight.rockets)
        orisa.take_commands([Command(run.frame, buttons=8)])
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        duration = fortify.instance.vars[215]
        before = duration.value()
        run.frame += 300
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(fight.vitals[orisa.body].total, 330, places=4)
        self.assertTrue(fortify.active)
        self.assertLessEqual(abs(duration.value() - before), combat.TICK)
        for _ in range(70):
            run.step({hero: (*angles, 1)})
        run.step({hero: (*angles, 0)})
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertTrue(fortify.active)
        self.assertAlmostEqual(fight.vitals[orisa.body].total, 260, places=4)

    def test_fortify_cooldown_requires_new_press_and_restores_bow_reduction(self):
        run, hero, orisa = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = orisa.mover.position
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = (x, y, z)
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        angles = aim(fight._eye(hero), (x, y + 1, z))
        run.step({orisa: (0, 0, 8)})
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        instance = fortify.instance
        for _ in range(260):
            if not fortify.active:
                break
            run.step({})
        self.assertFalse(fortify.active)
        cooldown = instance.vars[61]
        self.assertGreater(cooldown.value(), 0)
        run.step({orisa: (0, 0, 8)})
        self.assertFalse(fortify.active)
        for _ in range(700):
            if cooldown.value() <= 0:
                break
            run.step({})
            self.assertFalse(fortify.active)
        self.assertEqual(cooldown.value(), 0)
        for _ in range(70):
            run.step({hero: (*angles, 1)})
            self.assertFalse(fortify.active)
        run.step({hero: (*angles, 1), orisa: (0, 0, 8)})
        self.assertTrue(fortify.active)
        run.step({hero: (*angles, 0)})
        for _ in range(10):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(fight.vitals[orisa.body].total, 380, places=4)

    def verify_fortify_bow(self, *, head, after_expiry):
        run, shooter, orisa = self.arena()
        match = run.match
        fight = match.combat
        position = tuple(orisa.mover.position)
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = position
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0CF4)
        point = fight._eye(orisa) if head else (position[0], position[1] + 1, position[2])
        angles = aim(fight._eye(shooter), point)
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 450)
        for active, expected in ((True, 380), (False, after_expiry)):
            for _ in range(70):
                run.step({shooter: (*angles, 1)})
            run.step({shooter: (*angles, 1), orisa: (0, 0, 8)})
            state = instance.states[4]
            self.assertEqual(state.node.pos, 10)
            self.assertEqual(state.active, active)
            script = shooter.body_script
            self.assertIsNone(script.ult_seconds)
            charge = script.component.entity_var(combat.ULT_CHARGE)
            initial_charge, initial_hp = charge.value(), pool.total
            initial_frame, passive_ms = script.frame, script.passive_charge_ms
            run.step({shooter: (*angles, 0)})
            arrow, = fight.rockets
            self.assertEqual(arrow.direct.amount, 125)
            if head:
                # Isolate Fortify from the unresolved native bow critical default.
                arrow.direct = replace(arrow.direct, critical=2)
            for _ in range(10):
                run.step({})
            self.assertFalse(fight.rockets)
            self.assertEqual(state.active, active)
            self.assertAlmostEqual(pool.total, expected, places=4)
            elapsed_ms = (script.frame - initial_frame) * script.frame_ms
            passive = ((passive_ms + elapsed_ms) // 1000) * 5
            self.assertAlmostEqual(charge.value() - initial_charge, initial_hp - expected + passive, places=4)
            hit = shooter.client.messages(combat.HIT)[-1]
            self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), head and not active)
            for _ in range(260):
                if not state.active:
                    break
                run.step({})
            self.assertFalse(state.active)

    def test_quick_melee_damage_changes_when_orisa_fortify_expires(self):
        run, hero, orisa = self.arena(distance=1.8)
        match = run.match
        fight = match.combat
        x, y, z = orisa.mover.position
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = (x, y, z)
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        angles = aim(fight._eye(hero), (x, y + 1, z))
        run.step({orisa: (0, 0, 8)})
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
        self.assertEqual(pool.total, 450)
        run.step({hero: (*angles, 0x800)})
        self.assertTrue(fortify.active)
        self.assertAlmostEqual(pool.total, 437, places=4)
        for _ in range(260):
            if not fortify.active:
                break
            run.step({})
        self.assertFalse(fortify.active)
        self.assertAlmostEqual(pool.total, 437, places=4)
        run.step({hero: (*angles, 0x800)})
        self.assertAlmostEqual(pool.total, 412, places=4)
        hits = hero.client.messages(combat.HIT)
        self.assertEqual(len(hits), 2)
        for hit, expected in zip(hits, (13, 25), strict=True):
            self.assertAlmostEqual(hit["+0x78"]["+0x4"], expected, places=4)

    def test_quick_melee_cooldown_buffers_only_near_ready_and_requires_a_fresh_press(self):
        for buttons, expected_hits in (
            ({0, 53}, [0]),
            ({0, 54}, [0, 62]),
            ({0, 61}, [0, 62]),
            ({0, 62}, [0, 62]),
            ({0, 63}, [0, 63]),
            (set(range(80)) | {81}, [0, 81]),
        ):
            with self.subTest(expected_hits=expected_hits):
                run, shooter, victim = self.arena(distance=1.8)
                fight = run.match.combat
                x, y, z = victim.mover.position
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
                hits_at = []
                for frame in range(100):
                    run.step({shooter: (yaw, pitch, 0x800 if frame in buttons else 0)})
                    hits = shooter.client.messages(combat.HIT)
                    if len(hits) != len(hits_at):
                        self.assertEqual(len(hits), len(hits_at) + 1)
                        self.assertEqual(hits[-1]["+0x78"]["+0x4"], 30)
                        hits_at.append(frame)
                self.assertEqual(hits_at, expected_hits)
                self.assertEqual(fight.vitals[victim.body].total, 200 - 30 * len(expected_hits))

    def test_storm_volley_switches_damage_as_orisa_fortify_expires(self):
        run, hero, orisa = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = orisa.mover.position
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = (x, y, z)
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        angles = aim(fight._eye(hero), (x, y + 1, z))
        run.step({orisa: (0, 0, 8)})
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        for _ in range(250):
            if fortify.instance.vars[215].value() < 0.35:
                break
            run.step({})
        self.assertTrue(fortify.active)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
        hits = []
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as shots:
            run.step({hero: (*angles, 16)})
            run.step({})
            for _ in range(140):
                before = pool.total
                active = fortify.active
                run.step({hero: (*angles, 1)})
                if pool.total < before:
                    hits.append((active, round(before - pool.total, 4)))
                if len(hits) == 5:
                    break
        self.assertEqual([call.args[3] for call in shots.call_args_list], [0x040000000000270F] * 5)
        self.assertEqual(len(hits), 5)
        self.assertEqual(hits[0], (True, 37))
        self.assertEqual(next(hit for hit in hits if not hit[0]), (False, 65))
        self.assertFalse(fortify.active)

    def test_storm_arrows_deal_authored_damage_and_kill(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        run.step({shooter: (0, 0, 0x10)})
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                run.step({shooter: (0, 0, 1)})
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [70, 70, 60])
        self.assertTrue(hits[-1]["+0x78"]["+0x8"] & combat.KILLING)
        self.assertTrue(fight.vitals[victim.body].dead)

    def test_enemy_visor_helix_hits_hanzo_while_off_axis_rifle_misses(self):
        run, hanzo, soldier = self.arena()
        fight = run.match.combat
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(combat.ULT_COST).value())
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        x, y, z = hanzo.mover.position
        angles = aim(fight._eye(soldier), (x, y + 1, z))
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        run.step({soldier: (*angles, 32)})
        self.assertTrue(visor.states[0].flags & 1)
        self.assertEqual(script.component.entity_var(combat.ULT_CHARGE).value(), 0)

        for _ in range(90):
            run.step({soldier: (*angles, 0)})
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rifle:
            run.step({soldier: (16384, 0, 1)})
        rifle.assert_called_once()
        self.assertEqual(pool.total, 200)
        self.assertFalse(soldier.client.messages(combat.HIT))
        for _ in range(10):
            run.step({soldier: (*angles, 0)})
        run.step({soldier: (*angles, 2)})
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, soldier)
        self.assertEqual(rocket.shot.definition, 0x0400000000001C86)
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
        for _ in range(15):
            run.step({})
        self.assertTrue(visor.states[0].flags & 1)
        self.assertEqual(pool.total, 80)
        hits = soldier.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [40, 80])
        for _ in range(400):
            if not visor.states[0].flags & 1:
                break
            run.step({})
        self.assertFalse(visor.states[0].flags & 1)
        self.assertFalse(fight.rockets)

    def test_bow_kills_active_visor_soldier_before_his_inflight_helix_hits(self):
        run, hanzo, soldier = self.arena(distance=12)
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        hanzo_pool = fight.vitals_of(targets[hanzo])
        soldier_pool = fight.vitals_of(targets[soldier])
        soldier_pool.damage(75)
        hanzo_pool.damage(80)
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(combat.ULT_COST).value())
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        hx, hy, hz = hanzo.mover.position
        sx, sy, sz = soldier.mover.position
        rifle_aim = aim(fight._eye(soldier), (hx, hy + 1, hz))
        bow_aim = aim(fight._eye(hanzo), (sx, sy + 1, sz))
        run.step({soldier: (*rifle_aim, 32)})
        for _ in range(90):
            run.step({hanzo: (*bow_aim, 1)})
        self.assertTrue(visor.states[0].flags & 1)
        old_hanzo, old_soldier = hanzo.body, soldier.body
        run.step({hanzo: (*bow_aim, 0), soldier: (*rifle_aim, 2)})
        arrow, rocket = fight.rockets
        self.assertIs(arrow.shooter, hanzo)
        self.assertIs(rocket.shooter, soldier)
        self.assertEqual(rocket.shot.definition, 0x0400000000001C86)

        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(20):
                run.step({})
                if soldier_pool.dead:
                    break
            self.assertTrue(soldier_pool.dead)
            self.assertEqual(soldier_pool.killer, old_hanzo)
            self.assertFalse(hanzo_pool.dead)
            self.assertTrue(rocket in fight.rockets)
            for _ in range(20):
                run.step({soldier: (*rifle_aim, 1)})
                if hanzo_pool.dead:
                    break
        self.assertTrue(hanzo_pool.dead)
        self.assertEqual(hanzo_pool.killer, old_soldier)
        self.assertFalse(fight.rockets)
        shoot.assert_not_called()

    def test_sonic_arrow_selection_preserves_direct_damage(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        run.step({shooter: (0, 0, 0x08)})
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(70):
                run.step({shooter: (0, 0, 1)})
            run.step({})
        call, = fired.call_args_list
        self.assertEqual(call.args[3], 0x0400000000000149)
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 75)
        # Reveal and attachment remain open; selection and damage alone do not
        # establish Sonic Arrow acceptance.

    def test_storm_cooldown_rejects_old_press_and_allows_fresh_press(self):
        run, shooter, _ = self.arena()
        fight = run.match.combat
        ability = next(i for i in shooter.body_script.component.instances.values()
                       if i.graph.index == 0x146A)
        run.step({shooter: (0, 0, 0x10)})
        run.step({})
        for _ in range(140):
            run.step({shooter: (0, 0, 1)})
        run.step({})
        self.assertEqual(ability.find_var(0, 472).value(), 10)
        self.assertGreater(ability.find_var(0, 61).value(), 8)
        run.step({shooter: (0, 0, 0x10)})  # rejected early retry during cooldown
        run.step({})
        for _ in range(700):
            run.step({})
        self.assertEqual(ability.find_var(0, 61).value(), 0)
        self.assertFalse(ability.states[0].flags & 1)
        run.step({shooter: (0, 0, 0x10)})  # a fresh edge after the10s cooldown
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                run.step({shooter: (0, 0, 1)})
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)

    def test_quick_melee_changes_enemy_health_once(self):
        run, shooter, victim = self.arena(distance=2)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals[victim.body].total, 170)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 1)

    def test_storm_near_deadline_tap_and_held_retry_start_once_when_ready(self):
        for retry in ('near', 'held'):
            with self.subTest(retry=retry):
                run, shooter, _ = self.arena()
                ability = next(i for i in shooter.body_script.component.instances.values()
                               if i.graph.index == 0x146A)
                run.step({shooter: (0, 0, 0x10)})
                run.step({})
                for _ in range(140):
                    run.step({shooter: (0, 0, 1)})
                run.step({})
                state = ability.states[0]
                self.assertGreater(ability.vars[61].value(), 8)
                with patch.object(state, 'start_ability', wraps=state.start_ability) as starts:
                    if retry == 'near':
                        while ability.vars[61].value() > 0.08:
                            run.step({})
                        run.step({shooter: (0, 0, 0x10)})
                        run.step({})
                    for _ in range(700):
                        if starts.called:
                            break
                        run.step({shooter: (0, 0, 0x10)} if retry == 'held' else {})
                    self.assertEqual(starts.call_count, 1)
                    self.assertTrue(state.flags & 1)
                    for _ in range(10):
                        run.step({shooter: (0, 0, 0x10)} if retry == 'held' else {})
                    self.assertEqual(starts.call_count, 1)

    def test_melee_stops_at_wall_but_can_hit_a_target_before_it(self):
        for wall_distance, expected in ((1, 200), (3, 170)):
            with self.subTest(wall_distance=wall_distance):
                run, shooter, victim = self.arena(distance=2)
                x, y, z = shooter.mover.position
                w = z + wall_distance
                run.match.collision_world = World([
                    (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
                    (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
                    (x-10, y, w, x+10, y, w, x+10, y+5, w),
                    (x-10, y, w, x+10, y+5, w, x-10, y+5, w),
                    (x-10, y, w, x+10, y+5, w, x+10, y, w),
                    (x-10, y, w, x-10, y+5, w, x+10, y+5, w),
                ], name="explicit floor and melee wall")
                run.step({shooter: (0, 0, 0x800)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(run.match.combat.vitals[victim.body].total, expected)

    def test_enemy_player_can_damage_and_kill_hanzo(self):
        run, hanzo, enemy = self.arena()
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertEqual(fight.vitals[hanzo.body].total, 180)
        for _ in range(90):
            run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(fight.vitals[hanzo.body].dead)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(70):
                run.step({hanzo: (0, 0, 1)})
            run.step({})
        self.assertFalse(shoot.called)

    def test_ballistic_path_survives_gap_without_lifetime_extension(self):
        results = []
        for advances in (range(1, 11), (10,)):
            run, shooter, victim = self.arena()
            victim.mover.position[2] += 100
            fight = run.match.combat
            start = fight._eye(shooter)
            arrow = combat.Rocket(shooter, shooter.team, start, (0, 0, 1), 25,
                                  0.1, combat.HitDef(125), None,
                                  last_tick=run.frame, gravity=9.8)
            fight.rockets.append(arrow)
            for offset in advances:
                tick = run.frame + offset
                run.match.update(tick * combat.TICK, tick)
            self.assertFalse(fight.rockets)
            self.assertAlmostEqual(arrow.position[1], start[1] - 0.5 * 9.8 * 0.1**2)
            self.assertAlmostEqual(arrow.position[2], start[2] + 2.5)
            self.assertAlmostEqual(arrow.flown, 0.1)
            results.append(arrow.position)
        for a, b in zip(*results, strict=True):
            self.assertAlmostEqual(a, b)

    def test_low_speed_arrow_drops_under_target_instead_of_hitting_straight_ahead(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        arrow = combat.Rocket(shooter, shooter.team, fight._eye(shooter), (0, 0, 1),
                              5, 2, combat.HitDef(125), None,
                              last_tick=run.frame, gravity=9.8)
        fight.rockets.append(arrow)
        for _ in range(80):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 200)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)  # intercepted by the collision floor

    def test_projectile_critical_multiplier_uses_impact_height(self):
        # Explicit definitions exercise shared impact-height semantics.
        for height, multiplier, expected, critical in (
            (1.55, 2, 50, True), (0.8, 2, 125, False), (1.55, 1, 125, False),
        ):
            with self.subTest(height=height, multiplier=multiplier):
                run, shooter, victim = self.arena()
                fight = run.match.combat
                x, y, z = shooter.mover.position
                arrow = combat.Rocket(shooter, shooter.team, (x, y + height, z),
                                      (0, 0, 1), 100, 1,
                                      combat.HitDef(75, critical=multiplier), None)
                fight.rockets.append(arrow)
                for _ in range(5):
                    run.step({})
                self.assertEqual(fight.vitals[victim.body].total, expected)
                hit, = shooter.client.messages(combat.HIT)
                self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), critical)

    def test_upper_capsule_uses_explicit_rifle_and_reconstructed_bow_multiplier(self):
        for weapon, amount in (("rifle", 40), ("bow", 250)):
            with self.subTest(weapon=weapon):
                run, shooter, victim = self.arena(distance=3)
                match = run.match
                fight = match.combat
                if weapon == "rifle":
                    position = tuple(shooter.mover.position)
                    self.assertTrue(match.switch_hero(shooter, heroes()[SOLDIER]))
                    shooter.mover.position[:] = position
                    shooter.mover.body = None
                    for _ in range(25):
                        run.step({})
                vitals = fight.vitals[victim.body] = combat.Vitals(victim.body, 500)
                target = next(t for t in fight._targets() if t.owner is victim)
                x, y, z = target.feet
                angles = aim(fight._eye(shooter), (x, y + target.eye - 0.05, z))
                if weapon == "rifle":
                    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                        run.step({shooter: (*angles, 1)})
                    hitscan.assert_called_once()
                    self.assertEqual(hitscan.call_args.args[4].critical, 2)
                else:
                    for _ in range(70):
                        run.step({shooter: (*angles, 1)})
                    run.step({shooter: (*angles, 0)})
                    arrow, = fight.rockets
                    self.assertEqual(arrow.direct.critical, 2)
                    for _ in range(12):
                        if not fight.rockets:
                            break
                        run.step({})
                    self.assertGreaterEqual(arrow.position[1], y + target.eye - combat.HEAD_BELOW)
                hit, = shooter.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x4"], amount)
                self.assertTrue(hit["+0x78"]["+0x8"] & combat.CRITICAL)
                self.assertEqual(vitals.total, 500 - amount)
