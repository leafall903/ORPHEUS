"""Actual Ana Biotic Grenade and Soldier field against Torbjorn."""

import os
import unittest
from dataclasses import replace

import test_game_torbjorn_sleep as sleep_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, projectiles, stats, zarya_bubbles
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared combat inputs")
class TorbjornAntihealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sleep_tests.TorbjornSleepTests.setUpClass()
        cls.addClassCleanup(sleep_tests.TorbjornSleepTests.doClassCleanups)

    def arena(self):
        fixture, run, torb, ana = sleep_tests.TorbjornSleepTests().arena()
        soldier = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        soldier.team = torb.team
        ana.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(soldier, (x + 2.5, y, z))
        fixture.advance(run, torb, ana, 20)
        return fixture, run, torb, ana, soldier

    def grenade(self, fixture, run, torb, ana):
        fight = run.match.combat
        fixture.step(run, torb, ana, defense=16)
        for _ in range(70):
            if torb.body in fight.antiheal:
                return fight.antiheal[torb.body][-1]
            fixture.step(run, torb, ana)
        self.fail("actual Biotic Grenade did not apply antiheal")

    def test_field_denies_heal_until_exact_expiry_even_with_skipped_ticks(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                fixture, run, torb, ana, soldier = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(soldier, (x + 8, y, z))
                start, end = self.grenade(fixture, run, torb, ana)
                fixture.place(soldier, (x + 2.5, y, z))
                self.assertEqual(end - start, 250)
                hp = fixture.health(run, torb)
                self.assertEqual(hp.total, 205)
                run.step({soldier: (0, 0, 16)})
                fixture.advance(run, torb, ana, 60)
                fields = [shot for shot in fight.projectiles.shots if shot.player is soldier]
                self.assertTrue(any(shot.field_tick is not None for shot in fields))
                self.assertEqual(hp.total, 205)
                self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                fixture.advance(run, torb, ana, end - run.frame - 3)
                self.assertEqual(hp.total, 205)
                if skip:
                    run.frame = end + 3
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    fixture.advance(run, torb, ana, 6)
                self.assertNotIn(torb.body, fight.antiheal)
                amount = 4 * projectiles.HEAL_PER_SECOND * combat.TICK
                self.assertAlmostEqual(hp.total, 205 + amount, places=5)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), amount, places=5)

    def test_antiheal_outlasts_ana_death_and_field_resumes_on_original_deadline(self):
        fixture, run, torb, ana, soldier = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(soldier, (x + 8, y, z))
        start, deadline = self.grenade(fixture, run, torb, ana)
        hp = fixture.health(run, torb)
        ana_hp = fixture.health(run, ana)
        fixture.place(soldier, (x + 2.5, y, z))
        run.step({soldier: (0, 0, 16)})
        for _ in range(185):
            if ana_hp.dead:
                break
            fixture.step(run, torb, ana, fire=1)
        self.assertTrue(ana_hp.dead)
        self.assertEqual(ana_hp.killer, torb.body)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 200)
        self.assertLess(run.frame, deadline - 1)
        self.assertEqual(fight.antiheal[torb.body], [(start, deadline)])
        self.assertTrue(any(shot.player is soldier and shot.field_tick is not None
                            for shot in fight.projectiles.shots))
        while run.frame < deadline - 1:
            self.assertEqual(hp.total, 205)
            self.assertEqual(soldier.stats.value(stats.HEALING), 0)
            run.step({})
        self.assertEqual(hp.total, 205)
        run.step({})
        heal = projectiles.HEAL_PER_SECOND * combat.TICK
        self.assertAlmostEqual(hp.total, 205 + heal)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), heal)
        self.assertNotIn(torb.body, fight.antiheal)

    def test_second_grenade_at_expiry_boundary_controls_healing_on_that_frame(self):
        for offset in (-1, 0, 1):
            with self.subTest(offset=offset):
                self.exercise_antiheal_reapplication(offset)

    def exercise_antiheal_reapplication(self, offset):
        fixture, run, torb, ana, soldier = self.arena()
        match = run.match
        fight = match.combat
        second = match.add_player(8, "Second Ana", ana.hero.guid, ana.team, False)
        in_world(match, second)
        run.players.append(second)
        self.assertTrue(match.switch_hero(second, heroes()[ana.hero.guid]))
        x, y, z = torb.mover.position
        fixture.place(second, (x - 6, y, z))
        fixture.place(soldier, (x + 8, y, z))
        fixture.advance(run, torb, ana, 35)
        start, deadline = self.grenade(fixture, run, torb, ana)
        hp = fixture.health(run, torb)
        fixture.advance(run, torb, ana, deadline - 72 - run.frame)
        fixture.place(soldier, (x + 2.5, y, z))
        run.step({soldier: (0, 0, 16)})
        fixture.advance(run, torb, ana, 50)
        field, = (shot for shot in fight.projectiles.shots
                  if shot.player is soldier and shot.field_tick is not None)
        fixture.place(soldier, (x + 8, y, z))

        # The second grenade reaches Torbjorn 13 frames after the input frame is queued.
        impact = deadline + offset
        fixture.advance(run, torb, ana, impact - 13 - run.frame)
        fixture.step(run, torb, second, defense=16)
        fixture.advance(run, torb, second, impact - 1 - run.frame)
        heal = projectiles.HEAL_PER_SECOND * combat.TICK
        gap = heal if offset > 0 else 0
        self.assertAlmostEqual(hp.total, 205 + gap)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), gap)
        if offset > 0:
            self.assertNotIn(torb.body, fight.antiheal)
        else:
            self.assertEqual(fight.antiheal[torb.body], [(start, deadline)])

        run.step({})
        self.assertEqual(run.frame, impact)
        updated_deadline = impact + (deadline - start)
        updated_start = impact if offset > 0 else start
        self.assertEqual(fight.antiheal[torb.body], [(updated_start, updated_deadline)])
        self.assertAlmostEqual(hp.total, 160 + gap)
        fixture.advance(run, torb, second, updated_deadline - 1 - run.frame)
        self.assertIn(field, fight.projectiles.shots)
        self.assertAlmostEqual(hp.total, 160 + gap)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), gap)
        run.step({})
        self.assertNotIn(torb.body, fight.antiheal)
        self.assertAlmostEqual(hp.total, 160 + gap + heal)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), gap + heal)

    def test_transcendence_heals_base_armour_only_at_antiheal_expiry(self):
        from ow174.game import phase_defenses

        fixture, run, torb, ana, zen = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        self.assertTrue(run.match.switch_hero(zen, heroes()[phase_defenses.ZENYATTA]))
        fixture.place(zen, (x + 8, y, z))
        fixture.advance(run, torb, ana, 35)
        start, deadline = self.grenade(fixture, run, torb, ana)
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 205)
        run.step({torb: (0, 0, 16)})
        self.assertEqual(hp.over_armour.amount, 100)
        for _ in range(100):
            if hp.over_armour.amount < 100:
                break
            fixture.step(run, torb, ana, defense=1)
        fixture.advance(run, torb, ana, 55)
        self.assertFalse(fight.rifle_wounds.active)
        self.assertAlmostEqual(hp.over_armour.amount, 30)
        source = hp.over_armour.source
        fixture.place(zen, (x + 3, y, z))
        self.cast_allied_transcendence(run, zen)
        self.assertEqual(fight.antiheal[torb.body], [(start, deadline)])
        while run.frame < deadline - 1:
            self.assertAlmostEqual(hp.total, 235)
            self.assertEqual(zen.stats.value(stats.HEALING), 0)
            run.step({})
        self.assertAlmostEqual(hp.total, 235)
        run.step({})
        self.assertEqual(run.frame, deadline)
        self.assertNotIn(torb.body, fight.antiheal)
        self.assertAlmostEqual(hp.total, 239.8)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)
        fixture.advance(run, torb, ana, 10)
        self.assertAlmostEqual(hp.total, 280)
        self.assertEqual(hp.now[combat.ARMOUR_POOL], 50)
        self.assertEqual(hp.over_armour.source, source)
        self.assertAlmostEqual(hp.over_armour.amount, 30)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 45)

    def test_transcendence_final_pulse_uses_live_countdown_and_replacement(self):
        from ow174.game import phase_defenses
        from ow174.game.content import SOLDIER

        for replace_zen in (False, True):
            with self.subTest(replace_zen=replace_zen):
                fixture, run, torb, ana, zen = self.arena()
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                self.assertTrue(match.switch_hero(zen, heroes()[phase_defenses.ZENYATTA]))
                fixture.place(zen, (x + 12, y, z))
                fixture.advance(run, torb, ana, 35)
                fixture.step(run, torb, ana, defense=1)
                fixture.advance(run, torb, ana, 55)
                hp = fixture.health(run, torb)
                injured = hp.total
                self.assertLess(injured, 235.6)
                self.assertFalse(fight.rifle_wounds.active)

                self.cast_allied_transcendence(run, zen)
                countdown = phase_defenses._transcendence_countdown(zen)
                self.assertIsNotNone(countdown)
                component = countdown.instance.component
                limit = (countdown.start + 6000) // component.frame_ms
                self.assertEqual(limit - run.frame, 375)
                while run.frame < limit - 3:
                    run.step({})
                self.assertEqual(hp.total, injured)
                fixture.place(zen, (x + 3, y, z))
                run.step({})
                self.assertEqual(run.frame, limit - 2)
                self.assertTrue(countdown.active)
                self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
                self.assertAlmostEqual(hp.total, injured + 4.8)

                if replace_zen:
                    old_body = zen.body
                    self.assertTrue(match.switch_hero(zen, heroes()[SOLDIER]))
                    self.assertNotEqual(zen.body, old_body)
                run.step({})
                self.assertEqual(run.frame, limit - 1)
                self.assertFalse(phase_defenses.transcendence_invulnerable(zen, run.frame))
                expected = 4.8 if replace_zen else 9.6
                if not replace_zen:
                    self.assertTrue(countdown.reached)
                    self.assertFalse(countdown.active)
                    self.assertEqual(component.now, countdown.start + 5999)
                self.assertAlmostEqual(hp.total, injured + expected)
                self.assertAlmostEqual(zen.stats.value(stats.HEALING), expected)
                fixture.advance(run, torb, ana, 5)
                self.assertAlmostEqual(hp.total, injured + expected)
                self.assertAlmostEqual(zen.stats.value(stats.HEALING), expected)

    def test_transcendence_healing_checks_live_team_range_and_wall(self):
        from ow174.game import phase_defenses

        fixture, run, torb, ana, zen = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = torb.mover.position
        self.assertTrue(match.switch_hero(zen, heroes()[phase_defenses.ZENYATTA]))
        fixture.place(zen, (x + 12, y, z))
        fixture.advance(run, torb, ana, 35)
        fixture.step(run, torb, ana, defense=1)
        fixture.advance(run, torb, ana, 55)
        hp = fixture.health(run, torb)
        injured = hp.total
        self.assertLess(injured, 245.2)
        self.assertFalse(fight.rifle_wounds.active)
        self.cast_allied_transcendence(run, zen)
        fixture.advance(run, torb, ana, 3)
        self.assertEqual(hp.total, injured)

        fixture.place(zen, (x + 3, y, z))
        allied_team = torb.team
        torb.team = ana.team
        fixture.advance(run, torb, ana, 3)
        self.assertEqual(hp.total, injured)
        torb.team = allied_team
        open_world = match.collision_world
        match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(x + 1.5, z - 4, x + 1.5, z + 4, y0=y, y1=y + 4)
        )
        fixture.advance(run, torb, ana, 3)
        self.assertEqual(hp.total, injured)
        self.assertEqual(zen.stats.value(stats.HEALING), 0)
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))

        match.collision_world = open_world
        run.step({})
        self.assertAlmostEqual(hp.total, injured + 4.8)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)

    def cast_allied_transcendence(self, run, zen):
        from ow174.game import phase_defenses

        component = zen.body_script.component
        zen.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({zen: (0, 0, 32)})
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))

    def test_projected_barrier_cleanses_grenade_and_live_field_resumes_healing(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                fixture, run, torb, ana, soldier = self.arena()
                match = run.match
                fight = match.combat
                zarya = match.add_player(8, "Zarya", zarya_bubbles.HERO, torb.team, False)
                in_world(match, zarya)
                run.players.append(zarya)
                self.assertTrue(match.switch_hero(zarya, heroes()[zarya_bubbles.HERO]))
                x, y, z = torb.mover.position
                fixture.place(zarya, (x - 5, y, z))
                fixture.place(soldier, (x + 8, y, z))
                fixture.advance(run, torb, ana, 35)
                start, deadline = self.grenade(fixture, run, torb, ana)
                fixture.place(soldier, (x + 2.5, y, z))
                run.step({soldier: (0, 0, 16), torb: (0, 0, 16)})
                fixture.advance(run, torb, ana, 60)
                hp = fixture.health(run, torb)
                self.assertEqual(hp.total, 305)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                self.assertTrue(any(shot.player is soldier and shot.field_tick is not None
                                    for shot in fight.projectiles.shots))
                yaw, pitch = fixture.body_aim(run, zarya, torb)
                away = yaw - 32768 if yaw >= 0 else yaw + 32768
                run.step({zarya: (away, pitch, 16)})
                self.assertIsNone(fight.bubbles.for_target(torb))
                self.assertEqual(fight.antiheal[torb.body], [(start, deadline)])
                run.step({})
                run.step({zarya: (yaw, pitch, 16)})
                cleanse = run.frame
                barrier = fight.bubbles.for_target(torb)
                self.assertIsNotNone(barrier)
                self.assertIs(barrier.caster, zarya)
                self.assertLess(cleanse + 6, deadline)
                self.assertEqual(fight.antiheal[torb.body], [(start, cleanse)])

                if skip:
                    run.frame += 6
                    match.update(run.frame * combat.TICK, run.frame)
                else:
                    fixture.advance(run, torb, ana, 6)
                amount = 6 * projectiles.HEAL_PER_SECOND * combat.TICK
                self.assertAlmostEqual(hp.total, 305 + amount)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), amount, places=5)
                self.assertEqual(barrier.health, 200)
                self.assertEqual(zarya.body_script.component.entity_var(87).value() or 0, 0)

                damage = ana.stats.value(stats.HERO_DAMAGE)
                fixture.step(run, torb, ana, defense=1)
                fixture.advance(run, torb, ana, 30)
                self.assertEqual(barrier.health, 130)
                self.assertFalse(fight.rifle_wounds.active)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), damage)
                for _ in range(130):
                    if fight.bubbles.for_target(torb) is None:
                        break
                    fixture.step(run, torb, ana)
                self.assertIsNone(fight.bubbles.for_target(torb))
                self.assertEqual(hp.over_armour.amount, 100)
                fixture.step(run, torb, ana, defense=1)
                fixture.advance(run, torb, ana, 55)
                self.assertAlmostEqual(hp.over_armour.amount, 30)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE) - damage, 70, places=4)

    def test_grenade_antiheal_retires_on_reselect_or_lethal_rifle_before_healing(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.exercise_antiheal_retirement(lethal=lethal)

    def exercise_antiheal_retirement(self, *, lethal):
        from ow174.game import hero_torbjorn

        fixture, run, torb, ana, soldier = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = torb.mover.position
        fixture.place(soldier, (x + 8, y, z))
        start, deadline = self.grenade(fixture, run, torb, ana)
        old_body = torb.body
        old_hp = fixture.health(run, torb)
        self.assertEqual(old_hp.total, 205)
        self.assertEqual(fight.antiheal[old_body], [(start, deadline)])
        if lethal:
            old_hp.damage(old_hp.total - 5)
            fight._sync_body_health(torb, old_hp)
            for _ in range(170):
                self.assertEqual(fight.antiheal[old_body], [(start, deadline)])
                fixture.step(run, torb, ana, defense=1)
                if old_hp.dead:
                    break
            self.assertTrue(old_hp.dead)
            self.assertEqual(old_hp.killer, ana.body)
            self.assertLess(run.frame, deadline)
            self.assertNotIn(old_body, fight.antiheal)
            for _ in range(900):
                if torb.body != old_body:
                    break
                run.step({})
        else:
            self.assertTrue(match.switch_hero(torb, heroes()[hero_torbjorn.HERO]))
        self.assertNotEqual(torb.body, old_body)
        self.assertNotIn(old_body, fight.antiheal)
        self.assertNotIn(torb.body, fight.antiheal)
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 250)
        hp.damage(20)
        fight._sync_body_health(torb, hp)
        fixture.place(torb, (x, y, z))
        fixture.place(soldier, (x + 2.5, y, z))
        run.step({soldier: (0, 0, 16)})
        fixture.advance(run, torb, ana, 60)
        if not lethal:
            self.assertLess(run.frame, deadline)
        self.assertTrue(any(shot.player is soldier and shot.field_tick is not None
                            for shot in fight.projectiles.shots))
        self.assertAlmostEqual(hp.total, 247.32)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 12.32)
        self.assertNotIn(torb.body, fight.antiheal)

    def test_antiheal_does_not_consume_overload_armour_grant(self):
        fixture, run, torb, ana, _ = self.arena()
        _, end = self.grenade(fixture, run, torb, ana)
        hp = fixture.health(run, torb)
        before = hp.total
        fixture.step(run, torb, ana, fire=16)
        fixture.advance(run, torb, ana, 35)
        self.assertLess(run.frame, end)
        self.assertEqual(hp.total, before + 100)

    def test_rifle_wound_spends_full_damage_over_time_across_armour_pools(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                fixture, run, torb, ana, _ = self.arena()
                fight = run.match.combat
                hp = fixture.health(run, torb)
                fixture.step(run, torb, ana, fire=16)
                scope = 2 if scoped else 0
                fixture.advance(run, torb, ana, 28, defense=scope)
                self.assertEqual(hp.total, 350)
                for before, after in ((350, 280), (280, 210), (210, 140)):
                    fixture.step(run, torb, ana, defense=scope | 1)
                    for _ in range(26):
                        if hp.total < before - 1e-6:
                            break
                        fixture.step(run, torb, ana, defense=scope)
                    self.assertGreater(hp.total, after)
                    self.assertLess(hp.total, before)
                    wound, = fight.rifle_wounds.active
                    last_partial = wound.ends_ms // 16
                    wait = last_partial - run.frame
                    fixture.advance(run, torb, ana, wait, defense=scope)
                    self.assertAlmostEqual(hp.total, after + 70 * 8 / 600)
                    fixture.step(run, torb, ana, defense=scope)
                    self.assertFalse(fight.rifle_wounds.active)
                    self.assertAlmostEqual(hp.total, after)
                    fixture.advance(run, torb, ana, 44 - wait, defense=scope)
                    self.assertAlmostEqual(hp.total, after)
                    self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 350 - after, places=4)
                    self.assertAlmostEqual(hp.over_armour.amount, max(0, after - 250))
                    self.assertAlmostEqual(hp.now[combat.ARMOUR_POOL], min(50, max(0, after - 200)))
                    self.assertAlmostEqual(hp.now[combat.HEALTH_POOL], min(200, after))

    def test_grenade_spends_active_overload_then_antiheal_blocks_recovery(self):
        fixture, run, torb, ana, soldier = self.arena()
        fight = run.match.combat
        fixture.step(run, torb, ana, fire=16)
        fixture.advance(run, torb, ana, 25)
        hp = fixture.health(run, torb)
        source = hp.over_armour.source
        base = dict(hp.now)
        self.assertEqual(hp.total, 350)
        x, y, z = torb.mover.position
        fixture.place(soldier, (x + 8, y, z))
        start, end = self.grenade(fixture, run, torb, ana)
        self.assertEqual(hp.total, 305)
        self.assertEqual(hp.over_armour.amount, 55)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertEqual(fight.antiheal[torb.body], [(start, end)])

        for _ in range(165):
            fixture.step(run, torb, ana, defense=1)
            if hp.total < 250:
                break
        self.assertEqual(hp.over_armour.amount, 0)
        self.assertEqual(hp.over_armour.source, source)
        self.assertLess(hp.total, 250)
        fixture.advance(run, torb, ana, 12)
        self.assertFalse(fight.rifle_wounds.active)
        self.assertAlmostEqual(hp.total, 235)

        fixture.place(soldier, (x + 2.5, y, z))
        run.step({soldier: (0, 0, 16)})
        before = hp.total
        fixture.advance(run, torb, ana, end - run.frame - 1)
        self.assertTrue(any(shot.player is soldier and shot.field_tick is not None
                            for shot in fight.projectiles.shots))
        self.assertEqual(hp.total, before)
        self.assertEqual(soldier.stats.value(stats.HEALING), 0)
        fixture.step(run, torb, ana)
        self.assertNotIn(torb.body, fight.antiheal)
        self.assertAlmostEqual(hp.total - before, projectiles.HEAL_PER_SECOND * combat.TICK)
        self.assertEqual(hp.over_armour.amount, 0)

    def test_grenade_damages_turret_and_overload_without_blocking_hammer_repair(self):
        fixture, run, torb, ana, _ = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(ana, (x, y, z + 60))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, ana, 225)
        turret = fight.turrets[torb.entity]
        x, y, z = turret.actor.position
        fixture.place(torb, (x - 1.6, y, z))
        fixture.place(ana, (x, y, z + 6))
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        rifle_aim = aim(fight._eye(ana), (x, y + .6, z))
        run.step({ana: (*rifle_aim, 1)})
        fixture.advance(run, torb, ana, 58)
        self.assertLess(hp.total, 200)
        turret_before = hp.total
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, ana, 45)
        fixture.step(run, torb, ana, fire=16)
        fixture.advance(run, torb, ana, 25)
        torb_hp = fixture.health(run, torb)
        self.assertEqual(torb_hp.over_armour.amount, 100)
        ana_damage = ana.stats.value(stats.HERO_DAMAGE)
        start, end = self.grenade(fixture, run, torb, ana)
        self.assertEqual(turret_before - hp.total, 50)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE) - ana_damage, 95)
        self.assertEqual(torb_hp.total, 305)
        self.assertEqual(torb_hp.over_armour.amount, 55)
        self.assertEqual(fight.antiheal[torb.body], [(start, end)])
        self.assertNotIn(turret.actor.entity, fight.antiheal)
        fixture.place(ana, (x, y, z + 60))
        before = hp.total
        fixture.advance(run, torb, ana, 45)
        yaw, pitch = aim(fight._eye(torb), (x, y + .4, z))
        run.step({torb: (yaw, pitch, 1)})
        for _ in range(40):
            run.step({torb: (yaw, pitch, 0)})
        self.assertLess(run.frame, end)
        self.assertEqual(fight.antiheal[torb.body], [(start, end)])
        self.assertEqual(torb_hp.over_armour.amount, 55)
        self.assertEqual(hp.total, before + 50)

    def test_grenade_blocked_by_static_wall_applies_no_antiheal(self):
        fixture, run, torb, ana, _ = self.arena()
        x, y, z = torb.mover.position
        run.match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(x - 5, z + 2, x + 5, z + 2, y0=y, y1=y + 4)
        )
        fixture.step(run, torb, ana, defense=16)
        fixture.advance(run, torb, ana, 75)
        self.assertFalse(run.match.combat.antiheal)
        self.assertEqual(fixture.health(run, torb).total, 250)

    def test_grenade_damage_wakes_torbjorn_while_antiheal_remains(self):
        fixture, run, torb, ana, _ = self.arena()
        fight = run.match.combat
        sleep_end, floor = sleep_tests.TorbjornSleepTests().cast(fixture, run, torb, ana)
        fixture.advance(run, torb, ana, 55)
        start, end = self.grenade(fixture, run, torb, ana)
        wake, _ = fight.sleep[torb.body]
        self.assertEqual(wake, max(floor, start + 32))
        self.assertLess(wake, sleep_end)
        fixture.advance(run, torb, ana, wake - run.frame)
        self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(fight.antiheal[torb.body], [(start, end)])
        hp = fixture.health(run, ana)
        before = hp.total
        fixture.step(run, torb, ana, fire=1)
        fixture.advance(run, torb, ana, 30)
        self.assertLess(hp.total, before)
