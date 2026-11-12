"""Actual Tracer and Lucio inputs through the 104319 0776 weapon."""

import math
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import (
    combat,
    lucio_aura,
    lucio_barrier,
    lucio_soundwave,
    mccree_roll,
    stats,
    tracer,
    wall_ride,
    zarya_barrier,
)
from ow174.game.collision import World
from ow174.game.commands import FIRE, JUMP, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script import expr, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerLucioTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.lucio, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team, self.lucio.team = 0, 1
        self.assertTrue(self.match.switch_hero(self.lucio, heroes()[lucio_soundwave.HERO]))
        x, y, z = self.tracer.mover.position
        self.place(self.lucio, (x, y, z - 4))
        self.step(25)
        self.health = self.vitals(self.tracer)
        self.lucio_health = self.vitals(self.lucio)
        self.weapon = next(i for i in self.lucio.body_script.component.instances.values()
                           if i.graph.index == lucio_soundwave.GRAPH)

    @staticmethod
    def place(actor, position):
        actor.mover.position[:] = position
        actor.mover.body = None

    def vitals(self, actor):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is actor))

    def aim_at(self, source, target):
        x, y, z = target.mover.position
        return aim(self.fight._eye(source), (x, y + 1, z))

    def step(self, count=1, *, lucio=0, tracer=0, aim_yaw=None):
        for _ in range(count):
            yaw, pitch = self.aim_at(self.lucio, self.tracer)
            if aim_yaw is not None:
                yaw = aim_yaw
            self.run.step({self.lucio: (yaw, pitch, lucio),
                           self.tracer: (*self.aim_at(self.tracer, self.lucio), tracer)})

    def cast(self, *, yaw=None):
        self.step(lucio=SECONDARY, aim_yaw=yaw)
        self.step(6, aim_yaw=yaw)

    def test_actual_soundwave_hits_once_launches_tracer_and_allows_pistol_return(self):
        before = tuple(self.tracer.mover.position)
        self.step(lucio=SECONDARY)
        for _ in range(8):
            if self.health.total < 150:
                break
            self.step()
        self.assertEqual(self.health.total, 125)
        self.assertEqual(self.lucio.stats.value(stats.HERO_DAMAGE), 25)
        self.assertEqual(self.weapon.state(11).counter, 1)
        self.assertTrue(self.tracer.mover.external_impulse)
        self.assertAlmostEqual(math.hypot(*self.tracer.mover.velocity), 17, delta=0.05)
        self.step(8, lucio=SECONDARY)
        self.assertGreater(self.tracer.mover.position[2], before[2] + 1)
        self.assertGreater(self.tracer.mover.position[1], before[1])
        self.assertEqual(self.health.total, 125)
        self.step(tracer=FIRE)
        self.assertEqual(self.lucio_health.total, 188)

    def test_soundwave_rejects_cone_range_cover_and_team(self):
        for reject in ("cone", "range", "cover", "ally"):
            with self.subTest(reject=reject):
                self.setUp()
                x, y, z = self.tracer.mover.position
                if reject == "range":
                    self.place(self.lucio, (x, y, z - 16))
                elif reject == "cover":
                    floor = box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                    self.match.collision_world = World(floor + wall(x - 4, z - 2, x + 4, z - 2, y, y + 5))
                elif reject == "ally":
                    self.tracer.team = self.lucio.team
                self.step(3)
                self.cast(yaw=16384 if reject == "cone" else None)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.tracer.mover.external_impulse)
                self.assertEqual(self.weapon.state(11).counter, 1)

    def test_recall_blocks_soundwave_damage_and_impulse_then_cooldown_allows_recast(self):
        origin = tuple(self.tracer.mover.position)
        self.step(tracer=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.cast()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(tuple(self.tracer.mover.position), origin)
        self.assertFalse(self.tracer.mover.external_impulse)
        self.step(90)
        self.assertFalse(tracer.recalling(self.tracer))
        cooldown = self.weapon.find_var(expr.INSTANCE, 61)
        self.assertGreater(cooldown.value(), 0)
        self.cast()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.weapon.state(11).counter, 1)
        self.step(260)
        self.assertEqual(cooldown.value(), 0)
        self.cast()
        self.assertEqual(self.health.total, 125)
        self.assertEqual(self.weapon.state(11).counter, 2)

    def test_real_sonic_amplifier_burst_and_tracer_return(self):
        self.step(lucio=FIRE)
        self.step(44)
        self.assertEqual(self.health.total, 70)
        self.assertEqual(self.lucio.stats.value(stats.HERO_DAMAGE), 80)
        self.step(tracer=FIRE)
        self.assertEqual(self.lucio_health.total, 188)


    def test_recall_after_soundwave_restores_health_and_displaced_pose(self):
        self.step(180)
        origin = tuple(self.tracer.mover.position)
        self.cast()
        self.step(8)
        self.assertEqual(self.health.total, 125)
        self.assertGreater(self.tracer.mover.position[2], origin[2] + 2)
        self.step(tracer=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.step(90)
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)
        self.assertEqual(tuple(self.tracer.mover.position), origin)
        self.assertFalse(self.tracer.mover.external_impulse)

    def test_soundwave_death_cancels_unreleased_pulse_but_keeps_a_launched_bomb(self):
        for released in (False, True):
            with self.subTest(released=released):
                self.setUp()
                self.health.damage(130)
                script = self.tracer.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.step(tracer=32, lucio=0 if released else SECONDARY)
                if released:
                    for _ in range(20):
                        if self.fight.pulse_bombs.bombs:
                            break
                        self.step()
                    self.assertEqual(len(self.fight.pulse_bombs.bombs), 1)
                    self.step(lucio=SECONDARY)
                self.step(12)
                self.assertTrue(self.health.dead)
                self.assertEqual(self.health.killer, self.lucio.body)
                self.assertFalse(self.tracer.mover.external_impulse)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.step(85)
                self.assertFalse(self.fight.pulse_bombs.bombs)
                self.assertEqual(self.lucio_health.dead, released)
                self.assertEqual(self.lucio_health.killer, self.tracer.body if released else None)


    def test_projected_barrier_spends_soundwave_without_tracer_knockback(self):
        zarya, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E0000000000068)
        zarya.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        self.place(zarya, (x + 4, y, z))
        self.step(30)
        self.run.step({zarya: (*self.aim_at(zarya, self.tracer), 16)})
        bubble = zarya_barrier.active(self.tracer)
        self.assertIsNotNone(bubble)
        origin = tuple(self.tracer.mover.position)
        self.cast()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(zarya_barrier.barrier_health(bubble, self.health), 175)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 5)
        self.assertEqual(tuple(self.tracer.mover.position), origin)
        self.assertFalse(self.tracer.mover.external_impulse)

    def test_soundwave_knockback_sweeps_against_wall_behind_tracer(self):
        x, y, z = self.tracer.mover.position
        floor = box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
        self.match.collision_world = World(floor + wall(x - 5, z + 1, x + 5, z + 1, y, y + 6))
        self.step(5)
        self.cast()
        self.step(16)
        self.assertEqual(self.health.total, 125)
        self.assertGreater(self.tracer.mover.position[2], z + 0.2)
        self.assertLess(self.tracer.mover.position[2], z + 1)

    def test_blink_leaves_soundwave_cone_and_tracer_can_fire_from_new_position(self):
        x = self.tracer.mover.position[0]
        self.run.step({self.tracer: (16384, 0, SECONDARY)})
        self.assertAlmostEqual(self.tracer.mover.position[0] - x, 7.5, delta=0.05)
        self.cast(yaw=0)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.tracer.mover.external_impulse)
        self.step(tracer=FIRE)
        self.assertEqual(self.lucio_health.total, 188)


    def barrier_cast(self):
        if lucio_barrier.model() is None:
            self.skipTest("requires extracted Sound Barrier status")
        script = self.lucio.body_script
        script.ult_seconds = 0
        cast = next(i for i in script.component.instances.values() if i.graph.index == lucio_barrier.CAST)
        return script, cast

    def charge_barrier(self):
        script, cast = self.barrier_cast()
        self.assertEqual(script.component.entity_var(338).value(), 2940)
        script.set_ult_charge(2940)
        self.step(lucio=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        return script, cast

    @staticmethod
    def barrier_pool(vitals):
        shield = vitals.temporary_shields.get(lucio_barrier.EFFECT)
        return shield.now if shield else 0

    def test_sound_barrier_charge_gate_authored_delay_and_decay(self):
        script, cast = self.barrier_cast()
        script.set_ult_charge(2939)
        self.step(lucio=32)
        self.assertFalse(cast.state(10).flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 2939)
        self.step()
        self.charge_barrier()
        self.step(40)
        self.assertEqual(self.barrier_pool(self.lucio_health), 0)
        self.step()
        self.assertEqual(self.barrier_pool(self.lucio_health), 750)
        self.assertEqual(self.barrier_pool(self.health), 0)
        lease = self.fight.lucio_barriers.leases[self.lucio.body]
        self.assertEqual(lease.component.instances[lease.instance_id].graph.index, 0x073B)
        self.step(10)
        self.assertAlmostEqual(self.barrier_pool(self.lucio_health), 730)
        self.step(365)
        self.assertEqual(self.barrier_pool(self.lucio_health), 0)
        self.assertEqual(self.lucio_health.total, 200)
        self.assertFalse(self.fight.lucio_barriers.leases)

    def test_sound_barrier_earned_by_soundwave_and_healing_tracer(self):
        if lucio_aura.model() is None:
            self.skipTest("requires extracted Crossfade status")
        script, cast = self.barrier_cast()
        meter = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 26)
        self.step(lucio=32)
        self.assertFalse(cast.state(10).flags & nodes.Ability.RUNNING)
        self.step(lucio=SECONDARY)
        self.assertEqual(self.health.total, 125)
        self.assertEqual(meter.value(), cost - 1)
        self.step(lucio=32)
        self.assertFalse(cast.state(10).flags & nodes.Ability.RUNNING)
        self.lucio.team = self.tracer.team
        self.step(lucio=8)
        for _ in range(40):
            if meter.value() == cost:
                break
            self.step()
        self.assertGreater(self.health.total, 125)
        self.assertEqual(meter.value(), cost)
        self.step(lucio=32)
        self.assertTrue(cast.state(10).flags & nodes.Ability.RUNNING)
        self.assertEqual(meter.value(), 0)
        self.step(20)
        self.assertEqual(meter.value(), 0)
        self.step(21)
        self.assertEqual(self.barrier_pool(self.health), 750)
        for _ in range(900):
            self.step()
            if not cast.state(10).flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(cast.state(10).flags & nodes.Ability.RUNNING)
        self.assertEqual(self.health.now[combat.HEALTH_POOL], 150)
        before = meter.value()
        self.health.damage(2)
        self.step(10)
        self.assertAlmostEqual(meter.value() - before, 2, places=3)
        self.step(10)
        self.assertAlmostEqual(meter.value() - before, 2, places=3)

    def test_sound_barrier_meter_ignores_temporary_shield_then_credits_projectile_hit(self):
        script, _ = self.barrier_cast()
        meter = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(0)
        origin = tuple(self.tracer.mover.position)
        self.health.grant_temporary_shields(lucio_barrier.EFFECT, 30, 30)
        self.step(lucio=SECONDARY)
        self.assertEqual(self.barrier_pool(self.health), 5)
        self.assertEqual(meter.value(), 0)
        self.step(85)
        self.place(self.tracer, origin)
        self.step()
        self.step(lucio=FIRE)
        self.assertEqual(meter.value(), 0)
        for _ in range(45):
            self.step(lucio=FIRE)
            if self.health.now[combat.HEALTH_POOL] < 150:
                break
        self.assertLess(self.health.now[combat.HEALTH_POOL], 150)
        self.assertEqual(meter.value(), 150 - self.health.now[combat.HEALTH_POOL])

    def test_sound_barrier_passive_uses_authored_five_points_per_second(self):
        script = self.lucio.body_script
        meter = script.component.entity_var(ULT_CHARGE)
        self.assertEqual(meter.value(), 0)
        self.step(63)
        self.assertEqual(meter.value(), 5)
        self.step(63)
        self.assertEqual(meter.value(), 10)

    def test_sound_barrier_charge_uses_authored_roadhog_factor(self):
        script, _ = self.barrier_cast()
        meter = script.component.entity_var(ULT_CHARGE)
        origin = tuple(self.tracer.mover.position)
        self.assertTrue(self.match.switch_hero(self.tracer, heroes()[0x02E0000000000040]))
        self.place(self.tracer, origin)
        self.step(5)
        health = self.vitals(self.tracer)
        before = health.total
        self.step(lucio=SECONDARY)
        self.assertEqual(health.total, before - 25)
        self.assertEqual(meter.value(), 18.75)

    def test_airborne_sound_barrier_waits_for_landing(self):
        self.barrier_cast()
        x, y, z = self.lucio.mover.position
        self.place(self.lucio, (x, y + 12, z))
        self.step()
        self.charge_barrier()
        self.step(41)
        self.assertTrue(self.lucio.mover.airborne)
        self.assertEqual(self.barrier_pool(self.lucio_health), 0)
        for _ in range(120):
            if self.lucio.body in self.fight.lucio_barriers.leases:
                break
            self.step()
        self.assertFalse(self.lucio.mover.airborne)
        self.assertEqual(self.barrier_pool(self.lucio_health), 750)

    def test_tracer_pistol_and_pulse_spend_only_lucios_temporary_shield(self):
        self.charge_barrier()
        self.step(41)
        self.assertEqual(self.barrier_pool(self.lucio_health), 750)
        begin = self.run.frame
        self.step(tracer=FIRE)
        self.assertAlmostEqual(self.barrier_pool(self.lucio_health), 736)
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(tracer=32)
        for _ in range(120):
            self.step()
            if self.tracer.stats.value(stats.HERO_DAMAGE) >= 367:
                break
        self.assertAlmostEqual(self.tracer.stats.value(stats.HERO_DAMAGE), 367)
        self.assertEqual(self.lucio_health.now[combat.HEALTH_POOL], 200)
        self.assertAlmostEqual(self.barrier_pool(self.lucio_health), 750 - 367 - 2 * (self.run.frame - begin))
        self.assertFalse(self.fight.pulse_bombs.bombs)

    def test_ally_tracer_recall_does_not_restore_consumed_barrier(self):
        self.lucio.team = self.tracer.team
        enemy, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        enemy.team = 1 - self.tracer.team
        self.charge_barrier()
        self.step(41)
        self.assertEqual(self.barrier_pool(self.health), 750)
        self.assertEqual(self.barrier_pool(self.vitals(enemy)), 0)
        start = self.run.frame
        self.run.step({enemy: (*self.aim_at(enemy, self.tracer), FIRE)})
        self.assertAlmostEqual(self.barrier_pool(self.health), 728)
        self.step(tracer=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.step(90)
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(self.health.now[combat.HEALTH_POOL], 150)
        self.assertAlmostEqual(self.barrier_pool(self.health), 730 - 2 * (self.run.frame - start))

    def test_lethal_tracer_pistols_during_sound_barrier_windup_prevent_grant(self):
        self.lucio_health.damage(190)
        script, cast = self.charge_barrier()
        self.step(10)
        self.step(tracer=FIRE)
        self.assertTrue(self.lucio_health.dead)
        self.assertEqual(self.lucio_health.killer, self.tracer.body)
        self.step(70)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(cast.state(10).flags & nodes.Ability.RUNNING)
        self.assertFalse(self.fight.lucio_barriers.leases)

    def test_caster_death_does_not_retire_the_applied_tracer_barrier(self):
        self.lucio.team = self.tracer.team
        enemy, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        enemy.team = 1 - self.tracer.team
        x, y, z = self.lucio.mover.position
        self.place(enemy, (x + 5, y, z))
        self.charge_barrier()
        self.step(41)
        self.assertEqual(self.barrier_pool(self.health), 750)
        self.lucio_health.damage(940)
        self.run.step({enemy: (*self.aim_at(enemy, self.lucio), FIRE)})
        self.assertTrue(self.lucio_health.dead)
        self.assertNotIn(self.lucio.body, self.fight.lucio_barriers.leases)
        self.assertIn(self.tracer.body, self.fight.lucio_barriers.leases)
        self.assertAlmostEqual(self.barrier_pool(self.health), 748)


    def test_sound_barrier_rejects_out_of_range_and_occluded_allies(self):
        for rejected in ("range", "wall"):
            with self.subTest(rejected=rejected):
                self.setUp()
                self.barrier_cast()
                self.lucio.team = self.tracer.team
                x, y, z = self.lucio.mover.position
                if rejected == "range":
                    self.place(self.tracer, (x, y, z + 35))
                else:
                    floor = box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                    self.match.collision_world = World(floor + wall(x - 4, z + 2, x + 4, z + 2, y, y + 5))
                self.step(3)
                self.charge_barrier()
                self.step(41)
                self.assertEqual(self.barrier_pool(self.lucio_health), 750)
                self.assertEqual(self.barrier_pool(self.health), 0)
                self.assertNotIn(self.tracer.body, self.fight.lucio_barriers.leases)


    def test_pistol_overflow_at_end_of_barrier_and_fresh_charge_can_recast(self):
        script, cast = self.charge_barrier()
        self.step(41)
        original = self.fight.lucio_barriers.leases[self.lucio.body]
        self.step(370)
        self.assertAlmostEqual(self.barrier_pool(self.lucio_health), 10)
        self.step(tracer=FIRE)
        self.assertEqual(self.barrier_pool(self.lucio_health), 0)
        self.assertAlmostEqual(self.lucio_health.now[combat.HEALTH_POOL], 196)
        for _ in range(700):
            self.step()
            if not cast.state(10).flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(cast.state(10).flags & nodes.Ability.RUNNING)
        self.assertFalse(self.fight.lucio_barriers.leases)
        self.assertFalse(original.component.instances)
        self.step(lucio=32)
        self.step(42)
        self.assertFalse(self.fight.lucio_barriers.leases)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(lucio=32)
        self.step(41)
        self.assertEqual(self.barrier_pool(self.lucio_health), 750)
        self.assertEqual(self.lucio_health.now[combat.HEALTH_POOL], 196)
        self.assertIsNot(self.fight.lucio_barriers.leases[self.lucio.body], original)


    def test_retained_world_ticks_keep_pulse_and_barrier_decay_in_order(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                self.setUp()
                self.charge_barrier()
                self.step(191)
                script = self.tracer.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.step(tracer=32)
                for _ in range(120):
                    self.step()
                    bombs = self.fight.pulse_bombs.bombs
                    if bombs and bombs[0].stuck and bombs[0].fuse_left <= 0.08:
                        break
                self.assertTrue(bombs and bombs[0].stuck and bombs[0].fuse_left <= 0.08)
                self.assertAlmostEqual(self.barrier_pool(self.lucio_health), 281)
                if sparse:
                    self.run.frame += 20
                    self.match.update(self.run.frame * combat.TICK, self.run.frame)
                else:
                    self.step(20)
                self.assertFalse(self.fight.pulse_bombs.bombs)
                self.assertFalse(self.fight.lucio_barriers.leases)
                self.assertAlmostEqual(self.lucio_health.total, 121)

    def aura_scene(self):
        if lucio_aura.model() is None:
            self.skipTest("requires extracted Crossfade status and speed controller")
        self.lucio.team = self.tracer.team
        self.step(25)

    def aura_speed(self):
        values = self.fight.lucio_auras.mods(self.tracer.body).values()
        return next((value for index, _, value, _ in values if index == 0), 0)

    def march(self, frames):
        for _ in range(frames):
            self.run.frame += 1
            frame = self.run.frame
            self.match.update(frame * combat.TICK, frame)
            for player in self.run.players:
                player.client.queue_entities(self.match.remote_movements(player))
            for player in self.run.players:
                player.take_commands([Command(frame, forward=127 if player is self.tracer else 0)])

    def test_crossfade_and_amp_speed_drive_actual_tracer_motion(self):
        self.aura_scene()
        base = self.tracer.mover.data.run_forward
        start = tuple(self.tracer.mover.position)
        self.assertAlmostEqual(self.aura_speed(), 0.25)
        self.march(28)
        self.assertAlmostEqual(self.tracer.mover.velocity[2], base * 1.25, delta=0.003)
        self.place(self.tracer, start)
        self.step(lucio=16)
        self.step(35)
        self.assertAlmostEqual(self.aura_speed(), 0.6)
        self.march(28)
        self.assertAlmostEqual(self.tracer.mover.velocity[2], base * 1.6, delta=0.004)
        self.place(self.tracer, start)
        self.step(190)
        self.assertAlmostEqual(self.aura_speed(), 0.25)
        self.step(lucio=8)
        self.step(35)
        self.assertEqual(self.aura_speed(), 0)
        self.march(28)
        self.assertAlmostEqual(self.tracer.mover.velocity[2], base, delta=0.003)

    def test_crossfade_ally_and_self_healing_rates_and_amp_cooldown(self):
        self.aura_scene()
        self.health.damage(100)
        self.lucio_health.damage(100)
        self.step(24)
        self.assertEqual((self.health.total, self.lucio_health.total), (50, 100))
        self.step(lucio=8)
        self.step(34)
        ally, own = self.health.total, self.lucio_health.total
        self.step(25)
        self.assertAlmostEqual(self.health.total - ally, 6.4, places=4)
        self.assertAlmostEqual(self.lucio_health.total - own, (16 / 1.3) * 25 * combat.TICK, places=4)
        self.step(lucio=16)
        self.step(28)
        ally, own = self.health.total, self.lucio_health.total
        self.step(25)
        self.assertAlmostEqual(self.health.total - ally, 20.8, places=4)
        self.assertAlmostEqual(self.lucio_health.total - own, 16, places=4)
        self.step(175)
        amp = next(i for i in self.lucio.body_script.component.instances.values()
                   if i.graph.index == lucio_aura.AMP)
        active = amp.state(4)
        self.assertFalse(active.active)
        self.assertGreater(amp.find_var(expr.INSTANCE, 61).value(), 0)
        counter = active.counter
        self.step(lucio=16)
        self.step(30)
        self.assertEqual(active.counter, counter)
        self.step(760)
        self.assertEqual(amp.find_var(expr.INSTANCE, 61).value(), 0)
        self.step(lucio=16)
        self.step(30)
        self.assertTrue(active.active)
        self.assertNotEqual(active.counter, counter)

    def test_blink_exits_and_reenters_crossfade_then_wall_and_team_end_healing(self):
        self.aura_scene()
        x, y, z = self.tracer.mover.position
        self.place(self.tracer, (x + 8, y, z))
        self.health.damage(80)
        self.step(lucio=8)
        self.step(32)
        before = self.health.total
        self.assertGreater(before, 70)
        self.run.step({self.tracer: (16384, 0, SECONDARY)})
        self.assertAlmostEqual(self.tracer.mover.position[0], x + 15.5, delta=0.05)
        edge = self.health.total
        self.step(12)
        self.assertEqual(self.health.total, edge)
        self.run.step({self.tracer: (-16384, 0, SECONDARY)})
        self.assertAlmostEqual(self.tracer.mover.position[0], x + 8, delta=0.05)
        self.step(10)
        self.assertAlmostEqual(self.health.total, edge + 2.56, places=4)
        floor = box(x - 20, x + 25, y - 1, y, z - 10, z + 10)
        cover = wall(x + 4, z - 5, x + 4, z + 1, y, y + 5)
        self.match.collision_world = World(floor + cover)
        before = self.health.total
        self.step(12)
        self.assertEqual(self.health.total, before)
        self.match.collision_world = World(floor)
        self.tracer.team = 1 - self.lucio.team
        self.step(12)
        self.assertEqual(self.health.total, before)

    def test_amp_healing_ends_on_world_clock_when_lucio_sends_no_commands(self):
        self.aura_scene()
        self.health.damage(100)
        self.step(lucio=8)
        self.step(32)
        self.step(lucio=16)
        self.step(145)
        script_frame = self.lucio.body_script.frame
        for _ in range(85):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(self.lucio.body_script.frame, script_frame)
        self.health.damage(40)
        before = self.health.total
        for _ in range(25):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertAlmostEqual(self.health.total - before, 6.4, places=4)

    def ride_scene(self, *, offset=0.45, height=7, length=14):
        x, y, z = self.lucio.mover.position
        floor = box(x - 20, x + 20, y - 1, y, z - 12, z + 25)
        side = wall(x + offset, z - 8, x + offset, z + length, y, y + height)
        self.match.collision_world = World(floor + side)
        self.place(self.tracer, (x - 3, y, z + 3))
        for _ in range(10):
            self.ride_step(jump=False)
        instance = wall_ride.controller(self.lucio.body_script)
        self.assertIsNotNone(instance)
        return instance

    def ride_step(self, *, jump=True, attack=0, facing=None):
        self.run.frame += 1
        frame = self.run.frame
        self.match.update(frame * combat.TICK, frame)
        for player in self.run.players:
            player.client.queue_entities(self.match.remote_movements(player))
        yaw, pitch = self.aim_at(self.tracer, self.lucio) if facing is None else facing
        self.tracer.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=attack)])
        self.lucio.take_commands([Command(frame, forward=127, buttons=JUMP if jump else 0)])

    def sustain_ride(self):
        phases = []
        for _ in range(70):
            self.ride_step()
            contact = self.lucio.mover.wall_contact
            if contact is not None:
                phases.append(contact[1])
                if contact[1] == 3:
                    break
        self.assertEqual(list(dict.fromkeys(phases)), [2, 3])

    def test_wall_ride_sustains_after_tracer_pistol_and_release_jumps(self):
        instance = self.ride_scene()
        self.sustain_ride()
        mover = self.lucio.mover
        self.assertTrue(instance.component.entity_var(wall_ride.CONTACT).value())
        self.assertEqual(instance.vars[14965].value(), 1)
        self.ride_step(attack=FIRE)
        self.assertEqual(self.lucio_health.total, 188)
        self.assertIsNotNone(mover.wall_contact)
        self.ride_step(jump=False)
        for _ in range(3):
            if mover.wall_jump is not None:
                break
            self.ride_step(jump=False)
        self.assertIsNone(mover.wall_contact)
        self.assertFalse(instance.component.entity_var(wall_ride.CONTACT).value())
        self.assertIsNotNone(mover.wall_jump)
        self.assertEqual(mover.wall_jump[0], self.run.frame + 1)
        pending = mover.wall_jump
        before_capture = snapshot_mover(mover)
        mover.wall_jump = None
        self.assertNotEqual(snapshot_mover(mover), before_capture)
        mover.wall_jump = pending
        self.assertEqual(snapshot_mover(mover), before_capture)
        before = mover.velocity[2]
        self.ride_step(jump=False)
        self.assertIsNone(mover.wall_jump)
        self.assertGreater(mover.velocity[2], before + 2)

    def test_wall_ride_ignores_low_or_distant_surfaces(self):
        for settings in ({"offset": 0.8}, {"height": 0.6}):
            with self.subTest(**settings):
                self.setUp()
                instance = self.ride_scene(**settings)
                for _ in range(40):
                    self.ride_step()
                    self.assertIsNone(self.lucio.mover.wall_contact)
                slot = instance.component.entity_var(wall_ride.CONTACT, create=False)
                self.assertFalse(slot is not None and slot.value())

    def test_lethal_tracer_pistol_retires_live_wall_ride(self):
        instance = self.ride_scene()
        self.sustain_ride()
        self.lucio_health.damage(190)
        self.ride_step(attack=FIRE)
        self.assertTrue(self.lucio_health.dead)
        self.assertEqual(self.lucio_health.killer, self.tracer.body)
        self.assertFalse(instance.component.entity_var(wall_ride.CONTACT).value())
        mover = self.lucio.mover
        self.assertIsNone(mover.wall_contact)
        self.assertIsNone(mover.wall_jump)
        position = tuple(mover.position)
        for _ in range(10):
            self.ride_step()
        self.assertEqual(tuple(mover.position), position)

    def test_finite_wall_edge_ends_ride_without_a_second_attach(self):
        edge = self.lucio.mover.position[2] + 6
        instance = self.ride_scene(length=6)
        phases = []
        for _ in range(100):
            self.ride_step()
            contact = self.lucio.mover.wall_contact
            if contact is None:
                if phases:
                    break
                continue
            phases.append(contact[1])
        self.assertEqual(list(dict.fromkeys(phases)), [2, 3, 4])
        self.assertGreater(self.lucio.mover.position[2], edge)
        self.assertIsNone(self.lucio.mover.wall_contact)
        self.assertFalse(instance.component.entity_var(wall_ride.CONTACT).value())
        self.assertTrue(instance.state(12).active)
        self.ride_step(jump=False, attack=FIRE)
        self.assertIsNotNone(self.lucio.mover.wall_jump)
        self.assertEqual(self.lucio_health.total, 188)

    def test_actual_flashbang_ends_ride_without_queueing_wall_jump(self):
        shooter, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=mccree_roll.HERO)
        shooter.team = self.tracer.team
        self.step(28)
        instance = self.ride_scene()
        self.sustain_ride()
        x, y, z = self.lucio.mover.position
        self.place(shooter, (x - 3, y - 1, z + 0.5))
        yaw, pitch = self.aim_at(shooter, self.lucio)
        shooter.take_commands([Command(self.run.frame, yaw=yaw, pitch=pitch, buttons=16)])
        for _ in range(35):
            self.ride_step()
            if self.fight.is_stunned(self.lucio):
                break
        self.assertTrue(self.fight.is_stunned(self.lucio))
        self.assertFalse(instance.component.entity_var(wall_ride.CONTACT).value())
        self.assertIsNone(self.lucio.mover.wall_contact)
        self.assertIsNone(self.lucio.mover.wall_jump)
        self.assertEqual(self.lucio_health.total, 175)

    def test_actual_pulse_sticks_to_wall_riding_lucio_and_follows_the_jump(self):
        self.ride_scene(length=22)
        self.sustain_ride()
        x, _, z = self.lucio.mover.position
        self.place(self.tracer, (x - 2.5, self.tracer.mover.position[1], z + 1))
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        bomb = None
        for frame in range(42):
            x, y, z = self.lucio.mover.position
            vx, vy, vz = self.lucio.mover.velocity
            point = (x + vx * 0.27, y + 0.75 + vy * 0.27, z + vz * 0.27)
            facing = aim(self.fight._eye(self.tracer), point)
            self.ride_step(attack=32 if frame == 0 else 0, facing=facing)
            bombs = self.fight.pulse_bombs.bombs
            if bombs and bombs[0].stuck:
                bomb = bombs[0]
                break
        self.assertIsNotNone(bomb)
        self.assertEqual(bomb.attached, self.lucio.body)
        self.assertIsNotNone(self.lucio.mover.wall_contact)
        self.assertEqual(self.lucio_health.total, 195)
        at_contact = bomb.position
        for _ in range(8):
            self.ride_step(jump=False)
        self.assertIsNone(self.lucio.mover.wall_contact)
        self.assertEqual(bomb.attached, self.lucio.body)
        self.assertGreater(math.dist(at_contact, bomb.position), 0.5)
        for _ in range(75):
            self.ride_step(jump=False)
        self.assertTrue(self.lucio_health.dead)
        self.assertEqual(self.lucio_health.killer, self.tracer.body)
        self.assertFalse(self.fight.pulse_bombs.bombs)

    def test_actual_ana_antigrenade_blocks_crossfade_until_expiry(self):
        self.aura_scene()
        ana, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E000000000013B)
        ana.team = 1 - self.tracer.team
        self.step(25)
        self.step(lucio=8)
        self.step(32)
        self.health.damage(30)
        self.run.step({ana: (*self.aim_at(ana, self.tracer), 16)})
        for _ in range(50):
            if self.health.healing_block_until > self.fight.tick:
                break
            self.step()
        deadline = self.health.healing_block_until
        self.assertGreater(deadline, self.fight.tick)
        blocked_health = self.health.total
        while self.fight.tick + 1 < deadline:
            self.step()
        self.assertEqual(self.health.total, blocked_health)
        self.step(25)
        self.assertAlmostEqual(self.health.total - blocked_health, 6.4, places=4)

    def test_applied_amp_keeps_healing_tracer_through_actual_flashbang(self):
        self.aura_scene()
        shooter, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=mccree_roll.HERO)
        shooter.team = 1 - self.tracer.team
        x, y, z = self.lucio.mover.position
        self.place(shooter, (x + 4, y, z))
        self.step(30)
        self.step(lucio=8)
        self.step(32)
        self.step(lucio=16)
        self.step(28)
        self.health.damage(60)
        self.run.step({shooter: (*self.aim_at(shooter, self.lucio), 16)})
        for _ in range(30):
            if self.fight.is_stunned(self.lucio):
                break
            self.step()
        self.assertTrue(self.fight.is_stunned(self.lucio))
        before = self.health.total
        self.step(10)
        self.assertTrue(self.fight.is_stunned(self.lucio))
        self.assertAlmostEqual(self.health.total - before, 8.32, places=4)

    def test_two_ordinary_healing_songs_do_not_stack_on_tracer(self):
        self.aura_scene()
        other, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=lucio_soundwave.HERO)
        other.team = self.tracer.team
        self.step(25)
        self.run.step({other: (0, 0, 8), self.lucio: (0, 0, 8)})
        self.step(38)
        receiver = self.fight.lucio_auras.receivers[self.tracer.body]
        self.assertEqual(set(receiver.sources), {other.body, self.lucio.body})
        self.health.damage(80)
        before = self.health.total
        self.step(25)
        self.assertAlmostEqual(self.health.total - before, 6.4, places=4)
