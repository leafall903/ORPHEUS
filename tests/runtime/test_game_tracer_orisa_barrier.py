"""Actual Orisa Protective Barrier and Tracer primary fire against its mesh."""

import os
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, orisa_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "verified external Orisa mesh required")
class TracerOrisaBarrierTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.orisa, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[orisa_barrier.HERO]))

    def launch(self):
        x, y, z = self.tracer.mover.position
        self.move(self.orisa, (x, y, z + 6))
        for _ in range(20):
            self.run.step({})
        self.run.step({self.orisa: (0, 0, 16)})
        for _ in range(20):
            rocket = next((r for r in self.fight.rockets if r.orisa_deploy_body == self.orisa.body), None)
            if rocket is not None:
                self.assertFalse(self.fight.orisa_barriers.active)
                return rocket
            self.run.step({})
        self.fail("Protective Barrier did not launch")

    def deploy(self):
        self.launch()
        for _ in range(120):
            self.run.step({})
            if self.fight.orisa_barriers.active:
                break
        self.barrier, = self.fight.orisa_barriers.active.values()
        self.assertEqual(self.barrier.health, 600)
        self.assertEqual(self.barrier.script.graph.index, 0x0BD8)
        self.assertEqual(self.barrier.component.entity_var(567).value(), 20)
        x, y, z = self.barrier.position
        self.move(self.tracer, (x, y, z + 5))
        self.move(self.orisa, (x, y, z - 2))
        for _ in range(8):
            self.run.step({})
        self.vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.orisa))

    @staticmethod
    def move(player, position):
        player.mover.position = list(position)
        player.mover.body = None

    def shoot(self):
        x, y, z = self.orisa.mover.position
        self.run.step({self.tracer: (*aim(self.fight._eye(self.tracer), (x, y + 1, z)), FIRE)})

    def test_actual_pistols_damage_deployed_mesh_and_mirror_health_to_script(self):
        self.deploy()
        before = snapshot_digest(export_component_vm(self.barrier.component))
        self.shoot()
        self.assertEqual(self.barrier.health, 588)
        self.assertEqual(self.barrier.component.entity_var(253).value(), 588)
        self.assertEqual(self.vitals.total, 450)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
        self.assertNotEqual(snapshot_digest(export_component_vm(self.barrier.component)), before)

    def test_melee_reaches_the_mesh_without_hitting_the_distant_owner(self):
        self.deploy()
        x, y, z = self.barrier.position
        self.move(self.tracer, (x, y, z + 2))
        for _ in range(5):
            self.run.step({})
        self.run.step({self.tracer: (-32768, 0, 0x800)})
        for _ in range(40):
            self.run.step({self.tracer: (-32768, 0, 0)})
        self.assertEqual(self.barrier.health, 570)
        self.assertEqual(self.vitals.total, 450)

    def test_blink_crosses_enemy_mesh_and_primary_hits_from_behind(self):
        self.deploy()
        x, y, z = self.barrier.position
        self.move(self.orisa, (x + 2, y, z - 2))
        for _ in range(5):
            self.run.step({})
        self.run.step({self.tracer: (-32768, 0, 8)})
        self.assertLess(self.tracer.mover.position[2], z - 1)
        self.assertEqual(self.barrier.health, 600)
        self.assertEqual(self.vitals.total, 450)
        self.shoot()
        self.assertEqual(self.vitals.total, 444)
        self.assertEqual(self.barrier.health, 600)

    def test_pulse_attaches_to_orisa_mesh_and_damages_it_once_at_detonation(self):
        for capacity in (600, 350):
            with self.subTest(capacity=capacity):
                self.setUp()
                self.deploy()
                self.fight.orisa_barriers.damage(self.barrier, 600 - capacity)
                bomb = self.throw_pulse_at_barrier()
                self.assertEqual(bomb.attached_orisa, self.barrier.entity)
                self.assertEqual(self.barrier.health, capacity - 5)
                self.assertEqual(self.vitals.total, 450)
                for _ in range(70):
                    self.run.step({})
                self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
                self.assertEqual(self.barrier.health, max(0, capacity - 355))
                self.assertEqual(self.vitals.total, 450)

    def test_first_shield_takes_pulse_contact_and_blast_without_spill_to_second(self):
        for bubble_first in (False, True):
            for breaks in (False, True):
                with self.subTest(bubble_first=bubble_first, breaks=breaks):
                    self.setUp()
                    self.check_stacked_pulse(bubble_first, breaks)

    def check_stacked_pulse(self, bubble_first, breaks):
        bubble = self.deploy_stacked_barriers(bubble_first)
        if breaks:
            if bubble_first:
                bubble.damage(350)
            else:
                self.fight.orisa_barriers.damage(self.barrier, 250)
        orisa_before, bubble_before = self.barrier.health, bubble.health
        bomb = self.throw_pulse_at_barrier()
        self.assertEqual(bomb.attached_orisa, None if bubble_first else self.barrier.entity)
        self.assertIs(bomb.attached_bubble, bubble if bubble_first else None)
        self.assertEqual(self.barrier.health, orisa_before if bubble_first else orisa_before - 5)
        self.assertEqual(bubble.health, bubble_before - 5 if bubble_first else bubble_before)

        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(self.barrier.health, orisa_before if bubble_first else max(0, orisa_before - 355))
        self.assertEqual(bubble.health, max(0, bubble_before - 355) if bubble_first else bubble_before)
        self.assertEqual(self.vitals.total, 450)

    def deploy_stacked_barriers(self, bubble_first):
        self.deploy()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.orisa.team = self.barrier.team
        self.tracer.team = 1 - self.orisa.team
        owner, _ = support_tests.PulseDeflectTests().support(
            self.run, self.orisa, hero=0x02E0000000000009,
        )
        owner.team = self.orisa.team
        x, y, z = self.barrier.position
        self.move(owner, (x, y, z - (3.5 if bubble_first else 6.5)))
        for _ in range(20):
            self.run.step({})
        return bubble_tests.TracerWinstonBarrierTests().cast(self.run, owner)

    def test_destroying_outer_shield_before_detonation_exposes_inner_shield(self):
        for bubble_first in (False, True):
            with self.subTest(bubble_first=bubble_first):
                self.setUp()
                bubble = self.deploy_stacked_barriers(bubble_first)
                outer, inner = (bubble, self.barrier) if bubble_first else (self.barrier, bubble)
                if bubble_first:
                    bubble.damage(689)
                else:
                    self.fight.orisa_barriers.damage(self.barrier, 589)
                bomb = self.throw_pulse_at_barrier()
                self.assertEqual(outer.health, 6)
                for _ in range(16):
                    self.run.step({})
                position, fuse = bomb.position, bomb.fuse_left
                self.shoot()
                self.assertEqual(outer.health, 0)
                after_shot = inner.health
                self.run.step({})
                self.assertIsNone(bomb.attached_bubble)
                self.assertIsNone(bomb.attached_orisa)
                self.assertEqual(bomb.position, position)
                self.assertAlmostEqual(bomb.fuse_left, fuse - 2 * combat.TICK)
                for _ in range(70):
                    self.run.step({})
                self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
                self.assertEqual(inner.health, after_shot - 350)

    def test_contact_break_detaches_pulse_at_its_anchor_without_resetting_fuse(self):
        self.deploy()
        self.fight.orisa_barriers.damage(self.barrier, 595)
        bomb = self.throw_pulse_at_barrier()
        self.assertEqual(self.barrier.health, 0)
        self.assertEqual(self.vitals.total, 450)
        anchor, fuse = bomb.position, bomb.fuse_left
        self.assertIsNone(bomb.attached_orisa)
        self.assertGreater(fuse, 0.98)
        self.run.step({})
        self.assertIsNone(bomb.attached_orisa)
        self.assertEqual(bomb.position, anchor)
        self.assertAlmostEqual(bomb.fuse_left, fuse - combat.TICK)
        for _ in range(70):
            self.run.step({})
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertLess(self.vitals.total, 450)

    def test_ground_pulse_damages_mesh_and_nearer_wall_occludes_the_blast(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                self.setUp()
                self.deploy()
                if cover:
                    x, y, z = self.barrier.position
                    world = World(box(x - 15, x + 15, y - 2, y, z - 15, z + 15)
                                  + box(x - 8, x + 8, y, y + 4, z + 1.8, z + 1.9))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                bomb = self.throw_pulse_at_barrier(pitch=0 if cover else 8192)
                self.assertIsNone(bomb.attached_orisa)
                self.assertEqual(self.barrier.health, 600)
                for _ in range(70):
                    self.run.step({})
                self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
                self.assertEqual(self.barrier.health, 600 if cover else 250)
                self.assertEqual(self.vitals.total, 450)

    def throw_pulse_at_barrier(self, *, pitch=0):
        x, y, z = self.barrier.position
        self.move(self.tracer, (x, y, z + 3))
        self.run.step({self.tracer: (-32768, 0, 0)})
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (-32768, pitch, 32)})
        for _ in range(55):
            for bomb in self.fight.pulse_bombs.bombs:
                if bomb.stuck:
                    self.assertIsNone(bomb.attached)
                    return bomb
            self.run.step({self.tracer: (-32768, pitch, 0)})
        self.fail("Pulse Bomb did not find a contact")

    def test_orisa_can_fire_through_own_barrier_and_hit_tracer(self):
        self.deploy()
        x, y, z = self.tracer.mover.position
        health = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.tracer))
        facing = aim(self.fight._eye(self.orisa), (x, y + 1, z))
        self.run.step({self.orisa: (*facing, FIRE)})
        for _ in range(25):
            if health.total < 150:
                break
            self.run.step({})
        self.assertEqual(health.total, 139)
        self.assertEqual(self.barrier.health, 600)

    def test_earthshatter_breaks_barrier_without_reaching_tracer(self):
        self.deploy()
        caster, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E0000000000007,
        )
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.orisa.team = self.barrier.team
        caster.team = 1 - self.barrier.team
        x, y, z = self.barrier.position
        self.move(caster, (x, y, z + 6))
        self.move(self.tracer, (x, y, z - 3))
        self.move(self.orisa, (x + 7, y, z - 2))
        health = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.tracer))
        self.fight.orisa_barriers.damage(self.barrier, 560)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        wave = None
        for frame in range(78):
            self.run.step({caster: (-32768, 0, 32 if frame == 0 else 0)})
            wave = self.fight.earthshatters.waves.get(caster.body) or wave
        self.assertIsNotNone(wave)
        self.assertIn(self.barrier.entity, wave.hit_barriers)
        self.assertEqual(self.barrier.health, 0)
        self.assertNotIn(self.barrier.entity, self.fight.orisa_barriers.active)
        self.assertEqual(health.total, 150)
        self.assertFalse(self.fight.is_stunned(self.tracer))

    def test_deployed_barrier_survives_owner_switch_and_expires(self):
        self.deploy()
        manager = self.fight.orisa_barriers
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[SOLDIER]))
        before = self.barrier.until - 1
        self.match.update(before * combat.TICK, before)
        self.assertIn(self.barrier.entity, manager.active)
        for tick in (self.barrier.until + 2, self.barrier.until + 400):
            self.match.update(tick * combat.TICK, tick)
        self.assertNotIn(self.barrier.entity, manager.active)
        self.assertNotIn(self.barrier.entity, manager.retiring)
        self.assertFalse(self.barrier.component.instances)

    def test_switch_retires_unlanded_protective_barrier(self):
        rocket = self.launch()
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[SOLDIER]))
        self.assertNotIn(rocket, self.fight.rockets)
        for _ in range(120):
            self.run.step({})
        self.assertFalse(self.fight.orisa_barriers.pending)
        self.assertFalse(self.fight.orisa_barriers.active)

    def test_breaking_first_pellet_does_not_prevent_second_from_reaching_orisa(self):
        self.deploy()
        self.fight.orisa_barriers.damage(self.barrier, 594)
        self.shoot()
        self.assertEqual(self.barrier.health, 0)
        self.assertNotIn(self.barrier.entity, self.fight.orisa_barriers.active)
        self.assertEqual(self.vitals.total, 447)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 1)
