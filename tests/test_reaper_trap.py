"""Actual Junkrat Steel Trap input and Reaper control boundaries."""

import unittest
from dataclasses import replace

import test_game_reaper as reaper_tests
from test_game_combat import aim

from ow174.game import combat, reaper
from ow174.game.commands import JUMP, Command
from ow174.game.content import find_hero
from ow174.game.script import nodes


class ReaperTrapTests(unittest.TestCase):
    def scene(self, *, names=("Reaper", "Target")):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(caster, find_hero("Junkrat")))
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x, y, z - 8)
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        pool = fight.vitals[owner.body]
        return run, owner, caster, pool

    def throw(self, run, caster):
        run.step({caster: (0, 0, 16)})
        fight = run.match.combat
        for _ in range(80):
            run.step({})
            if fight.traps.actors:
                trap, = fight.traps.actors.values()
                if trap.armed_at is not None and fight.now >= trap.armed_at:
                    return trap
        self.fail("actual Steel Trap never landed and armed")

    @staticmethod
    def place(owner, trap, *, offset=0):
        x, y, z = trap.position
        owner.mover.position[:] = (x + offset, y, z)
        owner.mover.body = None

    def test_actual_trap_roots_walk_jump_but_allows_aim_shell_and_reaping(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        trap = self.throw(run, caster)
        self.assertEqual((trap.damage, trap.duration, trap.arm_delay, trap.radius, trap.health),
                         (80, 3, 0.5, 1, 100))
        self.place(owner, trap)
        run.step({})
        self.assertEqual(pool.total, 170)
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        self.assertEqual(trap.release_tick - run.frame, 188)
        before = tuple(owner.mover.position)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
        enemy_before = enemy.total
        x, y, z = caster.mover.position
        yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
        for _ in range(18):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, forward=127, yaw=yaw, pitch=pitch, buttons=1 | JUMP)])
        self.assertEqual(tuple(owner.mover.position), before)
        self.assertEqual(owner.mover.yaw, yaw)
        self.assertLess(enemy.total, enemy_before)
        self.assertGreater(pool.total, 170)
        run.frame = trap.release_tick
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertNotIn(trap.entity, fight.traps.actors)

    def test_actual_wraith_rejects_capture_then_trap_reacquires(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        trap = self.throw(run, caster)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.place(owner, trap)
        for _ in range(30):
            run.step({})
        self.assertIsNone(trap.victim)
        self.assertEqual(pool.total, 250)
        for _ in range(230):
            run.step({})
            if trap.victim is not None:
                break
        self.assertEqual(trap.victim, owner.body)
        self.assertEqual(pool.total, 170)
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))

    def projected_barrier_scene(self, *, names=("Reaper", "Junkrat", "Zarya")):
        run, owner, junkrat, pool = self.scene(names=names)
        fight = run.match.combat
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, junkrat.team, zarya.team = 0, 1, 0
        trap = self.throw(run, junkrat)
        self.place(owner, trap, offset=3)
        x, y, z = owner.mover.position
        zarya.mover.position[:] = (x + 4, y, z)
        zarya.mover.body = None
        run.step({})
        heading = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*heading, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.place(owner, trap)
        return run, owner, pool, trap, bubble

    def test_actual_projected_barrier_prevents_trap_capture_until_expiry(self):
        run, owner, pool, trap, bubble = self.projected_barrier_scene()
        fight = run.match.combat
        protected_ticks = 0
        for _ in range(160):
            run.step({})
            if fight.zarya_bubbles.for_target(owner) is None:
                break
            protected_ticks += 1
            self.assertIsNone(trap.victim)
            self.assertEqual(pool.total, 250)
            self.assertEqual(bubble.health, 200)
        self.assertGreater(protected_ticks, 100)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertIsNone(trap.victim)
        run.step({})
        self.assertEqual(trap.victim, owner.body)
        self.assertEqual(pool.total, 170)
        self.assertEqual(trap.release_tick - run.frame, 188)
        for _ in range(10):
            run.step({})
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        self.assertEqual(pool.total, 170)

    def test_actual_helix_breaks_projected_barrier_before_trap_capture(self):
        run, owner, pool, trap, bubble = self.projected_barrier_scene(
            names=("Reaper", "Junkrat", "Zarya", "Soldier")
        )
        fight = run.match.combat
        soldier = run.players[3]
        self.assertTrue(run.match.switch_hero(soldier, find_hero("Soldier: 76")))
        soldier.team = 1
        x, y, z = owner.mover.position
        soldier.mover.position[:] = (x, y, z + 6)
        soldier.mover.body = None
        run.step({})
        heading = aim(fight._eye(soldier), (x, y + 1, z))
        for _ in range(60):
            run.step({soldier: (*heading, 1)})
            if bubble.health < 120:
                break
        self.assertGreater(bubble.health, 0)
        self.assertLess(bubble.health, 120)
        self.assertEqual(pool.total, 250)
        self.assertIsNone(trap.victim)
        run.step({soldier: (*heading, 2)})
        for _ in range(45):
            if fight.zarya_bubbles.for_target(owner) is None:
                break
            run.step({soldier: (*heading, 0)})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(pool.total, 250)
        self.assertIsNone(trap.victim)
        run.step({})
        self.assertEqual(trap.victim, owner.body)
        self.assertEqual(trap.release_tick - run.frame, 188)
        self.assertEqual(pool.total, 170)
        for _ in range(10):
            run.step({})
        self.assertEqual(pool.total, 170)

    def test_actual_fortify_selects_short_trap_root_before_reaper_exchange(self):
        run, owner, junkrat, own_pool = self.scene(names=("Reaper", "Junkrat", "Orisa"))
        fight = run.match.combat
        orisa = run.players[2]
        self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, junkrat.team, orisa.team = 0, 0, 1
        trap = self.throw(run, junkrat)
        self.place(orisa, trap, offset=3)
        run.step({orisa: (0, 0, 8)})
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        before = pool.total
        self.assertTrue(fight.traps.flag(orisa, 9161))
        self.assertAlmostEqual(trap.short_duration, 0.01)
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)

        self.place(orisa, trap)
        run.step({})
        self.assertEqual(trap.victim, orisa.body)
        self.assertAlmostEqual(before - pool.total, 43, places=4)
        self.assertTrue(fight.traps.rooted(orisa.body, run.frame))
        self.assertEqual(trap.release_tick, run.frame + 1)
        run.step({})
        self.assertFalse(fight.traps.rooted(orisa.body, run.frame))
        self.assertNotIn(trap.entity, fight.traps.actors)
        self.assertTrue(fight.traps.flag(orisa, 9161))
        self.assertAlmostEqual(before - pool.total, 43, places=4)

        self.place(owner, trap, offset=3)
        run.step({})
        after_trap = pool.total
        reaper_tests.ReaperTests().fire(run, owner, orisa)
        self.assertLess(pool.total, after_trap)
        reaper_tests.ReaperTests().fire(run, orisa, owner)
        for _ in range(16):
            run.step({})
        self.assertLess(own_pool.total, 250)

    def test_actual_shadow_lands_on_armed_trap_and_only_enemy_trap_captures(self):
        for hostile in (True, False):
            with self.subTest(hostile=hostile):
                run, owner, caster, pool = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, caster.team = 0, int(hostile)
                trap = self.throw(run, caster)
                self.assertIsNone(trap.victim)
                facing = aim(fight._eye(owner), trap.position)
                for button in (16, 0, 1):
                    run.step({owner: (*facing, button)})
                record = fight.reaper_shadows[owner.body]
                self.assertIsNotNone(record["cast"])
                for _ in range(115):
                    if record["moved"]:
                        break
                    run.step({})
                    self.assertIsNone(trap.victim)
                    self.assertEqual(pool.total, 250)
                self.assertTrue(record["moved"])
                self.assertAlmostEqual(owner.mover.position[0], trap.position[0], delta=0.12)
                self.assertAlmostEqual(owner.mover.position[2], trap.position[2], delta=0.12)
                self.assertIsNone(trap.victim)
                landed = run.frame
                for _ in range(5):
                    run.step({})
                    if trap.victim is not None:
                        break
                self.assertEqual(pool.total, 170 if hostile else 250)
                self.assertEqual(trap.victim, owner.body if hostile else None)
                self.assertEqual(fight.traps.rooted(owner.body, run.frame), hostile)
                if hostile:
                    self.assertGreater(run.frame, landed)
                    self.assertFalse(reaper.intangible(owner))
                    self.assertEqual(trap.release_tick - run.frame, 188)

    def test_actual_ally_destroys_trap_at_locked_shadow_destination(self):
        run, owner, caster, pool = self.scene(names=("Reaper", "Junkrat", "Ally"))
        fight = run.match.combat
        ally = run.players[2]
        self.assertTrue(run.match.switch_hero(ally, find_hero("Reaper")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = ally.team = 0
        caster.team = 1
        trap = self.throw(run, caster)
        self.place(ally, trap, offset=-2.2)
        for _ in range(25):
            run.step({})
        facing = aim(fight._eye(owner), trap.position)
        for button in (16, 0, 1):
            run.step({owner: (*facing, button)})
        record = fight.reaper_shadows[owner.body]
        self.assertIsNotNone(record["cast"])
        x, y, z = trap.position
        heading = aim(fight._eye(ally), (x, y + 0.25, z))
        for _ in range(60):
            run.step({ally: (*heading, 1)})
            if trap.entity not in fight.traps.actors:
                break
        self.assertNotIn(trap.entity, fight.traps.actors)
        self.assertFalse(record["moved"])
        for _ in range(110):
            run.step({})
        self.assertTrue(record["moved"])
        self.assertAlmostEqual(owner.mover.position[0], x, delta=0.12)
        self.assertAlmostEqual(owner.mover.position[2], z, delta=0.12)
        self.assertEqual(pool.total, 250)
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))

    def test_actual_shadow_cast_from_rooted_reaper_retains_trap_deadline(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        trap = self.throw(run, caster)
        self.place(owner, trap)
        run.step({})
        self.assertEqual(pool.total, 170)
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        deadline = trap.release_tick
        x, y, z = owner.mover.position
        facing = aim(fight._eye(owner), (x, y, z + 5))
        for button in (16, 0, 1):
            run.step({owner: (*facing, button)})
        record = fight.reaper_shadows[owner.body]
        self.assertIsNotNone(record["cast"])
        for _ in range(115):
            if record["moved"]:
                break
            run.step({})
        self.assertTrue(record["moved"])
        self.assertAlmostEqual(owner.mover.position[2], z + 5, delta=0.12)
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        self.assertEqual(trap.release_tick, deadline)
        self.assertEqual(pool.total, 170)
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertNotIn(trap.entity, fight.traps.actors)

    def test_actual_hook_refuses_trapped_reaper_then_connects_after_release(self):
        run, owner, caster, pool = self.scene(names=("Reaper", "Junkrat", "Roadhog"))
        fight = run.match.combat
        hog = run.players[2]
        self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, caster.team, hog.team = 0, 1, 1
        trap = self.throw(run, caster)
        self.place(owner, trap)
        x, y, z = owner.mover.position
        hog.mover.position[:] = (x, y, z + 6)
        hog.mover.body = None
        run.step({})
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        heading = aim(fight._eye(hog), (x, y + 1, z))
        run.step({hog: (*heading, 8)})
        launched = False
        for _ in range(55):
            run.step({hog: (*heading, 0)})
            launched |= hog.body in fight.hooks.flights
            self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertTrue(launched)
        self.assertEqual(pool.total, 170)
        self.assertEqual(trap.health, 100)
        for _ in range(520):
            run.step({})
        self.assertNotIn(trap.entity, fight.traps.actors)
        run.step({hog: (*heading, 8)})
        for _ in range(40):
            run.step({hog: (*heading, 0)})
        self.assertEqual(pool.total, 140)
        self.assertGreater(owner.mover.position[2], z + 1)

    def test_actual_trap_capture_severs_established_hook_pull(self):
        run, owner, caster, pool = self.scene(names=("Reaper", "Junkrat", "Roadhog"))
        fight = run.match.combat
        hog = run.players[2]
        self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, caster.team, hog.team = 0, 1, 1
        trap = self.throw(run, caster)
        x, y, z = trap.position
        owner.mover.position[:] = (x, y, z - 2.5)
        hog.mover.position[:] = (x, y, z + 6)
        owner.mover.body = hog.mover.body = None
        run.step({})
        heading = aim(fight._eye(hog), (x, y + 1, z - 2.5))
        run.step({hog: (*heading, 8)})
        for _ in range(55):
            run.step({hog: (*heading, 0)})
            if fight.hooks.controlled(owner.body):
                break
        self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertIsNone(trap.victim)
        self.assertEqual(pool.total, 220)
        for _ in range(30):
            run.step({})
            if trap.victim is not None:
                break
        self.assertEqual(trap.victim, owner.body)
        self.assertEqual(pool.total, 140)
        stopped = tuple(owner.mover.position)
        run.step({})
        self.assertFalse(fight.hooks.controlled(owner.body))
        for _ in range(35):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), stopped)
        self.assertEqual(pool.total, 140)
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        while run.frame < trap.release_tick:
            run.step({})
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertEqual(tuple(owner.mover.position), stopped)
        self.assertEqual(pool.total, 140)

    def trap_sleep_scene(self):
        run, owner, junkrat, pool = self.scene(names=("Reaper", "Junkrat", "Ana"))
        ana = run.players[2]
        self.assertTrue(run.match.switch_hero(ana, find_hero("Ana")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, junkrat.team, ana.team = 0, 1, 1
        trap = self.throw(run, junkrat)
        return run, owner, ana, pool, trap

    def sleep_dart(self, run, ana, owner):
        fight = run.match.combat
        x, y, z = owner.mover.position
        ana.mover.position[:] = (x, y, z + 6)
        ana.mover.body = None
        run.step({})
        heading = aim(fight._eye(ana), (x, y + 1, z))
        run.step({ana: (*heading, 8)})
        for _ in range(45):
            run.step({ana: (*heading, 0)})
            if owner.body in fight.ana_sleeps:
                return fight.ana_sleeps[owner.body]
        self.fail("actual Sleep Dart never applied sleep")

    def test_actual_trap_damage_wakes_sleeping_reaper_but_root_persists(self):
        run, owner, ana, pool, trap = self.trap_sleep_scene()
        fight = run.match.combat
        self.place(owner, trap, offset=3)
        sleep = self.sleep_dart(run, ana, owner)
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(pool.total, 245)
        self.assertEqual(sleep["end"] - sleep["start"], 313)
        self.assertIsNone(trap.victim)
        while run.frame < sleep["start"] + 32:
            run.step({})
        self.place(owner, trap)
        run.step({})
        self.assertEqual(pool.total, 165)
        self.assertEqual(trap.victim, owner.body)
        self.assertTrue(sleep["waking"])
        self.assertEqual(sleep["end"] - run.frame, 32)
        self.assertEqual(trap.release_tick - run.frame, 188)
        while run.frame < sleep["end"] - 1:
            run.step({})
        self.assertTrue(fight.is_stunned(owner))
        run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        stopped = tuple(owner.mover.position)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        owner.take_commands([Command(run.frame, forward=127)])
        self.assertEqual(tuple(owner.mover.position), stopped)
        while run.frame < trap.release_tick:
            run.step({})
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertNotIn(trap.entity, fight.traps.actors)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, forward=127)])
        self.assertNotEqual(tuple(owner.mover.position), stopped)
        self.assertEqual(pool.total, 165)

    def test_actual_sleep_dart_outlasts_preexisting_trap_root(self):
        run, owner, ana, pool, trap = self.trap_sleep_scene()
        fight = run.match.combat
        self.place(owner, trap)
        run.step({})
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        self.assertEqual(pool.total, 170)
        root_end = trap.release_tick

        sleep = self.sleep_dart(run, ana, owner)
        self.assertEqual(pool.total, 165)
        self.assertEqual(trap.release_tick, root_end)
        self.assertEqual(sleep["end"] - sleep["start"], 313)
        self.assertFalse(sleep["waking"])
        while run.frame < root_end:
            run.step({})
        self.assertNotIn(trap.entity, fight.traps.actors)
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertTrue(fight.is_stunned(owner))
        run.step({owner: (0, 0, 8)})
        self.assertFalse(reaper.intangible(owner))

        while run.frame < sleep["end"] - 1:
            run.step({})
        self.assertTrue(fight.is_stunned(owner))
        run.step({})
        self.assertNotIn(owner.body, fight.ana_sleeps)
        self.assertFalse(fight.is_stunned(owner))
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(pool.total, 165)

    def test_actual_trap_stops_swift_strike_before_it_hits_reaper(self):
        run, owner, junkrat, own = self.scene(names=("Reaper", "Junkrat", "Genji"))
        fight = run.match.combat
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, find_hero("Genji")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = junkrat.team = 0
        genji.team = 1
        trap = self.throw(run, junkrat)
        x, y, z = trap.position
        owner.mover.position[:] = (x, y, z + 4)
        genji.mover.position[:] = (x, y, z - 4)
        owner.mover.body = genji.mover.body = None
        run.step({})
        dash = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
        run.step({genji: (0, 0, 8)})
        self.assertTrue(dash.state(1).active)
        for _ in range(20):
            run.step({})
            if trap.victim is not None:
                break
        self.assertEqual(trap.victim, genji.body)
        self.assertGreater(genji.mover.position[2], z - 3)
        self.assertEqual(fight.vitals[genji.body].total, 120)
        stopped = tuple(genji.mover.position)
        while run.frame < trap.release_tick + 3:
            run.step({})
        self.assertFalse(dash.state(1).active)
        self.assertFalse(fight.traps.rooted(genji.body, run.frame))
        self.assertEqual(tuple(genji.mover.position), stopped)
        self.assertEqual(own.total, 250)
        reaper_tests.ReaperTests().fire(run, owner, genji)
        self.assertLess(fight.vitals[genji.body].total, 120)

    def test_actual_shotgun_destroys_trap_and_releases_root(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        trap = self.throw(run, caster)
        self.place(owner, trap, offset=0.9)
        run.step({})
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        x, y, z = trap.position
        yaw, pitch = aim(fight._eye(owner), (x, y + 0.25, z))
        for _ in range(70):
            run.step({owner: (yaw, pitch, 1)})
            if trap.entity not in fight.traps.actors:
                break
        self.assertEqual(trap.health, 0)
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertEqual(pool.total, 170)

    def test_actual_cooldown_refuses_repress_then_replaces_idle_trap(self):
        run, _, caster, _ = self.scene()
        fight = run.match.combat
        first = self.throw(run, caster)
        instance = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x09BA)
        ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
        self.assertEqual(instance.vars[61].base, 10)
        self.assertGreater(ability.remaining(), 9)
        run.step({caster: (0, 0, 16)})
        for _ in range(60):
            run.step({})
        self.assertIs(fight.traps.actors.get(first.entity), first)
        for _ in range(620):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        second = self.throw(run, caster)
        self.assertNotEqual(first.entity, second.entity)
        self.assertNotIn(first.entity, fight.traps.actors)

    def test_actual_trap_ignores_ally_then_lethal_capture_has_no_root(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = caster.team = 0
        trap = self.throw(run, caster)
        self.place(owner, trap)
        for _ in range(25):
            run.step({})
        self.assertEqual(pool.total, 250)
        self.assertIsNone(trap.victim)
        owner.team = 1
        pool.damage(180)
        run.step({})
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, caster.body)
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        self.assertNotIn(trap.entity, fight.traps.actors)

    def test_actual_lethal_shell_clears_trap_root_before_respawn(self):
        run, owner, junkrat, pool = self.scene(names=("Reaper", "Junkrat", "Attacker"))
        fight = run.match.combat
        attacker = run.players[2]
        self.assertTrue(run.match.switch_hero(attacker, find_hero("Reaper")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, junkrat.team, attacker.team = 0, 1, 1
        trap = self.throw(run, junkrat)
        self.place(owner, trap)
        x, y, z = owner.mover.position
        attacker.mover.position[:] = (x + 3, y, z)
        attacker.mover.body = None
        run.step({})
        old_body = owner.body
        self.assertEqual(pool.total, 170)
        self.assertTrue(fight.traps.rooted(old_body, run.frame))

        fire = reaper_tests.ReaperTests().fire
        fire(run, attacker, owner)
        self.assertEqual(pool.total, 44)
        for _ in range(40):
            run.step({})
        self.assertTrue(fight.traps.rooted(old_body, run.frame))
        fire(run, attacker, owner)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, attacker.body)
        self.assertLess(run.frame, trap.release_tick)
        self.assertFalse(fight.traps.rooted(old_body, run.frame))
        self.assertNotIn(trap.entity, fight.traps.actors)

        due, = (at for at, player in fight.respawns if player is owner)
        run.frame = int(due / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(owner.body, old_body)
        self.assertFalse(fight.is_dead(owner))
        self.assertFalse(fight.traps.rooted(owner.body, run.frame))
        target = next(t for t in fight._targets() if t.owner is owner)
        self.assertEqual(fight.vitals_of(target).total, 250)
        spawned = tuple(owner.mover.position)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, forward=127)])
        self.assertNotEqual(tuple(owner.mover.position), spawned)

    def test_actual_capture_does_not_interrupt_death_blossom(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        trap = self.throw(run, caster)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        channel = fight.reaper_blossoms[owner.body]
        self.assertFalse(channel["ended"])
        self.place(owner, trap)
        run.step({})
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        self.assertFalse(channel["ended"])
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
        before = enemy.total
        for _ in range(28):
            run.step({})
        self.assertLess(enemy.total, before)
        self.assertGreater(pool.total, 170)

    def test_actual_quick_melee_spends_trap_pool_without_reaping(self):
        run, owner, caster, pool = self.scene()
        fight = run.match.combat
        trap = self.throw(run, caster)
        self.place(owner, trap, offset=0.9)
        run.step({})
        self.assertTrue(fight.traps.rooted(owner.body, run.frame))
        x, y, z = trap.position
        yaw, pitch = aim(fight._eye(owner), (x, y + 0.25, z))
        run.step({owner: (yaw, pitch, 0x800)})
        for _ in range(22):
            run.step({})
        self.assertEqual(trap.health, 70)
        self.assertEqual(pool.total, 170)
