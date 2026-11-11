"""Actual confirmation/deployment and combat with the reconstructed wall plane."""

import math
import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import test_symmetra_grasp as grasp_tests
import test_symmetra_static_placement as placement_tests
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_symmetra_combat import health
from test_symmetra_matrix import prepare_matrix_resources
from test_symmetra_turrets import wall as sentry_wall

from ow174.game import (
    barrage_motion,
    barrier_mesh,
    combat,
    deflection,
    matrix,
    npc,
    projectile_tags,
    reinhardt_barrier,
    sigma_grasp,
    stats,
    symmetra,
    winston_barrier,
)
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

PHARAH = 0x02E0000000000008
BARRAGE_ROCKET = 0x0400000000000052


class PharahBarrageMotionTests(unittest.TestCase):
    def scene(self):
        run = Run(KANEZAKA_DM, ("Pharah", "Enemy"))
        pharah, enemy = run.players
        self.assertTrue(run.match.switch_hero(pharah, heroes()[PHARAH]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        y = pharah.mover.position[1]
        pharah.mover.position[:] = (0, y, 0)
        enemy.mover.position[:] = (30, y, 8)
        for _ in range(30):
            run.step({})
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        return run, pharah

    def test_actual_barrage_matches_sampled_heading_and_first_two_chords(self):
        run, pharah = self.scene()
        fight = run.match.combat
        origin = fight._eye(pharah)
        run.step({pharah: (0, 0, 32)})
        [rocket] = fight.rockets
        self.assertEqual(len(rocket.motion), 2)
        for actual, expected in zip(rocket.direction, (-.038900685, .057840325, .997567654), strict=True):
            self.assertAlmostEqual(actual, expected, delta=2e-7)
        delayed = replace(rocket, last_tick=fight.tick)
        for expected in ((-.030768202, .153935834, .711630322), (-.058296697, .299675820, 1.423862185)):
            run.step({})
            for actual, start, offset in zip(rocket.position, origin, expected, strict=True):
                self.assertAlmostEqual(actual - start, offset, delta=2e-6)
        self.assertIn(rocket, fight.rockets)
        fight._fly(delayed)
        self.assertEqual(delayed.position, rocket.position)
        self.assertEqual((delayed.flown, delayed.left), (rocket.flown, rocket.left))

    def test_actual_barrage_hits_narrow_terrain_above_the_launch_ray(self):
        run, pharah = self.scene()
        fight = run.match.combat
        x, y, z = fight._eye(pharah)
        z += .355815161
        wall = World([
            (x-.02, y+.067, z, x+.02, y+.067, z, x+.02, y+.087, z),
            (x-.02, y+.067, z, x+.02, y+.087, z, x-.02, y+.087, z),
        ])
        with patch.object(fight, "_collision", return_value=wall):
            run.step({pharah: (0, 0, 32)})
            [rocket] = fight.rockets
            self.assertIsNone(fight.cover_ray(rocket.position, rocket.direction, .72))
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.flown, combat.TICK / 2)


@unittest.skipUnless(os.environ.get('ORPHEUS_EXTRA_GRAPH_BUNDLES'), 'requires explicit actor resource shard')
class PhotonBarrierTests(unittest.TestCase):
    def add_target(self, run, number, position):
        player = run.match.add_player(number, f"Target {number}", SOLDIER, 0, False)
        in_world(run.match, player)
        run.players.append(player)
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        self.move(player, position)
        return player

    def move(self, player, position):
        player.mover.position[:] = position
        if player.mover.body is not None:
            x, y, z = position
            player.mover.body.teleport((x, y + player.mover._lift(), z))

    def deploy(self, *, rotated=False, world_clock_offset=0):
        run, owner = placement_tests.StaticPlacementTests().scene()
        if world_clock_offset:
            original = run.match.combat.update
            clock = patch.object(run.match.combat, 'update', side_effect=lambda now, tick, frames=True:
                                 original(now + world_clock_offset, tick, frames))
            clock.start()
            self.addCleanup(clock.stop)
        enemy = run.players[1]
        enemy.mover.position[:] = (0, 0, 6)
        for _ in range(10):
            run.step({owner: (0, 8192, 32)})
        if rotated:
            for _ in range(5):
                run.step({owner: (0, 8192, 0)})
            for _ in range(5):
                run.step({owner: (0, 8192, 32)})
        run.step({owner: (0, 8192, 0)})
        run.step({owner: (0, 8192, 1)})
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(run.match.combat.barriers.walls, [])
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x156B)
        wait = instance.node_state(instance.graph.node(0))
        self.assertTrue(wait.active)
        while not run.match.combat.barriers.walls:
            self.assertLess(owner.body_script.component.now - wait.start, 400)
            run.step({})
        self.assertEqual(len(run.match.combat.barriers.walls), 1)
        return run, owner, enemy, run.match.combat.barriers.walls[0]

    def shoot(self, run, shooter, target, count, buttons=1):
        angles = aim(run.match.combat._eye(shooter),
                     (target.mover.position[0], target.mover.position[1] + 1, target.mover.position[2]))
        for _ in range(count):
            run.step({shooter: (*angles, buttons)})

    def test_live_barrier_suppresses_idle_and_damage_charge_until_removal(self):
        run, owner, enemy, wall = self.deploy()
        owner.body_script.ult_seconds = 60
        component = owner.body_script.component
        before = health(run, enemy).total
        self.shoot(run, owner, enemy, 90)
        self.assertLess(health(run, enemy).total, before)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        while run.match.combat.now < wall.expires_at:
            run.step({})
        self.assertFalse(run.match.combat.barriers.walls)
        for _ in range(65):
            run.step({})
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 5)

    def test_no_input_passive_charge_crosses_actual_wall_expiry_in_long_world_gap(self):
        for gap, offset in ((False, 0), (True, 0), (True, 1000)):
            with self.subTest(gap=gap, offset=offset):
                run, owner, _, wall = self.deploy(world_clock_offset=offset)
                script = owner.body_script
                script.ult_seconds = 60
                initial = script.frame
                expiry = math.ceil((wall.expires_at - offset) / combat.TICK - 1e-9)

                def update(tick, match=run.match):
                    match.update(tick * combat.TICK, tick)

                update(expiry - 1)
                self.assertIn(wall, run.match.combat.barriers.walls)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                if gap:
                    update(expiry + 126)
                else:
                    for tick in range(expiry, expiry + 127):
                        update(tick)
                        elapsed = (tick - expiry + 1) * script.frame_ms - 1
                        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(),
                                         5 * (elapsed // 1000))
                self.assertNotIn(wall, run.match.combat.barriers.walls)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 10)
                self.assertEqual(script.frame, initial)

    def test_actual_orb_charge_respects_photon_expiry_in_stepped_and_retained_updates(self):
        for impact_offset, clock_offset in ((-3, 0), (0, 0), (2, 0), (0, 1000)):
            with self.subTest(impact_offset=impact_offset, clock_offset=clock_offset):
                stepped = self.orb_charge_across_photon_expiry(impact_offset, clock_offset, gap=False)
                retained = self.orb_charge_across_photon_expiry(impact_offset, clock_offset, gap=True)
                self.assertEqual(retained, stepped)

    def orb_charge_across_photon_expiry(self, impact_offset, clock_offset, *, gap):
        run, owner, enemy, wall = self.deploy(world_clock_offset=clock_offset)
        fight = run.match.combat
        owner.body_script.ult_seconds = 60
        component = owner.body_script.component
        component.set(12918, 0)
        expiry_tick = math.ceil((wall.expires_at - clock_offset) / combat.TICK - 1e-9)
        release_tick = expiry_tick + impact_offset - 14
        while run.frame < release_tick - 91:
            run.step({})
        self.shoot(run, owner, enemy, 90, buttons=2)
        self.shoot(run, owner, enemy, 1, buttons=0)
        self.assertEqual(run.frame, release_tick)
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        run.step({})
        self.assertEqual(orb.last_tick, run.frame)
        self.assertIn(wall, fight.barriers.walls)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)

        final_frame = expiry_tick + 8
        if gap:
            run.frame = final_frame
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            while run.frame < final_frame:
                run.step({})
        pool = health(run, enemy)
        self.assertNotIn(orb, fight.rockets)
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertEqual(pool.total, 80)
        hit_tick = round((pool.last_damage_at - clock_offset) / combat.TICK)
        self.assertEqual(hit_tick - expiry_tick, impact_offset)
        credited = component.entity_var(ULT_CHARGE).value()
        self.assertEqual(credited, 120 if impact_offset >= 0 else 0)
        return hit_tick - expiry_tick, credited

    def test_sentry_only_credits_post_expiry_ticks_in_retained_update(self):
        self.sentry_charge_across_owner_wall_expiry(gap=False)
        self.sentry_charge_across_owner_wall_expiry(gap=True)

    def sentry_charge_across_owner_wall_expiry(self, *, gap):
        run, owner, enemy, wall = self.deploy()
        fight = run.match.combat
        owner.body_script.ult_seconds = 60
        component = owner.body_script.component
        component.set(12918, 0)
        self.move(enemy, (20, 0, 20))
        expiry_tick = math.ceil(wall.expires_at / combat.TICK - 1e-9)
        with patch.object(fight, "_collision", return_value=sentry_wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            run.frame = expiry_tick - 12
            run.match.update(run.frame * combat.TICK, run.frame)
            self.move(enemy, (2, 0, 1))
            run.step({})
            self.assertIsNotNone(sentry.slow)
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
            pool = health(run, enemy)
            before = pool.total
            start_frame = run.frame
            final_frame = expiry_tick + 8
            if gap:
                run.frame = final_frame
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                while run.frame < final_frame:
                    run.step({})
            self.assertNotIn(wall, fight.barriers.walls)
            self.assertAlmostEqual(before - pool.total, .64 * (final_frame - start_frame))
            self.assertAlmostEqual(component.entity_var(ULT_CHARGE).value(), .64 * 9, places=4)
            before = pool.total
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(pool.total, before)
            self.assertAlmostEqual(component.entity_var(ULT_CHARGE).value(), .64 * 9, places=4)

    def test_sentry_resumes_charge_on_actual_hostile_orb_break_tick(self):
        stepped = self.sentry_charge_across_owner_wall_break(gap=None)
        self.assertEqual(self.sentry_charge_across_owner_wall_break(gap=stepped + 4), stepped)

    def sentry_charge_across_owner_wall_break(self, *, gap):
        run, owner, attacker, wall = self.deploy()
        fight = run.match.combat
        owner.body_script.ult_seconds = 60
        component = owner.body_script.component
        component.set(12918, 0)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[owner.hero.guid]))
        self.move(attacker, (20, 0, 20))
        with patch.object(fight, "_collision", return_value=sentry_wall()):
            launch = aim(fight._eye(owner), (5, 1.6, 3))
            for _ in range(50):
                run.step({owner: (*launch, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            self.move(attacker, (5, 0, 2))
            aim_wall = aim(fight._eye(attacker), (0, 1.5, wall.bot.position[2]))
            for _ in range(90):
                run.step({attacker: (*aim_wall, 2)})
            self.assertIsNotNone(sentry.slow)
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
            wall_pool = fight.vitals[wall.bot.entity]
            self.assertEqual(wall_pool.total, 4000)
            wall_pool.now[combat.HEALTH_POOL] = 100  # isolate one actual breaking impact
            run.step({attacker: (*aim_wall, 0)})
            [orb] = fight.rockets
            run.step({})
            self.assertIs(orb.shooter, attacker)
            self.assertEqual(orb.last_tick, run.frame)
            self.assertIn(wall, fight.barriers.walls)
            pool = health(run, attacker)
            before = pool.total
            start_frame = run.frame
            break_ticks = []
            retire = fight.barriers._retire

            def observe(ended):
                if ended is wall:
                    break_ticks.append(fight.tick)
                retire(ended)

            with patch.object(fight.barriers, "_retire", side_effect=observe):
                if gap is None:
                    for _ in range(35):
                        run.step({})
                        if wall not in fight.barriers.walls:
                            break
                    for _ in range(4):
                        run.step({})
                else:
                    run.frame += gap
                    run.match.update(run.frame * combat.TICK, run.frame)
            self.assertNotIn(orb, fight.rockets)
            self.assertNotIn(wall, fight.barriers.walls)
            self.assertEqual(wall_pool.total, 0)
            [break_tick] = break_ticks
            self.assertAlmostEqual(before - pool.total, .64 * (run.frame - start_frame))
            self.assertAlmostEqual(component.entity_var(ULT_CHARGE).value(), .64 * 5, places=4)
            return break_tick - start_frame

    def test_actual_confirm_deploys_authored_health_point_and_normal(self):
        for rotated in (False, True):
            with self.subTest(rotated=rotated):
                run, owner, _enemy, wall = self.deploy(rotated=rotated)
                fight = run.match.combat
                self.assertEqual(wall.bot.body, 0x04000000000026B0)
                self.assertEqual(fight.vitals[wall.bot.entity].total, 4000)
                self.assertAlmostEqual(wall.bot.position[2], 1.3)
                self.assertEqual(wall.normal, (1, 0, 0) if rotated else (0, 0, 1))
                instance = next(i for i in owner.body_script.component.instances.values()
                                if i.graph.index == 0x156B)
                self.assertAlmostEqual(wall.expires_at - fight.now, 12)
                self.assertEqual(instance.find_var(0, 38).value().id, wall.bot.entity)
                self.assertFalse(any(t.owner is wall.bot for t in fight._targets()))

    def test_enemy_hitscan_damages_wall_and_protects_owner(self):
        run, owner, enemy, wall = self.deploy()
        before = health(run, owner).total
        self.shoot(run, enemy, owner, 60)
        self.assertLess(run.match.combat.vitals[wall.bot.entity].total, 4000)
        self.assertEqual(health(run, owner).total, before)

    def test_actor_lifetime_does_not_assume_vm_and_world_clock_origins_are_equal(self):
        run, owner, enemy, wall = self.deploy(world_clock_offset=1000)
        self.assertAlmostEqual(wall.expires_at - run.match.combat.now, 12)
        before = health(run, owner).total
        self.shoot(run, enemy, owner, 30)
        self.assertIn(wall, run.match.combat.barriers.walls)
        self.assertEqual(health(run, owner).total, before)

    def test_owner_beam_passes_through_own_wall(self):
        run, owner, enemy, wall = self.deploy()
        before = health(run, enemy).total
        self.shoot(run, owner, enemy, 90)
        self.assertLess(health(run, enemy).total, before)
        self.assertEqual(run.match.combat.vitals[wall.bot.entity].total, 4000)

    def test_explicit_team_ally_hits_an_enemy_through_the_wall(self):
        run, owner, ally, wall = self.deploy()
        # Explicit team-mode fixture: the Combat team policy is under test,
        # rather than the deathmatch resource's native match-rule selection.
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        target = npc.spawn(run.match, npc.Definition(SOLDIER, ally.hero.body, 'Enemy fixture', 200),
                           (0, 0, -4), team=1 - owner.team)
        before = run.match.combat.vitals[target.entity].total
        angles = aim(run.match.combat._eye(ally), (0, 1, -4))
        for _ in range(45):
            run.step({ally: (*angles, 1)})
        self.assertLess(run.match.combat.vitals[target.entity].total, before)
        self.assertEqual(run.match.combat.vitals[wall.bot.entity].total, 4000)

    def test_breaking_hit_is_absorbed_then_following_hits_reach_the_owner(self):
        run, owner, enemy, wall = self.deploy()
        fight = run.match.combat
        fight.vitals[wall.bot.entity].now[0] = 1
        before = health(run, owner).total
        for _ in range(20):
            self.shoot(run, enemy, owner, 1)
            if wall not in fight.barriers.walls:
                break
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertEqual(health(run, owner).total, before)
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x156B)
        self.assertEqual(instance.find_var(0, 38).value(), 0)  # Authored exit resets the dynamic to integer0.
        self.assertFalse(instance.node_state(instance.graph.node(17)).active)
        self.shoot(run, enemy, owner, 30)
        self.assertLess(health(run, owner).total, before)
        self.assertEqual(fight.barriers.walls, [])

    def test_nearer_static_wall_prevents_damage_to_the_barrier(self):
        run, owner, enemy, wall = self.deploy()
        floor = [(-50, 0, -50, -50, 0, 50, 50, 0, 50), (-50, 0, -50, 50, 0, 50, 50, 0, -50)]
        cover = [(-5, 0, 3, 5, 0, 3, 5, 5, 3), (-5, 0, 3, 5, 5, 3, -5, 5, 3)]
        collision = World(floor + cover)
        run.match.collision_world = collision
        for player in run.players:
            player.mover.use(world=collision)
        before = health(run, owner).total
        self.shoot(run, enemy, owner, 30)
        self.assertEqual(run.match.combat.vitals[wall.bot.entity].total, 4000)
        self.assertEqual(health(run, owner).total, before)

    def test_enemy_beam_hits_wall_once_per_command_instead_of_the_player(self):
        run, owner, enemy, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
        self.move(enemy, (0, 0, 6))
        enemy.body_script.ult_seconds = 0
        before = health(run, owner).total
        self.shoot(run, enemy, owner, 30)
        remaining = run.match.combat.vitals[wall.bot.entity].total
        self.assertLess(remaining, 4000)
        self.assertGreater(remaining, 4000 - 60 * 30 * .016 - .01)
        self.assertEqual(health(run, owner).total, before)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires authored shield tags")
    def test_enemy_beam_recovers_ammo_from_actual_wall_actor_and_release_stops_it(self):
        run, owner, enemy, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
        self.move(enemy, (0, 0, 6))
        instance = next(i for i in enemy.body_script.component.instances.values()
                        if i.graph.index == symmetra.WEAPON)
        ammo = instance.find_var(0, 53)
        self.shoot(run, enemy, owner, 90, buttons=2)
        self.shoot(run, enemy, owner, 30, buttons=0)
        self.shoot(run, enemy, owner, 5)
        self.assertTrue(instance.state(95).active)
        self.assertEqual(instance.find_var(0, 536).value().id, wall.bot.entity)
        before = ammo.value()
        self.shoot(run, enemy, owner, 25)
        self.assertAlmostEqual(ammo.value() - before, 25 * .016 * 7, places=3)
        self.shoot(run, enemy, owner, 3, buttons=0)
        self.assertFalse(instance.state(95).active)
        self.assertIsNone(enemy.body_script.world.beam_barrier)
        before = ammo.value()
        self.shoot(run, enemy, owner, 25, buttons=0)
        self.assertEqual(ammo.value(), before)

    def test_enemy_orb_impact_ends_the_projectile_and_damages_only_the_wall(self):
        run, owner, enemy, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
        self.move(enemy, (0, 0, 6))
        enemy.body_script.ult_seconds = 0
        before = health(run, owner).total
        self.shoot(run, enemy, owner, 90, buttons=2)
        self.shoot(run, enemy, owner, 1, buttons=0)
        for _ in range(30):
            run.step({})
        self.assertLess(run.match.combat.vitals[wall.bot.entity].total, 4000)
        self.assertEqual(health(run, owner).total, before)
        self.assertFalse(any(r.shooter is enemy for r in run.match.combat.rockets))

    def test_orb_breaking_wall_allows_approach_side_splash_but_no_spill(self):
        run, owner, enemy, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
        self.move(enemy, (0, 0, 6))
        front = self.add_target(run, 3, (1, 0, 1.9))
        back = self.add_target(run, 4, (1, 0, .8))
        front_hp = health(run, front).total
        back_hp = health(run, back).total
        fight = run.match.combat
        fight.vitals[wall.bot.entity].now[0] = 5
        self.shoot(run, enemy, owner, 90, buttons=2)
        self.shoot(run, enemy, owner, 25, buttons=0)
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertLess(health(run, front).total, front_hp)
        self.assertEqual(health(run, back).total, back_hp)

    def test_orb_body_impact_splash_cannot_cross_wall(self):
        for cover in ("none", "barrier", "static"):
            with self.subTest(cover=cover):
                run, owner, enemy, wall = self.deploy()
                self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
                self.move(enemy, (0, 0, 6))
                front = self.add_target(run, 3, (0, 0, 2.1))
                back = self.add_target(run, 4, (.7, 0, .65))
                fight = run.match.combat
                if cover == "none":
                    self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
                    self.assertNotIn(wall, fight.barriers.walls)
                elif cover == "barrier":
                    fight.vitals[wall.bot.entity].now[0] = 5
                before = health(run, back).total
                front_hp = health(run, front).total
                divider = World([(-5, 0, 1.7, 5, 0, 1.7, 5, 8, 1.7),
                                 (-5, 0, 1.7, 5, 8, 1.7, -5, 8, 1.7)])
                collision = patch.object(fight, "_collision", return_value=divider)
                with collision if cover == "static" else nullcontext():
                    self.shoot(run, enemy, front, 90, buttons=2)
                    self.shoot(run, enemy, front, 25, buttons=0)
                self.assertLess(health(run, front).total, front_hp)
                if cover == "none":
                    self.assertLess(health(run, back).total, before)
                else:
                    self.assertEqual(health(run, back).total, before)
                    if cover == "barrier":
                        self.assertNotIn(wall, fight.barriers.walls)
                    else:
                        self.assertEqual(fight.vitals[wall.bot.entity].total, 4000)

    def test_hostile_helix_spends_barrier_and_only_splashes_the_approach_side(self):
        for barrier_hp in (4000, 5):
            with self.subTest(barrier_hp=barrier_hp):
                run, owner, soldier, wall = self.deploy()
                front = self.add_target(run, 3, (1, 0, 1.9))
                back = self.add_target(run, 4, (1, 0, .7))
                fight = run.match.combat
                barrier_pools = fight.vitals[wall.bot.entity]
                barrier_pools.now[0] = barrier_hp
                front_pools, back_pools = health(run, front), health(run, back)
                front_before, back_before = front_pools.total, back_pools.total
                self.shoot(run, soldier, owner, 1, buttons=2)
                self.assertTrue(any(r.shooter is soldier for r in fight.rockets))
                for _ in range(20):
                    run.step({})
                self.assertFalse(any(r.shooter is soldier for r in fight.rockets))
                self.assertEqual(barrier_pools.total, max(0, barrier_hp - 120))
                self.assertEqual(wall in fight.barriers.walls, barrier_hp > 120)
                self.assertAlmostEqual(front_before - front_pools.total, 80)
                self.assertEqual(back_pools.total, back_before)

    def test_helix_body_impact_splash_respects_hostile_and_allied_photon_barriers(self):
        for allied, barrier_hp in ((False, 4000), (False, 5), (True, 4000)):
            with self.subTest(allied=allied, barrier_hp=barrier_hp):
                run, owner, soldier, wall = self.deploy()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                soldier.team = owner.team if allied else 1 - owner.team
                front = self.add_target(run, 3, (0, 0, 2.7))
                back = self.add_target(run, 4, (.7, 0, .7))
                front.team = back.team = 1 - soldier.team
                fight = run.match.combat
                barrier_pools = fight.vitals[wall.bot.entity]
                barrier_pools.now[0] = barrier_hp
                front_pools, back_pools = health(run, front), health(run, back)
                front_before, back_before = front_pools.total, back_pools.total
                self.shoot(run, soldier, front, 1, buttons=2)
                self.assertTrue(any(r.shooter is soldier for r in fight.rockets))
                for _ in range(20):
                    run.step({})
                self.assertFalse(any(r.shooter is soldier for r in fight.rockets))
                self.assertAlmostEqual(front_before - front_pools.total, 120)
                if allied:
                    self.assertLess(back_pools.total, back_before)
                    self.assertEqual(barrier_pools.total, 4000)
                else:
                    self.assertEqual(back_pools.total, back_before)
                    if barrier_hp == 5:
                        self.assertEqual(barrier_pools.total, 0)
                        self.assertNotIn(wall, fight.barriers.walls)
                    else:
                        self.assertGreater(barrier_pools.total, 3920)
                        self.assertLess(barrier_pools.total, 4000)

    def test_allied_helix_flies_through_photon_barrier(self):
        run, owner, soldier, wall = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        soldier.team = owner.team
        target = self.add_target(run, 3, (0, 0, -2))
        target.team = 1 - soldier.team
        fight = run.match.combat
        pools = health(run, target)
        before = pools.total
        self.shoot(run, soldier, target, 1, buttons=2)
        self.assertTrue(any(r.shooter is soldier for r in fight.rockets))
        for _ in range(20):
            run.step({})
        self.assertFalse(any(r.shooter is soldier for r in fight.rockets))
        self.assertAlmostEqual(before - pools.total, 120)
        self.assertEqual(fight.vitals[wall.bot.entity].total, 4000)

    def pharah_scene(self, *, allied=False):
        run, owner, pharah, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(pharah, heroes()[PHARAH]))
        self.move(pharah, (0, 0, 6))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = owner.team if allied else 1 - owner.team
        return run, owner, pharah, wall

    def launch_concussion(self, run, pharah, target):
        fight = run.match.combat
        for _ in range(12):
            self.shoot(run, pharah, target, 1, buttons=16)
            if fight.rockets:
                break
        [rocket] = fight.rockets
        self.assertEqual(rocket.definition, 0x040000000000010F)
        self.assertEqual(rocket.concussion, (20, 5, ((8, 1, 1),)))
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (0, 0))
        return rocket

    def test_actual_concussive_blast_pushes_symmetra_after_photon_wall_expires(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, owner, pharah, wall = self.pharah_scene()
                fight = run.match.combat
                wall_pool = fight.vitals[wall.bot.entity]
                if not protected:
                    run.frame = math.ceil(wall.expires_at / combat.TICK) + 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(wall in fight.barriers.walls, protected)
                for _ in range(75):
                    run.step({})
                pharah.body_script.ult_seconds = 0
                pharah.body_script.set_ult_charge(0)
                before = tuple(owner.mover.position)
                rocket = self.launch_concussion(run, pharah, owner)
                for _ in range(35):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual((health(run, owner).total, wall_pool.total), (225, 4000))
                self.assertEqual(pharah.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(pharah.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                if protected:
                    self.assertEqual(tuple(owner.mover.position), before)
                else:
                    self.assertLess(owner.mover.position[2], before[2] - 2)

    def test_actual_concussion_passes_allied_wall_and_pushes_enemy_and_shooter(self):
        run, owner, pharah, wall = self.pharah_scene(allied=True)
        fight = run.match.combat
        self.move(owner, (1.1, 0, 0))
        target = self.add_target(run, 3, (0, 0, 0))
        target.team = 1 - owner.team
        for _ in range(75):
            run.step({})
        ally_before = tuple(owner.mover.position)
        shot = self.launch_concussion(run, pharah, target)
        for _ in range(35):
            run.step({})
        self.assertNotIn(shot, fight.rockets)
        self.assertEqual(tuple(owner.mover.position), ally_before)
        self.assertLess(target.mover.position[2], -2)
        self.assertGreater(pharah.mover.position[2], 8)
        self.assertEqual((health(run, target).total, health(run, pharah).total), (200, 200))
        self.assertEqual(fight.vitals[wall.bot.entity].total, 4000)

    def test_actual_concussion_is_consumed_by_matrix_and_grasp_without_impulse(self):
        prepare_matrix_resources(self)
        for mech in (True, False):
            with self.subTest(mech=mech):
                run, owner, pharah, wall, defender = self.pharah_absorb_scene(mech=mech, wall_first=False)
                fight = run.match.combat
                button = 2 if mech else 8
                for _ in range(5):
                    run.step({defender: (0, 0, button)})
                state = matrix.active(defender) if mech else sigma_grasp.active(defender)
                self.assertIsNotNone(state)
                before = tuple(defender.mover.position), tuple(owner.mover.position)
                shot = self.launch_concussion(run, pharah, owner)
                for _ in range(30):
                    run.step({defender: (0, 0, button)})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertLess(shot.flown * shot.speed, 8)
                self.assertEqual((tuple(defender.mover.position), tuple(owner.mover.position)), before)
                self.assertEqual(fight.vitals[wall.bot.entity].total, 4000)
                if not mech:
                    self.assertEqual(state.instance.find_var(0, 21312).value() or 0, 0)

    def test_hostile_pharah_rocket_splash_stays_on_approach_side_on_impact_and_break(self):
        for barrier_hp in (4000, 100):
            with self.subTest(barrier_hp=barrier_hp):
                run, owner, pharah, wall = self.pharah_scene()
                fight = run.match.combat
                front = self.add_target(run, 3, (1, 0, 1.9))
                back = self.add_target(run, 4, (1, 0, .7))
                front.team = back.team = owner.team
                front_pool, back_pool, owner_pool = (health(run, target) for target in (front, back, owner))
                wall_pool = fight.vitals[wall.bot.entity]
                wall_pool.now[combat.HEALTH_POOL] = barrier_hp

                self.shoot(run, pharah, owner, 1)
                [rocket] = fight.rockets
                self.assertIs(rocket.shooter, pharah)
                self.assertEqual((rocket.speed, rocket.direct.amount, rocket.blast.amount), (35, 40, 80))
                for _ in range(18):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(wall_pool.total, max(0, barrier_hp - 120))
                self.assertEqual(wall in fight.barriers.walls, barrier_hp > 120)
                self.assertLess(front_pool.total, 200)
                self.assertEqual((back_pool.total, owner_pool.total), (200, 225))

    def test_allied_pharah_rocket_hits_enemy_behind_photon_barrier(self):
        run, owner, pharah, wall = self.pharah_scene(allied=True)
        fight = run.match.combat
        self.move(owner, (4, 0, 0))
        target = self.add_target(run, 3, (0, 0, 0))
        target.team = 1 - owner.team
        target_pool = health(run, target)
        self.shoot(run, pharah, target, 1)
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, pharah)
        for _ in range(18):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual((target_pool.total, fight.vitals[wall.bot.entity].total), (80, 4000))

    def test_actual_pharah_primary_resolves_deflect_and_photon_barrier_in_both_directions(self):
        for order in ("wall_first", "deflect_first", "wall_on_return"):
            with self.subTest(order=order):
                return_wall = order == "wall_on_return"
                run, owner, pharah, wall = self.pharah_scene(allied=return_wall)
                fight = run.match.combat
                position = (0, 0, 3 if order == "deflect_first" else -1)
                genji = self.add_target(run, 3, position)
                self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
                genji.team = 1 - owner.team if return_wall else owner.team
                self.move(genji, position)
                self.move(owner, (4, 0, 0))
                run.step({genji: (0, 0, 16)})
                target = next(t for t in fight._targets() if t.owner is genji)
                self.assertIsNotNone(deflection.state_of(target))
                angles = aim(fight._eye(pharah), (0, 1.25, -2))
                run.step({pharah: (*angles, 1)})
                [rocket] = fight.rockets
                self.assertTrue(rocket.direct.deflectable)
                for _ in range(30):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                self.assertIs(rocket.shooter, pharah if order == "wall_first" else genji)
                expected_wall = 4000 if order == "deflect_first" else 3880
                self.assertEqual(fight.vitals[wall.bot.entity].total, expected_wall)
                self.assertEqual((health(run, owner).total, health(run, genji).total), (225, 200))
                returned = 120 if order == "deflect_first" else 0
                self.assertEqual(health(run, pharah).total, 200 - returned)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned)

    def test_actual_pharah_rocket_respects_photon_expiry_in_retained_flight(self):
        for crossing in (-1, 0, 1):
            with self.subTest(crossing=crossing):
                stepped = self.pharah_rocket_at_expiry(crossing, gap=False)
                self.assertEqual(self.pharah_rocket_at_expiry(crossing, gap=True), stepped)

    def pharah_rocket_at_expiry(self, crossing, *, gap):
        run, owner, pharah, wall = self.pharah_scene()
        fight = run.match.combat
        origin = fight._eye(pharah)
        direction = combat.aim(*aim(origin, (0, 1, 0)))
        distance, hit = fight.barriers.intersection(pharah, origin, direction, 30)
        self.assertIs(hit, wall)
        flight_frames = math.ceil(distance / (35 * combat.TICK))
        expiry = math.ceil(wall.expires_at / combat.TICK - 1e-9)
        run.frame = expiry + crossing - flight_frames - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.shoot(run, pharah, owner, 1)
        [rocket] = fight.rockets
        run.step({})
        self.assertEqual(rocket.last_tick, run.frame)
        wall_pool, owner_pool = fight.vitals[wall.bot.entity], health(run, owner)
        endpoint = expiry + 8
        if gap:
            run.frame = endpoint
            run.match.update(endpoint * combat.TICK, endpoint)
        else:
            while run.frame < endpoint:
                run.step({})
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertNotIn(rocket, fight.rockets)
        result = wall_pool.total, owner_pool.total
        self.assertEqual(result, (3880, 225) if crossing < 0 else (4000, 105))
        return result

    def test_actual_symmetra_orbs_kill_pharah_during_two_way_fire_through_own_wall(self):
        run, owner, pharah, wall = self.pharah_scene()
        fight = run.match.combat
        owner_pool, pharah_pool = health(run, owner), health(run, pharah)
        wall_pool = fight.vitals[wall.bot.entity]
        for volley, target_hp in enumerate((80, 0), 1):
            for _ in range(60):
                run.step({})
            self.shoot(run, owner, pharah, 90, buttons=2)
            x, y, z = pharah.mover.position
            orb_aim = aim(fight._eye(owner), (x, y + 1, z))
            x, y, z = owner.mover.position
            rocket_aim = aim(fight._eye(pharah), (x, y + 1, z))
            run.step({owner: (*orb_aim, 0), pharah: (*rocket_aim, 1)})
            self.assertEqual({rocket.shooter for rocket in fight.rockets}, {owner, pharah})
            orb = next(rocket for rocket in fight.rockets if rocket.shooter is owner)
            self.assertEqual(orb.direct.amount + orb.blast.amount, 120)
            for _ in range(28):
                run.step({})
                if not fight.rockets:
                    break
            self.assertFalse(fight.rockets)
            self.assertEqual((pharah_pool.total, owner_pool.total), (target_hp, 225))
            self.assertEqual(wall_pool.total, 4000 - volley * 120)
        self.assertTrue(fight.is_dead(pharah))
        self.assertEqual(pharah_pool.killer, owner.body)

    def test_pharah_barrage_first_actual_rocket_breaks_wall_without_spill(self):
        run, owner, pharah, wall = self.pharah_scene()
        fight = run.match.combat
        front = self.add_target(run, 3, (.6, 0, 1.8))
        front.team = owner.team
        wall_pool = fight.vitals[wall.bot.entity]
        front_pool, owner_pool = health(run, front), health(run, owner)
        wall_pool.now[combat.HEALTH_POOL] = 30
        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.shoot(run, pharah, owner, 1, buttons=32)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(20):
            if fight.rockets:
                break
            run.step({})
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, pharah)
        self.assertEqual((rocket.speed, rocket.direct.amount, rocket.blast.amount), (45, 10, 30))
        for _ in range(18):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertEqual(wall_pool.total, 0)
        self.assertLess(front_pool.total, 200)
        self.assertEqual(owner_pool.total, 225)

    def test_actual_curved_barrage_respects_photon_expiry_in_retained_flight(self):
        for crossing in (-1, 0, 1):
            with self.subTest(crossing=crossing):
                stepped = self.curved_barrage_at_expiry(crossing, gap=False)
                self.assertEqual(self.curved_barrage_at_expiry(crossing, gap=True), stepped)

    def curved_barrage_at_expiry(self, crossing, *, gap):
        run, owner, pharah, wall = self.pharah_scene()
        fight = run.match.combat
        expiry = math.ceil(wall.expires_at / combat.TICK - 1e-9)
        run.frame = expiry + crossing - 8
        run.match.update(run.frame * combat.TICK, run.frame)
        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.shoot(run, pharah, owner, 1, buttons=32)
        [rocket] = fight.rockets
        self.assertEqual(len(rocket.motion), 2)
        elapsed, hit_wall = self.first_pharah_defense_contact(fight, rocket, mech=True)
        self.assertTrue(hit_wall)
        self.assertEqual(run.frame + math.ceil(elapsed / combat.TICK), expiry + crossing)
        wall_pool, owner_pool = fight.vitals[wall.bot.entity], health(run, owner)
        first = run.frame + 1
        endpoint = expiry + 8
        landed = []
        end = fight._landed

        def observe(projectile):
            self.assertIs(projectile, rocket)
            landed.append(fight.tick)
            end(projectile)

        with patch.object(fight, "_landed", side_effect=observe):
            run.match.update(first * combat.TICK, first)
            self.assertEqual(rocket.last_tick, first)
            for tick in (endpoint,) if gap else range(first + 1, endpoint + 1):
                run.match.update(tick * combat.TICK, tick)
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertNotIn(rocket, fight.rockets)
        [impact] = landed
        result = impact - expiry, wall_pool.total, owner_pool.total
        self.assertEqual(result, (-1, 3960, 225) if crossing < 0 else (crossing + 1, 4000, 185))
        return result

    def test_pharah_sustained_barrage_spends_photon_barrier_without_hitting_owner(self):
        run, owner, pharah, wall = self.pharah_scene()
        fight = run.match.combat
        wall_pool = fight.vitals[wall.bot.entity]
        owner_pool = health(run, owner)
        landed = []
        end = fight._landed

        def observe(rocket):
            landed.append(rocket)
            end(rocket)

        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        with patch.object(fight, "_landed", side_effect=observe):
            run.step({pharah: (-32768, 0, 32)})
            for _ in range(220):
                run.step({pharah: (-32768, 0, 0)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(len(landed), 76)
        self.assertEqual(len({id(rocket) for rocket in landed}), 76)
        self.assertTrue(all(rocket.shooter is pharah and rocket.definition == BARRAGE_ROCKET
                            for rocket in landed))
        self.assertTrue(all(len(rocket.motion) == 2 for rocket in landed))
        self.assertFalse(fight.rockets)
        self.assertEqual((wall_pool.total, owner_pool.total), (960, 225))

    def test_actual_symmetra_beam_kills_pharah_and_ends_barrage_launches(self):
        run, owner, pharah, wall = self.pharah_scene()
        fight = run.match.combat
        wall_pool = fight.vitals[wall.bot.entity]
        owner_pool, pharah_pool = health(run, owner), health(run, pharah)
        for _ in range(75):
            run.step({})
        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        beam = aim(fight._eye(owner), (0, 1.2, 6))
        barrage = (-32768, 0)
        run.step({owner: (*beam, 1), pharah: (*barrage, 32)})
        launched = {}
        for _ in range(185):
            launched.update((id(rocket), rocket) for rocket in fight.rockets)
            if fight.is_dead(pharah):
                break
            run.step({owner: (*beam, 1), pharah: (*barrage, 0)})
        self.assertTrue(fight.is_dead(pharah))
        self.assertEqual((pharah_pool.total, pharah_pool.killer), (0, owner.body))
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 200)
        self.assertGreater(len(launched), 1)
        self.assertLess(len(launched), 76)
        self.assertTrue(all(rocket.shooter is pharah and rocket.definition == BARRAGE_ROCKET
                            for rocket in launched.values()))
        for _ in range(80):
            run.step({owner: (*beam, 1), pharah: (*barrage, 0)})
            self.assertTrue(all(id(rocket) in launched for rocket in fight.rockets))
        self.assertFalse(fight.rockets)
        self.assertEqual(wall_pool.total, 4000 - len(launched) * 40)
        self.assertEqual(owner_pool.total, 225)

    def pharah_absorb_scene(self, *, mech, wall_first):
        run, owner, pharah, wall = self.pharah_scene()
        defender = self.add_target(run, 3, (0, 0, 3))
        hero = replace(heroes()[matrix.DVA], body=matrix.MECH) if mech else heroes()[sigma_grasp.SIGMA]
        self.assertTrue(run.match.switch_hero(defender, hero))
        defender.team = owner.team
        z = (-12 if mech else -4) if wall_first else 3
        self.move(defender, (0, 0, z))
        self.move(pharah, (0, 0, 12))
        for _ in range(75):
            run.step({})
        return run, owner, pharah, wall, defender

    def test_allied_matrix_and_photon_barrier_arbitrate_actual_barrage(self):
        prepare_matrix_resources(self)
        for wall_first in (False, True):
            with self.subTest(wall_first=wall_first):
                self.check_pharah_interception(mech=True, wall_first=wall_first)

    def test_releasing_allied_matrix_exposes_photon_barrier_to_later_barrage_rounds(self):
        prepare_matrix_resources(self)
        run, owner, pharah, wall, mech = self.pharah_absorb_scene(mech=True, wall_first=False)
        self.move(mech, (0, 0, -1))
        fight = run.match.combat
        wall_pool = fight.vitals[wall.bot.entity]
        for _ in range(35):
            run.step({mech: (0, 0, 2)})
        self.assertIsNotNone(matrix.active(mech))
        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        angles = aim(fight._eye(pharah), (0, 1, 0))
        run.step({mech: (0, 0, 2), pharah: (*angles, 32)})
        [first] = fight.rockets
        self.assertEqual(first.definition, BARRAGE_ROCKET)
        for _ in range(25):
            run.step({mech: (0, 0, 2), pharah: (*angles, 0)})
            if first not in fight.rockets:
                break
        self.assertNotIn(first, fight.rockets)
        self.assertIsNotNone(matrix.active(mech))
        self.assertEqual(wall_pool.total, 4000)
        for _ in range(40):
            run.step({mech: (0, 0, 0), pharah: (*angles, 0)})
            if wall_pool.total < 4000:
                break
        self.assertIsNone(matrix.active(mech))
        self.assertEqual(wall_pool.total, 3960)
        self.assertEqual((health(run, mech).total, health(run, owner).total), (600, 225))

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires the external projectile catalog")
    def test_allied_grasp_and_photon_barrier_arbitrate_actual_barrage(self):
        for wall_first in (False, True):
            with self.subTest(wall_first=wall_first):
                self.check_pharah_interception(mech=False, wall_first=wall_first)

    def test_actual_pharah_primary_matrix_and_grasp_respect_nearer_photon_wall(self):
        prepare_matrix_resources(self)
        for mech in (False, True):
            for wall_first in (False, True):
                with self.subTest(mech=mech, wall_first=wall_first):
                    self.check_pharah_interception(mech=mech, wall_first=wall_first, barrage=False)

    def check_pharah_interception(self, *, mech, wall_first, barrage=True):
        run, owner, pharah, wall, defender = self.pharah_absorb_scene(mech=mech, wall_first=wall_first)
        fight = run.match.combat
        button = 2 if mech else 8
        for _ in range(5):
            run.step({defender: (0, 0, button)})
        state = matrix.active(defender) if mech else sigma_grasp.active(defender)
        self.assertIsNotNone(state)
        if barrage:
            script = pharah.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.shoot(run, pharah, owner, 1, buttons=32 if barrage else 1)
        [rocket] = fight.rockets
        self.assertEqual(rocket.definition, BARRAGE_ROCKET if barrage else None)
        self.assertEqual(rocket.implicit_absorbable, not barrage)
        self.assertEqual(projectile_tags.absorbable(rocket.definition), barrage)
        elapsed, hit_wall = self.first_pharah_defense_contact(fight, rocket, mech=mech)
        self.assertEqual(hit_wall, wall_first)
        wall_pool, defender_pool = fight.vitals[wall.bot.entity], health(run, defender)
        initial = defender_pool.total
        for _ in range(32):
            run.step({defender: (0, 0, button)})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.flown, elapsed)
        amount = 40 if barrage else 120
        self.assertEqual((wall_pool.total, defender_pool.total, health(run, owner).total),
                         (4000 - amount if wall_first else 4000, initial, 225))
        if not mech:
            self.assertEqual(state.instance.find_var(0, 21312).value() or 0, 0 if wall_first else amount)
            for _ in range(150):
                run.step({})
                if not sigma_grasp.active(defender):
                    break
            self.assertIsNone(sigma_grasp.active(defender))
            self.assertEqual(defender_pool.bonus_shields, 0 if wall_first else amount * .6)

    def first_pharah_defense_contact(self, fight, rocket, *, mech):
        """Trace fixed-geometry defenses from the independently sampled flight endpoints."""
        origin = rocket.position
        previous = origin
        for index in range(32):
            start = index * combat.TICK
            end = start + combat.TICK
            lateral = barrage_motion.displacement(rocket.motion, rocket.direction, 0, end)
            point = tuple(o + d * (rocket.speed * end) + x
                          for o, d, x in zip(origin, rocket.direction, lateral, strict=True))
            chord = tuple(p - q for p, q in zip(point, previous, strict=True))
            length = math.hypot(*chord)
            ray = previous, tuple(axis / length for axis in chord), length
            wall = fight.trace_barrier(rocket.shooter, *ray)
            incoming = rocket.shooter, rocket.definition, *ray
            if mech:
                contact = matrix.trace(fight, *incoming, implicit=rocket.implicit_absorbable)
            else:
                contact = fight.grasp.trace(*incoming, implicit=rocket.implicit_absorbable)
            if wall is not None and (contact is None or wall[0] < contact[0]):
                return start + combat.TICK * wall[0] / length, True
            if contact is not None:
                return start + combat.TICK * contact[0] / length, False
            previous = point
        self.fail("flight did not reach either defense")

    def test_actual_deflected_pharah_primary_retains_absorption_on_the_new_team(self):
        prepare_matrix_resources(self)
        for mech in (False, True):
            with self.subTest(mech=mech):
                self.check_deflected_pharah_absorption(mech)

    def check_deflected_pharah_absorption(self, mech):
        run, owner, pharah, wall, defender = self.pharah_absorb_scene(mech=mech, wall_first=False)
        fight = run.match.combat
        defender.team = pharah.team
        self.move(pharah, (0, 0, 6))
        self.move(defender, (0, 0, 7.2))
        genji = self.add_target(run, 4, (0, 0, 3))
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        genji.team = owner.team
        self.move(genji, (0, 0, 3))
        button = 2 if mech else 8
        held = {defender: (-32768, 0, button)}
        for _ in range(5):
            run.step(held)
        state = matrix.active(defender) if mech else sigma_grasp.active(defender)
        self.assertIsNotNone(state)
        run.step({**held, genji: (0, 0, 16)})
        self.shoot(run, pharah, owner, 1)
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, pharah)
        self.assertTrue(rocket.implicit_absorbable)
        for _ in range(30):
            run.step(held)
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.team, genji.team)
        self.assertEqual(health(run, pharah).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual((health(run, owner).total, fight.vitals[wall.bot.entity].total), (225, 4000))
        if not mech:
            self.assertEqual(state.instance.find_var(0, 21312).value(), 120)

    @unittest.skipIf(barrier_mesh.DESCRIPTOR_SHA256 is None, "requires the build 104319 shield mesh")
    def test_nearer_held_shield_protects_photon_barrier_for_same_blast(self):
        for raised in (False, True):
            with self.subTest(raised=raised):
                run, owner, enemy, wall = self.deploy()
                self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
                self.move(enemy, (0, 0, 6))
                front = self.add_target(run, 3, (0, 0, 2.7))
                rein = self.add_target(run, 4, (0, 0, 1))
                self.assertTrue(run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO]))
                self.move(rein, (0, 0, 1))
                fight = run.match.combat
                angles = aim(fight._eye(enemy), (0, 1, 2.7))
                for _ in range(20):
                    run.step({})
                for _ in range(12):
                    run.step({rein: (0, 0, 2 if raised else 0)})
                held = fight.reinhardt_barriers.states[rein.body]
                self.assertEqual(held.active, raised)
                if raised:
                    held.current = 5
                before = health(run, front).total
                for frame in range(115):
                    run.step({enemy: (*angles, 2 if frame < 90 else 0), rein: (0, 0, 2 if raised else 0)})
                self.assertAlmostEqual(before - health(run, front).total, 120, places=4)
                photon = fight.vitals[wall.bot.entity].total
                if raised:
                    self.assertEqual(held.current, 0)
                    self.assertEqual(photon, 4000)
                else:
                    self.assertLess(photon, 4000)

    def test_photon_and_winston_barriers_choose_nearer_layer_without_breaking_spill(self):
        for sphere_first in (False, True):
            for breaking in (False, True):
                with self.subTest(sphere_first=sphere_first, breaking=breaking):
                    self.check_photon_and_winston_layers(sphere_first, breaking)

    def photon_winston_layers(self, sphere_first, *, close=False):
        run, owner, attacker, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(attacker, owner.hero))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        attacker.team = 1 - owner.team
        wall_z = wall.bot.position[2]
        self.move(owner, (20, 0, wall_z + 4))
        self.move(attacker, (0, 0, wall_z - (3.8 if close else 4)))
        fight = run.match.combat
        # Put the sphere surface within orb splash reach of the wall.
        winston_z = wall_z + (4.2 if sphere_first else 5.6)
        if close:
            height = fight._eye(attacker)[1] - .1
            winston_z = wall_z + math.sqrt(25 - height ** 2) + (-.15 if sphere_first else .15)
        winston = self.add_target(run, 3, (0, 0, winston_z))
        self.assertTrue(run.match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        self.move(winston, (0, 0, winston_z))
        winston.team = owner.team
        attacker.body_script.component.set(12918, 0)
        for _ in range(20):
            run.step({})
        run.step({winston: (0, 0, 16)})
        for _ in range(4):
            run.step({})
        sphere = fight.winston_barriers.actors[winston.body]
        photon = fight.vitals[wall.bot.entity]
        self.assertEqual((photon.total, sphere.health), (4000, 700))
        origin, direction = fight._eye(attacker), (0, 0, 1)
        wall_contact, _ = fight.barriers.intersection(attacker, origin, direction, 12)
        sphere_contact, _ = fight.winston_barriers.trace(attacker, origin, direction, 12)
        self.assertEqual(sphere_contact < wall_contact, sphere_first)
        self.assertLess(abs(sphere_contact - wall_contact), 1)
        return run, owner, attacker, winston, wall, sphere

    def check_photon_and_winston_layers(self, sphere_first, breaking):
        run, owner, attacker, winston, wall, sphere = self.photon_winston_layers(sphere_first)
        fight = run.match.combat
        photon = fight.vitals[wall.bot.entity]
        defender_pools = [health(run, p).total for p in (winston, owner)]

        def step(buttons=0, count=1):
            for _ in range(count):
                run.step({attacker: (0, 0, buttons)})

        def pools():
            return (sphere.health, photon.total) if sphere_first else (photon.total, sphere.health)

        outer, inner = pools()
        step(1, 5)
        self.assertAlmostEqual(pools()[0], outer - 5 * .96)
        self.assertEqual(pools()[1], inner)
        step()
        if breaking:
            if sphere_first:
                sphere.health = 5
            else:
                fight.barriers.damage(wall, photon.total - 5)
        before_orb = pools()[0]
        step(2, 90)
        step()
        [orb] = fight.rockets
        self.assertIs(orb.shooter, attacker)
        step(count=35)
        self.assertNotIn(orb, fight.rockets)
        self.assertAlmostEqual(pools()[0], max(0, before_orb - 120))
        self.assertEqual(pools()[1], inner)
        self.assertEqual(wall in fight.barriers.walls, sphere_first or not breaking)
        self.assertEqual(winston.body in fight.winston_barriers.actors, not sphere_first or not breaking)
        if breaking:
            step(1, 5)
            self.assertAlmostEqual(pools()[1], inner - 5 * .96)
        self.assertEqual([health(run, p).total for p in (winston, owner)], defender_pools)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(attacker.client.messages(combat.HIT))
        self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_same_tick_orbs_recheck_photon_and_winston_layers_after_each_break(self):
        for sphere_first in (False, True):
            for breaks in (0, 1, 2):
                with self.subTest(sphere_first=sphere_first, breaks=breaks):
                    stepped = self.check_photon_and_winston_volley(sphere_first, breaks, clock="stepped")
                    for clock in ("retained", "unanchored", "mixed"):
                        with self.subTest(clock=clock):
                            self.assertEqual(self.check_photon_and_winston_volley(
                                sphere_first, breaks, clock=clock,
                            ), stepped)

    def check_photon_and_winston_volley(self, sphere_first, breaks, *, clock):
        run, owner, first, winston, wall, sphere = self.photon_winston_layers(sphere_first, close=True)
        fight = run.match.combat
        photon = fight.vitals[wall.bot.entity]
        second = self.add_target(run, 4, (.05, 0, first.mover.position[2]))
        self.assertTrue(run.match.switch_hero(second, first.hero))
        self.move(second, (.05, 0, first.mover.position[2]))
        second.team = first.team
        second.body_script.component.set(12918, 0)
        before = [health(run, defender).total for defender in (owner, winston)]

        def pools():
            return (sphere.health, photon.total) if sphere_first else (photon.total, sphere.health)

        if breaks >= (1 if sphere_first else 2):
            sphere.health = 5
        if breaks >= (2 if sphere_first else 1):
            fight.barriers.damage(wall, photon.total - 5)
        outer, inner = pools()
        for _ in range(90):
            run.step({first: (0, 0, 2), second: (0, 0, 2)})
        run.step({second: (0, 0, 2)} if clock == "mixed" else {})
        launch = run.frame
        if clock == "mixed":
            self.assertEqual([orb.shooter for orb in fight.rockets], [first])
            run.step({})
        self.assertEqual([orb.shooter for orb in fight.rockets], [first, second])
        first_orb, second_orb = fight.rockets
        self.assertIsNone(second_orb.last_tick)
        if clock in ("stepped", "retained"):
            run.step({})
            self.assertTrue(all(orb.last_tick == run.frame for orb in fight.rockets))
        elif clock == "mixed":
            self.assertEqual(first_orb.last_tick, run.frame)
        else:
            self.assertIsNone(first_orb.last_tick)
        new_orbs = [(orb, orb.position, orb.left) for orb in fight.rockets if orb.last_tick is None]
        impacts = []
        explode = fight._explode

        def observe(rocket, *args, **kwargs):
            explode(rocket, *args, **kwargs)
            impacts.append((fight.tick, rocket.shooter, pools()))

        first_reference = second_reference = launch
        after_near = (max(0, outer - 120), inner)
        with patch.object(fight, "_explode", side_effect=observe):
            if clock != "stepped":
                run.frame += 90
                gap_end = run.frame
                run.match.update(run.frame * combat.TICK, run.frame)
                if new_orbs:
                    self.assertEqual(fight.rockets, [orb for orb, _, _ in new_orbs])
                    for orb, position, lifetime in new_orbs:
                        self.assertEqual(orb.last_tick, gap_end)
                        self.assertAlmostEqual(orb.flown, combat.TICK)
                        self.assertAlmostEqual(lifetime - orb.left, combat.TICK)
                        self.assertAlmostEqual(math.dist(position, orb.position), orb.speed * combat.TICK)
                    self.assertEqual(len(impacts), int(clock == "mixed"))
                    self.assertEqual(pools(), after_near if clock == "mixed" else (outer, inner))
                    second_reference = gap_end - 1
                    if clock == "unanchored":
                        first_reference = second_reference
            for _ in range(25):
                if not fight.rockets:
                    break
                run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(len(impacts), 2)
        [(first_tick, first_source, after_first), (second_tick, second_source, after_second)] = impacts
        if clock == "mixed":
            self.assertLess(first_tick, gap_end - combat.COMBAT_CATCH_UP + 1)
            self.assertGreater(second_tick, gap_end)
        else:
            self.assertEqual(first_tick, second_tick)
            if clock == "retained":
                self.assertLess(first_tick, gap_end - combat.COMBAT_CATCH_UP + 1)
        self.assertEqual((first_source, second_source), (first, second))
        self.assertEqual(after_first, after_near)
        expected = (0, max(0, inner - 120)) if breaks else (outer - 240, inner)
        self.assertEqual(after_second, expected)
        self.assertEqual(wall in fight.barriers.walls, photon.total > 0)
        self.assertEqual(winston.body in fight.winston_barriers.actors, sphere.health > 0)
        self.assertEqual([health(run, defender).total for defender in (owner, winston)], before)
        for attacker in (first, second):
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(attacker.client.messages(combat.HIT))
            self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return first_tick - first_reference, second_tick - second_reference, after_first, after_second

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_orb_volley_breaks_photon_and_winston_before_grasp(self):
        for sphere_first in (False, True):
            for breaks in (0, 1, 2):
                with self.subTest(sphere_first=sphere_first, breaks=breaks):
                    stepped = self.check_orb_volley_through_grasp(sphere_first, breaks, gap=False)
                    self.assertEqual(self.check_orb_volley_through_grasp(sphere_first, breaks, gap=True),
                                     stepped)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_rear_flashbang_interrupts_or_spends_grasp_after_layered_orb_breaks(self):
        for sphere_first in (False, True):
            for launch in (118, 119, 120):
                with self.subTest(sphere_first=sphere_first, launch=launch):
                    args = {"flash_launch": launch}
                    stepped = self.check_orb_volley_through_grasp(sphere_first, 2, gap=False, **args)
                    self.assertEqual(self.check_orb_volley_through_grasp(sphere_first, 2, gap=True, **args),
                                     stepped)

    def check_orb_volley_through_grasp(self, sphere_first, breaks, *, gap, flash_launch=None):
        run, owner, first, winston, wall, sphere = self.photon_winston_layers(sphere_first, close=True)
        fight = run.match.combat
        photon = fight.vitals[wall.bot.entity]
        sigma_pos = (0, 0, wall.bot.position[2] + 4)
        sigma = self.add_target(run, 4, sigma_pos)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        self.move(sigma, sigma_pos)
        sigma.team = owner.team
        attackers = [first]
        for number, x in ((5, .05), (6, -.05)):
            position = (x, 0, first.mover.position[2])
            attacker = self.add_target(run, number, position)
            self.assertTrue(run.match.switch_hero(attacker, first.hero))
            self.move(attacker, position)
            attacker.team = first.team
            attacker.body_script.component.set(12918, 0)
            attackers.append(attacker)
        defenders = owner, winston, sigma
        before = [health(run, defender).total for defender in defenders]

        def pools():
            return (sphere.health, photon.total) if sphere_first else (photon.total, sphere.health)

        if breaks >= (1 if sphere_first else 2):
            sphere.health = 5
        if breaks >= (2 if sphere_first else 1):
            fight.barriers.damage(wall, photon.total - 5)
        outer, inner = pools()
        charging = dict.fromkeys(attackers, (0, 0, 2))
        for _ in range(90):
            run.step(charging)
        run.step(charging | {sigma: (-32768, 0, 8)})
        self.assertIsNotNone(sigma_grasp.active(sigma))
        run.step({sigma: (-32768, 0, 0)})
        orbs = tuple(fight.rockets)
        self.assertEqual([orb.shooter for orb in orbs], attackers)
        launch = run.frame
        for orb in orbs:
            self.assertTrue(projectile_tags.absorbable(orb.definition))
            ray = orb.position, orb.direction, 12
            wall_hit, _ = fight.barriers.intersection(orb.shooter, *ray)
            sphere_hit, _ = fight.winston_barriers.trace(orb.shooter, *ray)
            grasp_hit, _, _ = fight.grasp.trace(orb.shooter, orb.definition, *ray)
            self.assertGreater(grasp_hit, max(wall_hit, sphere_hit))
        run.step({sigma: (-32768, 0, 0)})
        self.assertTrue(all(orb.last_tick == run.frame for orb in orbs))

        impacts = []
        explode, absorb = fight._explode, fight.grasp.absorb

        def observe_impact(rocket, *args, **kwargs):
            explode(rocket, *args, **kwargs)
            impacts.append((fight.tick - launch, rocket.shooter, pools(), 0))

        def observe_absorb(state, shooter, *args):
            absorb(state, shooter, *args)
            total = state.instance.find_var(0, 21312).value()
            impacts.append((fight.tick - launch, shooter, pools(), total))

        with (
            patch.object(fight, "_explode", side_effect=observe_impact),
            patch.object(fight.grasp, "absorb", side_effect=observe_absorb),
            patch.object(fight, "_damage", wraps=fight._damage) as damage,
        ):
            if gap:
                run.frame += 90
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                for _ in range(25):
                    if not fight.rockets:
                        break
                    run.step({sigma: (-32768, 0, 0)})
        damage.assert_not_called()
        self.assertFalse(fight.rockets)
        self.assertEqual([shooter for _, shooter, _, _ in impacts], attackers)
        trace = [(tick, hp, amount) for tick, _, hp, amount in impacts]
        self.assertEqual(trace[0][0], trace[1][0])
        self.assertEqual(trace[0][1:], ((max(0, outer - 120), inner), 0))
        second = (0, max(0, inner - 120)) if breaks else (outer - 240, inner)
        self.assertEqual(trace[1][1:], (second, 0))
        third = ((outer - 360, inner), (0, inner - 240), (0, 0))[breaks]
        self.assertEqual(trace[2][1:], (third, 120 if breaks == 2 else 0))
        self.assertEqual([health(run, defender).total for defender in defenders], before)
        for attacker in attackers:
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(attacker.client.messages(combat.HIT))
            self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        if flash_launch is not None:
            self.assertNotIn(wall, fight.barriers.walls)
            self.assertNotIn(winston.body, fight.winston_barriers.actors)
            self.move(winston, (8, 0, winston.mover.position[2]))
            hit = self.rear_flashbang_after_layered_grasp(
                run, sigma, first.team, flash_launch, gap=gap,
            )
            return trace, hit

        for _ in range(150):
            if not sigma_grasp.active(sigma):
                break
            run.step({})
        self.assertIsNone(sigma_grasp.active(sigma))
        sigma_pool = health(run, sigma)
        bonus = 72 if breaks == 2 else 0
        self.assertEqual(sigma_pool.bonus_shields, bonus)
        self.assertEqual(sigma_pool.total, before[-1] + bonus)
        return trace

    def rear_flashbang_after_layered_grasp(self, run, sigma, hostile_team, launch, *, gap,
                                          absorbed=120, converted=72, caster_number=7):
        fight = run.match.combat
        state = sigma_grasp.active(sigma)
        self.assertIsNotNone(state)
        self.assertEqual(state.instance.find_var(0, 21312).value(), absorbed)
        x, y, z = sigma.mover.position
        position = (x, y, z + 3)
        caster = self.add_target(run, caster_number, position)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        self.move(caster, position)
        caster.team = hostile_team
        angles = aim(fight._eye(caster), (x, y + 1, z))

        def step(count=1):
            for _ in range(count):
                run.step({sigma: (-32768, 0, 0)})

        cast_frame = state.start // round(combat.TICK * 1000)
        pool = health(run, sigma)
        damage, play_script = fight._damage, fight.grasp.play_script
        events = []

        def observe_damage(shooter, target, *args, **kwargs):
            if shooter is caster and target.owner is sigma:
                events.append(("flashbang", fight.tick - cast_frame, pool.bonus_shields))
            return damage(shooter, target, *args, **kwargs)

        def observe_conversion(body, instance, node):
            handled = play_script(body, instance, node)
            if handled and body == sigma.body and node.pos == sigma_grasp.CONVERSION:
                events.append(("conversion", fight.tick - cast_frame, pool.bonus_shields))
            return handled

        hit = launch + 6
        before_expiry = hit < 125
        with (
            patch.object(fight, "_damage", side_effect=observe_damage),
            patch.object(fight.grasp, "play_script", side_effect=observe_conversion),
        ):
            result = grasp_tests.SymmetraGraspTests().check_rear_flashbang_expiry(
                run, sigma, step, state, caster, angles, launch,
                before_expiry=before_expiry, gap=gap, converted=converted,
            )
        # Contiguous commands run after their world tick. Catch-up advances the
        # missing body frame before the following tick's projectile impacts.
        conversion_tick = 125 if gap else 124
        expected = [("flashbang", hit, 0)] if before_expiry else [
            ("conversion", conversion_tick, converted), ("flashbang", hit, converted),
        ]
        self.assertEqual(events, expected)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 25)
        return result, events[-1]


    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_full_photon_and_winston_barriers_break_before_grasp(self):
        for sphere_first in (False, True):
            with self.subTest(sphere_first=sphere_first):
                flight_ticks, timeline = self.check_full_barriers_before_grasp(sphere_first)
                self.assertEqual(
                    self.check_full_barriers_before_grasp(sphere_first, replay_flight_ticks=flight_ticks),
                    (flight_ticks, timeline),
                )

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_flashbang_at_grasp_expiry_after_full_photon_and_winston_break(self):
        for sphere_first in (False, True):
            for launch in (118, 119):
                for gap in (False, True):
                    with self.subTest(sphere_first=sphere_first, hit=launch + 6, gap=gap):
                        self.check_full_barriers_before_grasp(sphere_first, flash_launch=launch, gap=gap)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_full_barrier_flight_replay_spans_retention_before_flashbang(self):
        for sphere_first in (False, True):
            with self.subTest(sphere_first=sphere_first):
                flight_ticks, timeline = self.check_full_barriers_before_grasp(sphere_first)
                # The final jump begins one tick after release and puts every
                # contact in that volley before the ordinary retained window.
                replay_ticks = [*flight_ticks[:-1], 91]
                self.assertLess(flight_ticks[-1], replay_ticks[-1] - combat.COMBAT_CATCH_UP + 1)
                self.assertEqual(
                    self.check_full_barriers_before_grasp(
                        sphere_first, flash_launch=119, gap=True, replay_flight_ticks=replay_ticks,
                    ),
                    (replay_ticks, timeline),
                )

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_mixed_final_volley_after_full_photon_and_winston_deploy(self):
        for sphere_first in (False, True):
            for flash_first in (False, True):
                with self.subTest(sphere_first=sphere_first, flash_first=flash_first):
                    flight_ticks, timeline = self.check_full_barriers_before_grasp(
                        sphere_first, mixed_flash_first=flash_first,
                    )
                    replay_ticks = [*flight_ticks[:-1], 91 if flash_first else 92]
                    self.assertLess(flight_ticks[-1], replay_ticks[-1] - combat.COMBAT_CATCH_UP + 1)
                    self.assertEqual(
                        self.check_full_barriers_before_grasp(
                            sphere_first, mixed_flash_first=flash_first, replay_flight_ticks=replay_ticks,
                        ),
                        (replay_ticks, timeline),
                    )

    def check_full_barriers_before_grasp(self, sphere_first, *, flash_launch=None, gap=False,
                                         replay_flight_ticks=None, mixed_flash_first=None):
        run, owner, first, winston, wall, sphere = self.photon_winston_layers(sphere_first, close=True)
        fight = run.match.combat
        photon = fight.vitals[wall.bot.entity]
        sigma_pos = (0, 0, wall.bot.position[2] + 4)
        sigma = self.add_target(run, 4, sigma_pos)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        self.move(sigma, sigma_pos)
        sigma.team = owner.team
        attackers = [first]
        for number in range(5, 13):
            position = ((number - 8.5) * .04, 0, first.mover.position[2])
            attacker = self.add_target(run, number, position)
            self.assertTrue(run.match.switch_hero(attacker, first.hero))
            self.move(attacker, position)
            attacker.team = first.team
            attacker.body_script.component.set(12918, 0)
            attackers.append(attacker)
        mixed_caster = None
        if mixed_flash_first is not None:
            flash_position = (-.05, 0, wall.bot.position[2] - (5 if mixed_flash_first else 4.05))
            mixed_caster = self.add_target(run, 13, flash_position)
            self.assertTrue(run.match.switch_hero(mixed_caster, heroes()[0x02E0000000000042]))
            self.move(mixed_caster, flash_position)
            mixed_caster.team = first.team
            flash_charge = mixed_caster.body_script.component.entity_var(ULT_CHARGE)
        defenders = owner, winston, sigma
        before = [health(run, player).total for player in defenders]
        self.assertEqual((photon.total, sphere.health), (4000, 700))
        contacts = []
        timeline = []
        flight_ticks = []
        explode, absorb = fight._explode, fight.grasp.absorb
        magazines = [
            next(i for i in attacker.body_script.component.instances.values() if i.graph.index == 0x157C)
            .find_var(0, 53)
            for attacker in attackers
        ]
        initial_ammo = [magazine.value() for magazine in magazines]

        def observe_impact(rocket, *args, **kwargs):
            shield, _ = kwargs["impact"]
            deadline = sphere.expires if shield is sphere else wall.expires_at
            self.assertLess(fight.now, deadline)
            if rocket.shooter is mixed_caster:
                before_impact = flash_charge.value()
            explode(rocket, *args, **kwargs)
            if rocket.shooter is mixed_caster:
                self.assertEqual(flash_charge.value(), before_impact)
            contacts.append((rocket.shooter, shield))
            timeline.append((fight.tick, photon.total, sphere.health, 0))

        def observe_absorb(state, shooter, *args):
            if shooter is mixed_caster:
                before_impact = flash_charge.value()
            absorb(state, shooter, *args)
            if shooter is mixed_caster:
                self.assertEqual(flash_charge.value(), before_impact)
            contacts.append((shooter, sigma))
            amount = state.instance.find_var(0, 21312).value()
            timeline.append((fight.tick, photon.total, sphere.health, amount))

        charging = dict.fromkeys(attackers, (0, 0, 2))
        idle_grasp = {sigma: (-32768, 0, 0)}
        with (
            patch.object(fight, "_explode", side_effect=observe_impact),
            patch.object(fight.grasp, "absorb", side_effect=observe_absorb),
            patch.object(fight, "_damage", wraps=fight._damage) as damage,
        ):
            for volley in range(5):
                mixed = volley == 4 and mixed_caster is not None
                flash = {mixed_caster: (0, 0, 16)} if mixed else {}
                for _ in range(90):
                    run.step(charging)
                if volley == 4:
                    run.step(charging | {sigma: (-32768, 0, 8)})
                    state = sigma_grasp.active(sigma)
                    self.assertIsNotNone(state)
                if mixed and mixed_flash_first:
                    run.step(charging | idle_grasp | flash)
                run.step(idle_grasp)
                launch = run.frame
                if mixed and not mixed_flash_first:
                    run.step(idle_grasp | flash)
                shots = [(p, symmetra.ORB_PROJECTILE) for p in attackers]
                if mixed:
                    thrown = [(mixed_caster, 0x040000000000037D)]
                    shots = thrown + shots if mixed_flash_first else shots + thrown
                self.assertEqual([(rocket.shooter, rocket.definition) for rocket in fight.rockets], shots)
                self.assertEqual([magazine.value() for magazine in magazines],
                                 [value - 10 * (volley + 1) for value in initial_ammo])
                run.step(idle_grasp)
                self.assertEqual(len(fight.rockets), len(shots))
                self.assertTrue(all(orb.last_tick == run.frame for orb in fight.rockets))
                if replay_flight_ticks is None:
                    for _ in range(24):
                        if not fight.rockets:
                            break
                        run.step(idle_grasp)
                else:
                    endpoint = launch + replay_flight_ticks[volley]
                    self.assertGreater(endpoint - run.frame, 1)
                    run.frame = endpoint
                    run.match.update(run.frame * combat.TICK, run.frame)
                flight_ticks.append(run.frame - launch)
                self.assertFalse(fight.rockets)
                self.assertEqual([shooter for shooter, _ in contacts[volley * 9:]
                                  if shooter is not mixed_caster], attackers)
        damage.assert_not_called()
        layers = [sphere] * 6 + [wall] * 34 if sphere_first else [wall] * 34 + [sphere] * 6
        self.assertEqual([target for shooter, target in contacts if shooter is not mixed_caster],
                         layers + [sigma] * 5)
        absorbed, converted = 600, 360
        if mixed_caster is not None:
            [(index, target)] = [(i, target) for i, (shooter, target) in enumerate(contacts)
                                 if shooter is mixed_caster]
            if mixed_flash_first:
                self.assertEqual((index, target), (36, layers[-1]))
                self.assertEqual(timeline[index][1:], (375, 0, 0) if sphere_first else (0, 435, 0))
            else:
                self.assertIs(target, sigma)
                self.assertGreaterEqual(index, len(layers))
                self.assertEqual(timeline[index][3] - timeline[index - 1][3], 25)
                absorbed, converted = 625, 375
            attackers.append(mixed_caster)
        self.assertEqual((photon.total, sphere.health), (0, 0))
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertNotIn(winston.body, fight.winston_barriers.actors)
        self.assertEqual(state.instance.find_var(0, 21312).value(), absorbed)
        self.assertEqual([health(run, player).total for player in defenders], before)
        self.assertFalse(fight.is_stunned(sigma))
        for attacker in attackers:
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(attacker.client.messages(combat.HIT))
            if attacker is not mixed_caster:
                self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if flash_launch is not None:
            self.move(winston, (8, 0, winston.mover.position[2]))
            self.rear_flashbang_after_layered_grasp(
                run, sigma, first.team, flash_launch, gap=gap,
                absorbed=600, converted=360, caster_number=13,
            )
            return flight_ticks, timeline
        for _ in range(150):
            if not sigma_grasp.active(sigma):
                break
            run.step({})
        self.assertIsNone(sigma_grasp.active(sigma))
        pool = health(run, sigma)
        self.assertEqual((pool.bonus_shields, pool.bonus_shields_max), (converted, converted))
        self.assertEqual(pool.total, before[-1] + converted)
        return flight_ticks, timeline


    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    def test_flashbang_and_orbs_across_photon_winston_and_grasp(self):
        for sphere_first in (False, True):
            for flash_first in (False, True):
                for outer_health in (5, 200, None):
                    with self.subTest(sphere_first=sphere_first, flash_first=flash_first,
                                      outer_health=outer_health):
                        args = sphere_first, flash_first, outer_health
                        stepped = self.check_mixed_orbs_through_grasp(*args, gap=False)
                        self.assertEqual(self.check_mixed_orbs_through_grasp(*args, gap=True), stepped)

    def check_mixed_orbs_through_grasp(self, sphere_first, flash_first, outer_health, *, gap):
        run, owner, first, winston, wall, sphere = self.photon_winston_layers(sphere_first, close=True)
        fight = run.match.combat
        photon = fight.vitals[wall.bot.entity]
        wall_z = wall.bot.position[2]
        sigma = self.add_target(run, 4, (0, 0, wall_z + 4))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        self.move(sigma, (0, 0, wall_z + 4))
        sigma.team = owner.team
        second = self.add_target(run, 5, (.05, 0, first.mover.position[2]))
        self.assertTrue(run.match.switch_hero(second, first.hero))
        self.move(second, (.05, 0, first.mover.position[2]))
        flash_position = (-.05, 0, wall_z - (5 if flash_first else 4.05))
        caster = self.add_target(run, 6, flash_position)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        self.move(caster, flash_position)
        attackers = first, second, caster
        for player in attackers:
            player.team = first.team
            player.body_script.component.set(12918, 0)
        defenders = owner, winston, sigma
        before = [health(run, player).total for player in defenders]

        def pools():
            return (sphere.health, photon.total) if sphere_first else (photon.total, sphere.health)

        if outer_health is None:
            self.assertEqual((photon.total, sphere.health), (4000, 700))
        else:
            sphere.health = outer_health if sphere_first else 5
            fight.barriers.damage(wall, photon.total - (5 if sphere_first else outer_health))
        outer, inner = pools()
        charging = dict.fromkeys((first, second), (0, 0, 2))
        idle_grasp = {sigma: (-32768, 0, 0)}
        flash = {caster: (0, 0, 16)}
        for _ in range(90):
            run.step(charging)
        run.step(charging | {sigma: (-32768, 0, 8)})
        self.assertIsNotNone(sigma_grasp.active(sigma))
        launch = run.frame
        if flash_first:
            run.step(charging | flash | idle_grasp)
            run.step(idle_grasp)
        else:
            run.step(idle_grasp)
            run.step(flash | idle_grasp)
        order = (caster, first, second) if flash_first else attackers
        self.assertEqual([rocket.shooter for rocket in fight.rockets], list(order))
        self.assertEqual(
            {rocket.shooter: rocket.definition for rocket in fight.rockets},
            {first: symmetra.ORB_PROJECTILE, second: symmetra.ORB_PROJECTILE, caster: 0x040000000000037D},
        )
        run.step(idle_grasp)
        self.assertTrue(all(rocket.last_tick == run.frame for rocket in fight.rockets))
        impacts = []
        explode, absorb = fight._explode, fight.grasp.absorb

        def observe_impact(rocket, *args, **kwargs):
            charge = rocket.shooter.body_script.component.entity_var(ULT_CHARGE)
            before_impact = charge.value()
            explode(rocket, *args, **kwargs)
            self.assertEqual(charge.value(), before_impact)
            impacts.append((fight.tick - launch, rocket.shooter, pools(), 0))

        def observe_absorb(state, shooter, *args):
            charge = shooter.body_script.component.entity_var(ULT_CHARGE)
            before_impact = charge.value()
            absorb(state, shooter, *args)
            self.assertEqual(charge.value(), before_impact)
            impacts.append((fight.tick - launch, shooter, pools(), state.instance.find_var(0, 21312).value()))

        with (
            patch.object(fight, "_explode", side_effect=observe_impact),
            patch.object(fight.grasp, "absorb", side_effect=observe_absorb),
            patch.object(fight, "_damage", wraps=fight._damage) as damage,
        ):
            if gap:
                run.frame += 90
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                for _ in range(25):
                    if not fight.rockets:
                        break
                    run.step(idle_grasp)
        damage.assert_not_called()
        self.assertFalse(fight.rockets)
        self.assertEqual([shooter for _, shooter, _, _ in impacts], list(order))
        trace = [(tick, hp, amount) for tick, _, hp, amount in impacts]
        self.assertEqual(trace[0][0], trace[1][0])
        if outer_health is None:
            amount = 0
            self.assertEqual(trace[0][0], trace[2][0])
            spent = (25, 145, 265) if flash_first else (120, 240, 265)
            expected = [((outer - damage, inner), 0) for damage in spent]
        elif outer_health == 5:
            amount = 120 if flash_first else 25
            expected = [((0, 5), 0), ((0, 0), 0), ((0, 0), amount)]
        else:
            amount = 0
            self.assertEqual(trace[0][0], trace[2][0])
            expected = [((175, 5), 0), ((55, 5), 0), ((0, 5), 0)] if flash_first else [
                ((80, 5), 0), ((0, 5), 0), ((0, 0), 0),
            ]
        self.assertEqual([event[1:] for event in trace], expected)
        self.assertEqual([health(run, player).total for player in defenders], before)
        self.assertFalse(fight.is_stunned(sigma))
        for attacker in attackers:
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(attacker.client.messages(combat.HIT))
        for _ in range(150):
            if not sigma_grasp.active(sigma):
                break
            run.step({})
        self.assertIsNone(sigma_grasp.active(sigma))
        sigma_pool = health(run, sigma)
        self.assertAlmostEqual(sigma_pool.bonus_shields, amount * .6, places=4)
        self.assertEqual(sigma_pool.bonus_shields_max, sigma_pool.bonus_shields)
        self.assertAlmostEqual(sigma_pool.total, before[-1] + amount * .6, places=4)
        return trace


    def test_enemy_sentry_cannot_damage_or_slow_through_the_wall(self):
        run, owner, enemy, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[owner.hero.guid]))
        self.move(enemy, (0, 0, 6))
        enemy.body_script.ult_seconds = 0
        before = health(run, owner).total
        for _ in range(45):
            run.step({enemy: (-32768, 8192, 8)})
        for _ in range(80):
            run.step({})
        sentries = run.match.combat.sentries.sentries
        self.assertEqual(len(sentries), 1)
        self.assertIsNotNone(sentries[0].attached_at)
        self.assertIsNone(sentries[0].slow)
        self.assertEqual(health(run, owner).total, before)
        # Explicit destruction fixture isolates sentry reacquisition from any
        # damage done by the attack that would otherwise break the wall.
        run.match.combat.barriers.damage(wall, 4000)
        for _ in range(20):
            run.step({})
        self.assertLess(health(run, owner).total, before)
        self.assertEqual(sentries[0].target, owner.body)
        self.assertIsNotNone(sentries[0].slow)

    def test_sentry_flies_through_hostile_photon_plane_but_its_beam_is_occluded(self):
        run, caster, owner, shield = self.deploy()
        self.assertTrue(run.match.switch_hero(owner, heroes()[caster.hero.guid]))
        self.move(owner, (0, 0, 6))
        self.move(caster, (30, 0, 0))
        target = self.add_target(run, 3, (1, 0, 3))
        fight = run.match.combat
        cover = World([(-10, 0, -2, 10, 0, -2, 10, 8, -2),
                       (-10, 0, -2, 10, 8, -2, -10, 8, -2)])
        before = health(run, target).total
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(40):
                run.step({owner: (-32768, 0, 8)})
                if fight.sentries.sentries:
                    break
            [sentry] = fight.sentries.sentries
            self.assertIsNone(sentry.attached_at)
            _, crossed = fight.barriers.intersection(
                sentry.bot, sentry.bot.position, sentry.direction, 8)
            self.assertIs(crossed, shield)
            for _ in range(100):
                run.step({})
            self.assertIsNotNone(sentry.attached_at)
            self.assertAlmostEqual(sentry.bot.position[2], -1.99)
            self.assertIs(sentry.owner, owner)
            self.assertEqual(health(run, sentry.bot).total, 30)
            self.assertEqual(fight.vitals[shield.bot.entity].total, 4000)
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
            self.assertEqual(health(run, target).total, before)
            self.assertTrue(run.match.switch_hero(caster, heroes()[SOLDIER]))
            self.move(caster, (30, 0, 0))
            for _ in range(10):
                run.step({})
            self.assertEqual(sentry.target, target.body)
            self.assertIsNotNone(sentry.slow)
            self.assertLess(health(run, target).total, before)

    def test_ordinary_twelve_second_expiry_retires_actor_without_respawning_it(self):
        run, _owner, _enemy, wall = self.deploy()
        while (run.frame + 1) * .016 < wall.expires_at:
            run.step({})
        self.assertIn(wall, run.match.combat.barriers.walls)
        for _ in range(5):
            run.step({})
        self.assertEqual(run.match.combat.barriers.walls, [])
        self.assertNotIn(wall.bot, run.match.bots)
        self.assertNotIn(wall.bot.entity, run.match.combat.vitals)

    def test_sentry_catch_up_starts_on_actual_orb_break_of_photon_barrier(self):
        flight_ticks, damage = self.sentry_damage_on_photon_break()
        self.assertGreaterEqual(flight_ticks, 8)
        self.assertAlmostEqual(damage, 5 * 40 * combat.TICK, places=4)
        for gap in (4, 8):
            with self.subTest(gap=gap):
                self.assertEqual(self.sentry_damage_on_photon_break(flight_ticks, gap),
                                 (flight_ticks, damage))
                self.assertEqual(self.sentry_damage_on_photon_break(flight_ticks, gap, tail_in_gap=4),
                                 (flight_ticks, damage))

    def sentry_damage_on_photon_break(self, expected_flight=None, gap=1, *, tail_in_gap=0):
        run, victim, attacker, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(attacker, heroes()[victim.hero.guid]))
        self.move(attacker, (0, 0, 6))
        attacker.body_script.ult_seconds = 0
        for _ in range(45):
            run.step({attacker: (-32768, 8192, 8)})
        fight = run.match.combat
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        vitals = health(run, victim)
        before = vitals.total
        self.shoot(run, attacker, victim, 90, buttons=2)
        self.assertIsNone(sentry.target)
        self.assertIsNone(sentry.slow)
        self.assertEqual(vitals.total, before)
        fight.vitals[wall.bot.entity].now[0] = 5
        self.shoot(run, attacker, victim, 1, buttons=0)
        [orb] = fight.rockets
        launch_tick = run.frame
        breaks = []
        damage_wall = fight.barriers.damage

        def record_break(target, amount, **kwargs):
            damage_wall(target, amount, **kwargs)
            if target is wall and wall not in fight.barriers.walls:
                breaks.append(fight.tick)

        def world_step(ticks=1):
            run.frame += ticks
            run.match.update(run.frame * combat.TICK, run.frame)

        with patch.object(fight.barriers, "damage", side_effect=record_break):
            if expected_flight is None:
                while wall in fight.barriers.walls and run.frame - launch_tick < 30:
                    world_step()
            else:
                for _ in range(expected_flight - gap):
                    world_step()
                self.assertIn(wall, fight.barriers.walls)
                self.assertIn(orb, fight.rockets)
                self.assertEqual(vitals.total, before)
                world_step(gap + tail_in_gap)
        [break_tick] = breaks
        flight_ticks = break_tick - launch_tick
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(sentry.target, victim.body)
        self.assertIsNotNone(sentry.slow)
        for _ in range(4 - tail_in_gap):
            world_step()
        damage = attacker.stats.value(stats.HERO_DAMAGE)
        self.assertAlmostEqual(before - vitals.total, damage, places=4)
        world_step(0)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), damage)
        return flight_ticks, round(damage, 4)

    def test_photon_replay_keeps_new_orb_at_the_endpoint(self):
        run, victim, attacker, wall = self.deploy()
        self.assertTrue(run.match.switch_hero(attacker, heroes()[victim.hero.guid]))
        self.move(attacker, (0, 0, 6))
        soldier = self.add_target(run, 3, (30, 0, 0))
        self.shoot(run, attacker, victim, 90, buttons=2)
        run.step({soldier: (-32768, 0, 2), attacker: (-32768, 0, 2)})
        run.step({attacker: (-32768, 0, 2)})
        fight = run.match.combat
        [helix] = fight.rockets
        self.assertEqual(helix.last_tick, run.frame)
        self.shoot(run, attacker, victim, 1, buttons=0)
        orb = next(rocket for rocket in fight.rockets if rocket.shooter is attacker)
        self.assertIsNone(orb.last_tick)
        helix_age = helix.flown
        run.frame += 8
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(helix.flown - helix_age, 8 * combat.TICK)
        self.assertAlmostEqual(orb.flown, combat.TICK)
        self.assertIn(wall, fight.barriers.walls)

    def test_sentry_catch_up_starts_after_hostile_photon_barrier_expires(self):
        contiguous = self.sentry_damage_across_photon_expiry(1)
        self.assertAlmostEqual(contiguous, 5 * 40 * combat.TICK, places=4)
        for gap in (16, 64, 80):
            with self.subTest(gap=gap):
                self.assertAlmostEqual(self.sentry_damage_across_photon_expiry(gap),
                                       contiguous, places=4)
        self.assertAlmostEqual(self.sentry_damage_across_photon_expiry(64, allied=True),
                               64 * 40 * combat.TICK, places=4)
        offset = 1000.003
        self.assertAlmostEqual(
            self.sentry_damage_across_photon_expiry(16, clock_offset=offset),
            self.sentry_damage_across_photon_expiry(1, clock_offset=offset), places=4,
        )

    def sentry_damage_across_photon_expiry(self, gap, *, allied=False, clock_offset=0):
        run, owner, attacker, wall = self.deploy(world_clock_offset=clock_offset)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[owner.hero.guid]))
        self.move(attacker, (0, 0, 6))
        attacker.body_script.ult_seconds = 0
        victim = owner
        if allied:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            attacker.team = owner.team
            victim = self.add_target(run, 3, (0, 0, -30))
            victim.team = 1 - owner.team
        for _ in range(45):
            run.step({attacker: (-32768, 8192, 8)})
        fight = run.match.combat
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        vitals = health(run, victim)
        before = vitals.total
        final_tick = round((wall.expires_at - clock_offset) / combat.TICK) + 4
        while run.frame < final_tick - gap:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        if gap > 1:
            self.assertIn(wall, fight.barriers.walls)
            self.assertIsNone(sentry.target)
        if allied:
            self.move(victim, (0, 0, 0))
        run.frame += gap
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(wall, fight.barriers.walls)
        self.assertEqual(sentry.target, victim.body)
        self.assertIsNotNone(sentry.slow)
        self.assertFalse(fight.vitals.get(wall.bot.entity))
        damage = attacker.stats.value(stats.HERO_DAMAGE)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), damage)
        self.assertAlmostEqual(before - vitals.total, damage, places=4)
        return damage

    def test_owner_death_preserves_independent_wall_then_hero_switch_cleans_it(self):
        run, owner, enemy, wall = self.deploy()
        self.move(enemy, (0, 0, -4))
        self.shoot(run, enemy, owner, 150)
        self.assertTrue(run.match.combat.is_dead(owner))
        self.assertIn(wall, run.match.combat.barriers.walls)
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertEqual(run.match.combat.barriers.walls, [])
        self.assertNotIn(wall.bot, run.match.bots)

    def test_owner_leave_retires_actor(self):
        run, owner, _enemy, wall = self.deploy()
        run.match.leave(owner)
        self.assertEqual(run.match.combat.barriers.walls, [])
        self.assertNotIn(wall.bot.entity, run.match.combat.vitals)
