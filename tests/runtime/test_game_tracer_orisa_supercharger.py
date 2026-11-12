"""Actual Orisa Supercharger casts and damage in Tracer matches."""

import os
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, orisa_barrier
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "verified 104319 supplement required")
class TracerOrisaSuperchargerTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.orisa, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.manager = self.fight.superchargers
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[orisa_barrier.HERO]))
        x, y, z = self.tracer.mover.position
        self.move(self.orisa, (x, y, z + 12))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        for _ in range(30):
            self.run.step({})

    @staticmethod
    def move(player, position):
        player.mover.position[:] = position
        player.mover.body = None

    def deploy(self):
        script = self.orisa.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.orisa: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(self.manager.active)
        flight = None
        for _ in range(100):
            self.run.step({})
            flight = next((r for r in self.fight.rockets if r.supercharger_body == self.orisa.body), flight)
            if self.manager.active:
                break
        self.assertIsNotNone(flight)
        self.assertNotIn(flight, self.fight.rockets)
        device, = self.manager.active.values()
        self.assertEqual(device.health, 200)
        self.assertEqual(device.script.graph.index, 0x0D00)
        self.assertEqual(device.until - self.fight.tick, 938)
        self.assertEqual(self.manager.scale(self.orisa), 1.5)
        return device

    def vitals(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is player))

    def fire(self, shooter, point):
        self.run.step({shooter: (*aim(self.fight._eye(shooter), point), FIRE)})

    def test_allied_device_boosts_tracer_pistols_only_within_range(self):
        self.tracer.team = self.orisa.team
        device = self.deploy()
        target, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        target.team = 1 - self.tracer.team
        x, y, z = device.position
        self.move(self.orisa, (x + 8, y, z))
        health = self.vitals(target)
        for offset, damage in ((2, 18), (35, 12)):
            self.move(self.tracer, (x + offset, y, z))
            self.move(target, (x + offset, y, z + 6))
            for _ in range(16):
                self.run.step({})
            before = health.total
            self.fire(self.tracer, (x + offset, y + 1, z + 6))
            self.assertEqual(health.total, before - damage)
            self.assertEqual(self.manager.scale(target), 1)
        self.assertNotIn((device.entity, self.tracer.body), self.manager.buffs)

    def test_allied_device_boosts_melee_and_pulse_through_detonation(self):
        self.tracer.team = self.orisa.team
        device = self.deploy()
        target, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        target.team = 1 - self.tracer.team
        x, y, z = device.position
        self.move(self.orisa, (x - 8, y, z))
        self.move(self.tracer, (x + 3, y, z))
        self.move(target, (x + 3, y, z + 1.5))
        health = self.vitals(target)
        health.most[combat.HEALTH_POOL] = health.now[combat.HEALTH_POOL] = 1000
        self.run.step({self.tracer: (0, 0, 0x800)})
        for _ in range(40):
            self.run.step({})
        self.assertEqual(health.total, 955)

        self.move(target, (x + 3, y, z + 3))
        self.run.step({})
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (0, 0, 32)})
        for _ in range(35):
            self.run.step({})
            if health.total < 955:
                break
        self.assertEqual(health.total, 947.5)
        bomb, = self.fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, target.body)
        self.move(self.tracer, (x + 35, y, z))
        self.run.step({})
        self.assertEqual(self.manager.scale(self.tracer), 1)
        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(health.total, 422.5)

    def test_wall_blocks_tracer_boost_with_a_clear_shot_to_enemy(self):
        self.tracer.team = self.orisa.team
        device = self.deploy()
        target, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        target.team = 1 - self.tracer.team
        x, y, z = device.position
        self.move(self.tracer, (x + 3, y, z))
        self.move(target, (x + 3, y, z + 6))
        self.move(self.orisa, (x - 6, y, z))
        open_world = self.match.collision_world
        walled = World(box(x - 25, x + 25, y - 2, y, z - 25, z + 25)
                       + box(x + 1, x + 1.2, y, y + 5, z - 7, z + 7))
        health = self.vitals(target)
        for world, damage in ((walled, 12), (open_world, 18)):
            self.match.collision_provider = StaticCollisionProvider(world)
            self.match.collision_world = world
            for actor in self.run.players:
                actor.mover.use(world=world)
            for _ in range(16):
                self.run.step({})
            before = health.total
            self.fire(self.tracer, (x + 3, y + 1, z + 6))
            self.assertEqual(health.total, before - damage)

    def test_authored_device_expiry_retires_boost_and_charge_lock(self):
        device = self.deploy()
        component = self.orisa.body_script.component
        self.assertTrue(component.entity_var(842).value())
        for tick in (device.until - 1, device.until):
            self.match.update(tick * combat.TICK, tick)
            self.assertEqual(device.entity in self.manager.active, tick < device.until)
        self.assertFalse(self.manager.buffs)
        self.assertEqual(self.manager.scale(self.orisa), 1)
        self.assertFalse(component.entity_var(842).value())

    def test_quick_melee_checks_device_range_and_team(self):
        device = self.deploy()
        x, y, z = device.position
        self.move(self.orisa, (x + 8, y, z))
        for offset, team, health in ((5, 1 - self.orisa.team, 200),
                                     (2, self.orisa.team, 200),
                                     (2, 1 - self.orisa.team, 170)):
            self.tracer.team = team
            self.move(self.tracer, (x, y, z + offset))
            self.run.step({})
            facing = aim(self.fight._eye(self.tracer), (x, y + 0.55, z))
            self.run.step({self.tracer: (*facing, 0x800)})
            for _ in range(45):
                self.run.step({self.tracer: (*facing, 0)})
            self.assertEqual(device.health, health)
        self.assertEqual(self.vitals(self.orisa).total, 450)

    def throw_pulse(self, device, *, ground=False):
        x, y, z = device.position
        self.move(self.orisa, (x + 8, y, z))
        self.move(self.tracer, (x, y, z + 3))
        self.run.step({})
        target = (x, y + 0.15, z + 2.0) if ground else (x, y + 0.15, z)
        facing = aim(self.fight._eye(self.tracer), target)
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (*facing, 32)})
        for _ in range(55):
            for bomb in self.fight.pulse_bombs.bombs:
                if bomb.stuck:
                    self.assertIsNone(bomb.attached)
                    return bomb
            self.run.step({self.tracer: (*facing, 0)})
        self.fail("Pulse Bomb did not find a contact")

    def test_pulse_contact_and_fuse_destroy_hostile_device(self):
        self.tracer.team = 1 - self.orisa.team
        device = self.deploy()
        bomb = self.throw_pulse(device)
        self.assertEqual(bomb.attached_supercharger, device.entity)
        self.assertEqual(device.health, 195)
        self.assertEqual(self.manager.scale(self.orisa), 1.5)
        for _ in range(40):
            self.run.step({})
        self.assertEqual(device.health, 195)
        for _ in range(30):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(device.health, 0)
        self.assertEqual(self.manager.scale(self.orisa), 1)
        self.assertEqual(self.vitals(self.orisa).total, 450)

    def test_breaking_pulse_contact_detaches_and_keeps_the_fuse(self):
        self.tracer.team = 1 - self.orisa.team
        device = self.deploy()
        self.manager.damage(device, 195)
        bomb = self.throw_pulse(device)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, self.manager.active)
        self.assertIsNone(bomb.attached_supercharger)
        anchor, fuse = bomb.position, bomb.fuse_left
        self.assertGreater(fuse, 0.98)
        self.run.step({})
        self.assertEqual(bomb.position, anchor)
        self.assertAlmostEqual(bomb.fuse_left, fuse - combat.TICK)
        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)

    def test_ground_pulse_blast_destroys_nearby_hostile_device(self):
        self.tracer.team = 1 - self.orisa.team
        device = self.deploy()
        bomb = self.throw_pulse(device, ground=True)
        self.assertIsNone(bomb.attached_supercharger)
        self.assertEqual(device.health, 200)
        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(device.health, 0)
        self.assertEqual(self.manager.scale(self.orisa), 1)

    def test_static_wall_protects_hostile_device_from_pulse(self):
        self.tracer.team = 1 - self.orisa.team
        device = self.deploy()
        x, y, z = device.position
        wall = World(box(x - 15, x + 15, y - 2, y, z - 15, z + 15)
                     + box(x - 8, x + 8, y, y + 4, z + 1.3, z + 1.5))
        self.match.collision_provider = StaticCollisionProvider(wall)
        self.match.collision_world = wall
        for actor in self.run.players:
            actor.mover.use(world=wall)
        bomb = self.throw_pulse(device)
        self.assertIsNone(bomb.attached_supercharger)
        self.assertEqual(device.health, 200)
        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(device.health, 200)

    def test_pulse_leaves_allied_device_intact(self):
        self.tracer.team = self.orisa.team
        device = self.deploy()
        bomb = self.throw_pulse(device)
        self.assertIsNone(bomb.attached_supercharger)
        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(device.health, 200)
        self.assertEqual(self.manager.scale(self.orisa), 1.5)

    def test_tracer_kill_during_windup_prevents_supercharger_launch(self):
        self.tracer.team = 1 - self.orisa.team
        health = self.vitals(self.orisa)
        health.now[combat.HEALTH_POOL] = 6
        health.now[combat.ARMOUR_POOL] = 0
        script = self.orisa.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.orisa: (0, 0, 32)})
        cast = next(i for i in script.component.instances.values() if i.graph.index == 0x0CF5)
        self.assertTrue(cast.state(11).active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        x, y, z = self.orisa.mover.position
        self.fire(self.tracer, (x, y + 1, z))
        self.assertTrue(health.dead)
        for _ in range(40):
            self.run.step({})
            self.assertFalse(any(
                rocket.supercharger_body == self.orisa.body for rocket in self.fight.rockets
            ))
        self.assertEqual(cast.state(10).counter, 0)
        self.assertFalse(self.manager.active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_tracer_kill_after_launch_leaves_supercharger_projectile_and_device(self):
        self.tracer.team = 1 - self.orisa.team
        ally, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        ally.team = self.orisa.team
        x, y, z = self.orisa.mover.position
        self.move(ally, (x + 5, y, z + 5))
        for _ in range(8):
            self.run.step({})
        health = self.vitals(self.orisa)
        health.now[combat.HEALTH_POOL] = 6
        health.now[combat.ARMOUR_POOL] = 0
        script = self.orisa.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.orisa: (0, 0, 32)})
        flight = None
        for _ in range(60):
            self.run.step({})
            flight = next((r for r in self.fight.rockets if r.supercharger_body == self.orisa.body), None)
            if flight is not None:
                break
        self.assertIsNotNone(flight)
        self.assertFalse(self.manager.active)
        self.fire(self.tracer, (x, y + 1, z))
        self.assertTrue(health.dead)
        self.assertIn(flight, self.fight.rockets)
        for _ in range(60):
            self.run.step({})
            if self.manager.active:
                break
        device, = self.manager.active.values()
        self.assertTrue(health.dead)
        self.assertEqual(device.source_body, self.orisa.body)
        self.assertNotIn(flight, self.fight.rockets)
        self.assertEqual(self.manager.scale(self.orisa), 1)
        self.assertEqual(self.manager.scale(ally), 1.5)

    def test_device_boost_and_charge_gate_survive_death_and_allow_recast_after_expiry(self):
        self.tracer.team = self.orisa.team
        device = self.deploy()
        source = device.source_body
        enemy, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        enemy.team = 1 - self.orisa.team
        x, y, z = device.position
        self.move(self.orisa, (x - 8, y, z))
        self.move(self.tracer, (x + 3, y, z))
        self.move(enemy, (x + 3, y, z + 6))
        for _ in range(16):
            self.run.step({})
        health = self.vitals(self.orisa)
        health.now[combat.HEALTH_POOL] = 20
        health.now[combat.ARMOUR_POOL] = 0
        self.fire(enemy, (x - 8, y + 1, z))
        self.assertTrue(health.dead)
        self.run.step({})
        self.assertEqual(self.manager.scale(self.orisa), 1)
        self.assertIs(self.manager.active[device.entity], device)

        enemy_health = self.vitals(enemy)
        before = enemy_health.total
        self.fire(self.tracer, (x + 3, y + 1, z + 6))
        self.assertEqual(before - enemy_health.total, 18)
        for _ in range(700):
            if self.orisa.body != source:
                break
            self.run.step({})
        self.assertNotEqual(self.orisa.body, source)
        self.assertEqual(device.source_body, source)
        self.assertIs(self.manager.active[device.entity], device)
        self.move(self.orisa, (x - 8, y, z))
        script = self.orisa.body_script
        script.ult_seconds = 0.2  # Accelerate the existing passive meter for this fixture.
        component = script.component
        self.assertTrue(component.entity_var(842).value())
        self.run.step({self.orisa: (0, 0, 32)})
        for _ in range(25):
            self.run.step({})
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(tuple(self.manager.active), (device.entity,))
        self.assertFalse(any(r.supercharger_body for r in self.fight.rockets))

        while self.fight.tick < device.until:
            self.run.step({})
        self.assertFalse(component.entity_var(842).value())
        self.assertFalse(self.manager.active)
        before = enemy_health.total
        self.fire(self.tracer, (x + 3, y + 1, z + 6))
        self.assertEqual(before - enemy_health.total, 12)

        cost = component.entity_var(ULT_COST).value()
        for _ in range(20):
            if component.entity_var(ULT_CHARGE).value() == cost:
                break
            self.run.step({})
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
        script.ult_seconds = 0  # Isolate charge spending from the accelerated fixture clock.
        self.run.step({self.orisa: (0, 0, 32)})
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(100):
            self.run.step({})
            if self.manager.active:
                break
        replacement, = self.manager.active.values()
        self.assertNotEqual(replacement.entity, device.entity)
        self.assertEqual(replacement.source_body, self.orisa.body)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)

    def orisa_hits_tracer(self, damage):
        x, y, z = self.tracer.mover.position
        health = self.vitals(self.tracer)
        before = health.total
        self.fire(self.orisa, (x, y + 1, z))
        for _ in range(25):
            if health.total < before:
                break
            self.run.step({})
        self.assertEqual(health.total, before - damage)

    def test_tracer_pistols_destroy_hostile_device_and_remove_orisa_boost(self):
        self.tracer.team = 1 - self.orisa.team
        device = self.deploy()
        x, y, z = device.position
        self.move(self.tracer, (x, y, z + 4))
        self.move(self.orisa, (x, y, z - 3))
        for _ in range(16):
            self.run.step({})
        health = self.vitals(self.orisa)
        self.assertEqual(self.manager.scale(self.tracer), 1)
        self.orisa_hits_tracer(16.5)
        self.fire(self.tracer, (x, y + 0.5, z))
        self.assertEqual(device.health, 188)
        self.assertEqual(device.component.entity_var(253).value(), 188)
        self.assertEqual(health.total, 450)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
        for expected in (*range(176, 0, -12), 0):
            for _ in range(12):
                self.run.step({})
            self.fire(self.tracer, (x, y + 0.5, z))
            self.assertEqual(device.health, expected)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, self.manager.active)
        self.assertEqual(self.manager.scale(self.orisa), 1)
        self.assertFalse(self.manager.buffs)
        self.assertFalse(device.component.instances)
        self.orisa_hits_tracer(11)
