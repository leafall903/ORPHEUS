"""Actual Symmetra attacks and resource recovery across Winston's sphere."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import aim, in_world
from test_symmetra_combat import arena, health

from ow174.game import combat, projectile_tags, stats, symmetra, winston_barrier
from ow174.game.collision import World
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes


class SymmetraWinstonBarrierTests(unittest.TestCase):
    def scene(self, *, practice=False):
        run, owner, winston = arena(PRACTICE_RANGE) if practice else arena()
        self.assertTrue(run.match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        winston.mover.position[:] = (0, 1, 8)
        fight = run.match.combat
        service = fight.winston_barriers

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                x, y, z = winston.mover.position
                angles = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*angles, attack), winston: (0, 0, defense)})

        step(count=10)
        step(defense=16)
        self.assertNotIn(winston.body, service.actors)
        step(count=3)
        actor = service.actors[winston.body]
        self.assertEqual((actor.health, actor.radius), (700, 5))
        self.assertAlmostEqual(actor.expires - fight.now, 9)
        self.assertEqual(actor.centre, (0, 1.1, 8))
        return run, owner, winston, actor, step

    def test_outer_beam_orb_and_breaking_hit_spend_barrier_only(self):
        run, _, winston, actor, step = self.scene()
        before = health(run, winston).total
        step(1, count=5)
        self.assertAlmostEqual(actor.health, 700 - 5 * 60 * .016, places=4)
        step()
        step(2, count=90)
        step(count=25)
        self.assertAlmostEqual(actor.health, 580 - 5 * 60 * .016, places=4)
        self.assertEqual(health(run, winston).total, before)
        actor.health = .5  # pre-damaged fixture for the breaking sample
        step(1)
        self.assertEqual(actor.health, 0)
        self.assertEqual(health(run, winston).total, before)
        step(1)
        self.assertLess(health(run, winston).total, before)

    def test_orb_breaking_sphere_spares_inside_target_but_hits_outside_target(self):
        run, owner, winston, actor, _ = self.scene()
        outside = run.match.add_player(3, "Outside", 0x02E000000000006E, 0, False)
        in_world(run.match, outside)
        run.players.append(outside)
        self.assertTrue(run.match.switch_hero(outside, heroes()[0x02E000000000006E]))
        outside.mover.position[:] = (1, 1, 2.7)
        winston.mover.position[:] = (1, 1, 4.4)
        winston.mover.body = None
        fight = run.match.combat
        actor.health = 5
        before = health(run, winston).total
        outside_hp = health(run, outside).total
        angles = aim(fight._eye(owner), (0, 2, 8))
        for _ in range(90):
            run.step({owner: (*angles, 2)})
        for _ in range(25):
            run.step({owner: (*angles, 0)})
        self.assertNotIn(winston.body, fight.winston_barriers.actors)
        self.assertEqual(health(run, winston).total, before)
        self.assertLess(health(run, outside).total, outside_hp)

    def test_earlier_wall_inside_target_and_exit_crossing(self):
        run, owner, winston, actor, step = self.scene()
        before = health(run, winston).total
        wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                      (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            step(1, count=5)
        self.assertEqual(actor.health, 700)
        self.assertEqual(health(run, winston).total, before)
        owner.mover.position[:] = (0, 1, 5)
        owner.mover.body = None
        step(1, count=5)
        self.assertEqual(actor.health, 700)
        self.assertLess(health(run, winston).total, before)
        winston.mover.position[:] = (0, 1, 0)
        winston.mover.body = None
        before = health(run, winston).total
        step(1, count=5)
        self.assertLess(actor.health, 700)
        self.assertEqual(health(run, winston).total, before)

    def test_body_impact_spends_nearby_sphere_via_splash_only(self):
        run, owner, winston, actor, _ = self.scene()
        front = run.match.add_player(3, "Front", 0x02E000000000006E, 0, False)
        in_world(run.match, front)
        run.players.append(front)
        self.assertTrue(run.match.switch_hero(front, heroes()[0x02E000000000006E]))
        front.mover.position[:] = (0, 1, 2.5)
        before = health(run, front).total
        winston_hp = health(run, winston).total
        angles = aim(run.match.combat._eye(owner), (0, 2, 2.5))
        for _ in range(90):
            run.step({owner: (*angles, 2)})
        for _ in range(25):
            run.step({owner: (*angles, 0)})
        self.assertAlmostEqual(before - health(run, front).total, 120, places=4)
        self.assertGreater(actor.health, 580)
        self.assertLess(actor.health, 700)
        self.assertEqual(health(run, winston).total, winston_hp)

    def test_quick_melee_spends_sphere_and_body_when_both_overlap(self):
        run, owner, winston, actor, step = self.scene()
        owner.mover.position[:] = (0, 1, 2.3)
        winston.mover.position[:] = (0, 1, 4.2)
        owner.mover.body = winston.mover.body = None
        before = health(run, winston).total
        step(0x800)
        self.assertEqual(actor.health, 670)
        self.assertEqual(before - health(run, winston).total, 25)

    def test_explicit_allied_sphere_passes_beam_to_enemy_inside(self):
        run, owner, winston, actor, step = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = winston.team
        enemy = run.match.add_player(3, "Enemy", 0x02E000000000006E, 1 - owner.team, False)
        in_world(run.match, enemy)
        run.players.append(enemy)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E000000000006E]))
        enemy.mover.position[:] = (0, 1, 9)
        before = health(run, enemy).total
        step(1, count=5)
        self.assertEqual(actor.health, 700)
        self.assertLess(health(run, enemy).total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_actual_sentry_acquires_target_only_after_sphere_expires(self):
        run, owner, winston, actor, step = self.scene()
        fight = run.match.combat
        owner.mover.position[:] = (0, 1, 6)
        owner.mover.body = None
        wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                      (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
        before = health(run, winston).total
        with patch.object(fight, "_collision", return_value=wall):
            for _ in range(40):
                run.step({owner: (-32768, 0, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            step(count=50)
            self.assertIsNone(sentry.target)
            self.assertEqual(health(run, winston).total, before)
            while fight.now < actor.expires:
                step()
            step(count=5)
            self.assertEqual(sentry.target, winston.body)
            self.assertLess(health(run, winston).total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_flies_into_hostile_sphere_but_its_beam_cannot_exit(self):
        run, owner, winston, sphere, _ = self.scene()
        fight = run.match.combat
        winston.mover.position[:] = (1, 1, 1)
        winston.mover.body = None
        cover = World([(-5, 0, 5, 5, 0, 5, 5, 8, 5),
                       (-5, 0, 5, 5, 8, 5, -5, 8, 5)])
        before = health(run, winston).total
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
                if fight.sentries.sentries:
                    break
            [sentry] = fight.sentries.sentries
            self.assertIsNone(sentry.attached_at)
            distance, crossed = fight.winston_barriers.trace(
                sentry.bot, sentry.bot.position, sentry.direction, 5)
            self.assertIs(crossed, sphere)
            self.assertGreater(distance, 0)
            for _ in range(100):
                run.step({})
            self.assertIsNotNone(sentry.attached_at)
            self.assertAlmostEqual(sentry.bot.position[2], 4.99)
            self.assertIs(sentry.owner, owner)
            self.assertEqual(health(run, sentry.bot).total, 30)
            self.assertEqual(sphere.health, 700)
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
            self.assertEqual(health(run, winston).total, before)
            while fight.now < sphere.expires:
                run.step({})
            self.assertEqual(sentry.target, winston.body)
            self.assertIsNotNone(sentry.slow)
            self.assertLess(health(run, winston).total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_catch_up_respects_sphere_entry_and_exit_at_expiry(self):
        for side in ("entry", "exit", "inside"):
            with self.subTest(side=side):
                contiguous = self.sentry_damage_across_sphere_expiry(1, side)
                self.assertGreater(contiguous, 0)
                gaps = (16, 64) if side == "inside" else (16, 64, 80)
                for gap in gaps:
                    with self.subTest(gap=gap):
                        self.assertAlmostEqual(self.sentry_damage_across_sphere_expiry(gap, side),
                                               contiguous, places=4)

    def sentry_damage_across_sphere_expiry(self, gap, side):
        run, owner, winston, actor, _ = self.scene()
        fight = run.match.combat
        owner.mover.position[:] = (0, 1, 6)
        owner.mover.body = None
        wall_z = 2 if side == "entry" else 4
        cover = World([(-5, 0, wall_z, 5, 0, wall_z, 5, 8, wall_z),
                       (-5, 0, wall_z, 5, 8, wall_z, -5, 8, wall_z)])
        yaw = 0 if side == "exit" else -32768
        if side == "exit":
            owner.mover.position[:] = (0, 1, 1)
            winston.mover.position[:] = (0, 1, 2)
            winston.mover.body = None
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(40):
                run.step({owner: (yaw, 0, 8)})
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        final_tick = round(actor.expires / combat.TICK) + 4
        if side == "inside":
            # Keep this uninterrupted control out of sentry range until shortly
            # before the gap, so the target cannot die before the endpoint.
            winston.mover.position[:] = (20, 1, 8)
            winston.mover.body = None
        common_start = final_tick - 90 if side == "inside" else run.frame
        while run.frame < common_start:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        if side == "inside":
            winston.mover.position[:] = (0, 1, 8)
        while run.frame < final_tick - gap:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        if gap > 1:
            self.assertIn(winston.body, fight.winston_barriers.actors)
            self.assertEqual(sentry.target, winston.body if side == "inside" else None)
        run.frame += gap
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(winston.body, fight.winston_barriers.actors)
        self.assertEqual(sentry.target, winston.body)
        self.assertIsNotNone(sentry.slow)
        damage = owner.stats.value(stats.HERO_DAMAGE)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
        return damage

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_starts_on_midgap_orb_break_of_sphere(self):
        flight, contiguous = self.sentry_damage_across_sphere_break()
        self.assertGreaterEqual(flight, 5)
        self.assertAlmostEqual(contiguous, 2.24, places=4)
        self.assertEqual(self.sentry_damage_across_sphere_break(flight), (flight, contiguous))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_respects_sphere_cover_after_winston_switch_leave_or_death(self):
        for ending in ("switch", "leave", "death"):
            with self.subTest(ending=ending):
                contiguous = self.sentry_damage_after_sphere_owner_ends(1, ending)
                full_window = 16 * 40 * combat.TICK
                if ending == "death":
                    self.assertGreater(contiguous, 0)
                    self.assertLess(contiguous, full_window)
                else:
                    self.assertAlmostEqual(contiguous, full_window, places=4)
                self.assertAlmostEqual(self.sentry_damage_after_sphere_owner_ends(16, ending),
                                       contiguous, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_respects_same_hero_replacement_and_practice_respawn(self):
        for ending in ("replace", "respawn"):
            for step_size in (1, 16):
                with self.subTest(ending=ending, step_size=step_size):
                    self.assertAlmostEqual(self.sentry_damage_after_sphere_owner_ends(step_size, ending),
                                           16 * 40 * combat.TICK, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_starts_after_practice_respawn_inside_world_gap(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                self.assertAlmostEqual(
                    self.sentry_damage_after_sphere_owner_ends(
                        16, "respawn", respawn_tail=8, retry=retry,
                    ),
                    16 * 40 * combat.TICK, places=4,
                )

    def sentry_damage_after_sphere_owner_ends(self, step_size, ending, *, respawn_tail=0, retry=False):
        run, owner, winston, actor, step = self.scene(practice=ending == "respawn")
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, winston.team = 0, 1
        victim = run.match.add_player(3, "Protected Soldier", SOLDIER, 1, False)
        in_world(run.match, victim)
        run.players.append(victim)
        self.assertTrue(run.match.switch_hero(victim, heroes()[SOLDIER]))
        victim.team = winston.team
        victim.mover.position[:] = (0, 1, 8)
        victim.mover.body = None
        winston.mover.position[:] = (20, 1, 8)
        winston.mover.body = None
        winston.spawn = ((20, 1, 8), winston.spawn[1])
        owner.mover.position[:] = (0, 1, 6)
        owner.mover.body = None
        cover = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                       (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(40):
                run.step({owner: (-32768, 0, 8)})
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        vitals = health(run, victim)

        def world_step(ticks=1):
            run.frame += ticks
            run.match.update(run.frame * combat.TICK, run.frame)

        # Resolve the owner event before expiry, then cross the deadline.
        wait = round(4 / combat.TICK) if ending == "respawn" else (16 if ending == "death" else 8)
        while run.frame < round(actor.expires / combat.TICK) - wait:
            world_step()
        self.assertIn(actor.body, fight.winston_barriers.actors)
        self.assertIsNone(sentry.target)
        self.assertIsNone(sentry.slow)
        self.assertEqual(vitals.total, 200)
        damage_before = owner.stats.value(stats.HERO_DAMAGE)
        if ending == "leave":
            run.match.leave(winston)
            self.assertTrue(winston.gone)
        elif ending in ("switch", "replace"):
            hero = winston.hero if ending == "replace" else heroes()[SOLDIER]
            self.assertTrue(run.match.switch_hero(winston, hero))
            self.assertNotEqual(winston.body, actor.body)
            winston.mover.position[:] = (20, 1, 8)
            winston.mover.body = None
        else:
            # Kill Winston outside his sphere without damaging the shield.
            owner.mover.position[:] = (20, 1, 3)
            owner.mover.body = None
            winston_pools = health(run, winston)
            winston_pools.now[combat.HEALTH_POOL] = 5
            winston_pools.now[combat.ARMOUR_POOL] = 0
            for _ in range(12):
                step(1)
                if winston_pools.dead:
                    break
            self.assertTrue(winston_pools.dead)
            self.assertEqual(winston_pools.killer, owner.body)
            self.assertIs(fight.winston_barriers.actors.get(actor.body), actor)
            self.assertEqual(actor.health, 700)
            self.assertEqual(vitals.total, 200)
            damage_before = owner.stats.value(stats.HERO_DAMAGE)
            if ending == "respawn":
                [deadline] = [when for when, player in fight.respawns if player is winston]
                self.assertAlmostEqual(deadline - winston_pools.died_at, combat.PRACTICE_RESPAWN)
                self.assertLess(deadline, actor.expires)
                respawn_tick = math.ceil(deadline / combat.TICK)
                while run.frame < respawn_tick - step_size + respawn_tail:
                    world_step()
                self.assertTrue(winston_pools.dead)
                self.assertIs(fight.winston_barriers.actors.get(actor.body), actor)
                self.assertEqual(vitals.total, 200)
                if retry:
                    with patch.object(run.match, "_next_body_id", return_value=None):
                        world_step(step_size)
                    [retry_at] = [when for when, player in fight.respawns if player is winston]
                    self.assertAlmostEqual(retry_at - respawn_tick * combat.TICK, combat.RESPAWN_RETRY)
                    self.assertEqual(winston.body, actor.body)
                    self.assertIs(fight.winston_barriers.actors.get(actor.body), actor)
                    self.assertEqual(vitals.total, 200)
                    self.assertIsNone(sentry.target)
                world_step(step_size)
                self.assertNotEqual(winston.body, actor.body)
                self.assertFalse(fight.is_dead(winston))
                self.assertAlmostEqual(200 - vitals.total, respawn_tail * 40 * combat.TICK, places=4)
                self.assertEqual(sentry.target, victim.body if respawn_tail else None)
        if ending != "death":
            self.assertNotIn(actor.body, fight.winston_barriers.actors)
        remaining = 16 - respawn_tail
        while remaining:
            ticks = min(step_size, remaining)
            world_step(ticks)
            remaining -= ticks
        if ending == "respawn":
            self.assertLess(fight.now, actor.expires)
        else:
            self.assertGreater(fight.now, actor.expires)
        self.assertNotIn(actor.body, fight.winston_barriers.actors)
        self.assertEqual(sentry.target, victim.body)
        self.assertIsNotNone(sentry.slow)
        damage = owner.stats.value(stats.HERO_DAMAGE)
        self.assertAlmostEqual(200 - vitals.total, damage - damage_before, places=4)
        world_step(0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
        if ending in ("replace", "respawn"):
            winston.mover.position[:] = (0, 1, 8)
            winston.mover.body = None
            step(defense=16)
            step(count=4)
            fresh = fight.winston_barriers.actors[winston.body]
            self.assertIsNot(fresh, actor)
            self.assertEqual(fresh.health, 700)
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
        return damage - damage_before

    def sentry_damage_across_sphere_break(self, expected_flight=None):
        run, owner, winston, actor, step = self.scene()
        fight = run.match.combat
        owner.mover.position[:] = (0, 1, 6)
        owner.mover.body = None
        cover = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                       (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(40):
                run.step({owner: (-32768, 0, 8)})
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        owner.mover.position[:] = (0, 1, 0)
        owner.mover.body = None
        step(2, count=90)
        self.assertIsNone(sentry.target)
        self.assertIsNone(sentry.slow)
        before = health(run, winston).total
        actor.health = 5
        step()
        [orb] = fight.rockets
        launch = run.frame
        broken_at = []
        damage_sphere = fight.winston_barriers.damage

        def record_break(shield, amount, **kwargs):
            damage_sphere(shield, amount, **kwargs)
            if shield is actor and actor.health == 0:
                broken_at.append(fight.tick)

        def world_step(ticks=1):
            run.frame += ticks
            run.match.update(run.frame * combat.TICK, run.frame)

        with patch.object(fight.winston_barriers, "damage", side_effect=record_break):
            if expected_flight is None:
                while actor.health > 0 and run.frame - launch < 24:
                    world_step()
                for _ in range(4):
                    world_step()
            else:
                for _ in range(expected_flight - 4):
                    world_step()
                self.assertIn(orb, fight.rockets)
                self.assertEqual(actor.health, 5)
                self.assertEqual(health(run, winston).total, before)
                world_step(8)
        [break_tick] = broken_at
        self.assertNotIn(orb, fight.rockets)
        self.assertNotIn(actor.body, fight.winston_barriers.actors)
        self.assertEqual(sentry.target, winston.body)
        self.assertIsNotNone(sentry.slow)
        damage = owner.stats.value(stats.HERO_DAMAGE)
        self.assertAlmostEqual(before - health(run, winston).total, damage, places=4)
        return break_tick - launch, round(damage, 4)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires authored shield tags")
    def test_ammo_recovers_on_sphere_then_expires_and_cooldown_still_gates_recast(self):
        run, owner, winston, actor, step = self.scene()
        fight = run.match.combat
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == symmetra.WEAPON)
        ammo = weapon.find_var(0, 53)
        step(2, count=90)
        step(count=25)
        step(1, count=5)
        self.assertTrue(weapon.state(95).active)
        before = ammo.value()
        step(1, count=25)
        self.assertAlmostEqual(ammo.value() - before, 7 * 25 * .016, places=3)
        while fight.now < actor.expires:
            step()
        self.assertNotIn(winston.body, fight.winston_barriers.actors)
        step(defense=16)
        step(count=4)
        self.assertNotIn(winston.body, fight.winston_barriers.actors)
        while fight.now < actor.expires + 4.1:
            step()
        step(defense=16)
        step(count=4)
        self.assertIsNot(fight.winston_barriers.actors[winston.body], actor)
