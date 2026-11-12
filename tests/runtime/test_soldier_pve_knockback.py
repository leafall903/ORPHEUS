"""Actual-five-NPC displacement through a reconstructed external-force mover.

The adapter's trajectories execute; native boss resistance/immunity is still open.
"""

import unittest

import test_soldier_pve_static as fixture
from test_game_combat import aim

from ow174.game import combat, defenses, npc
from ow174.game.collision import MOVER, World, flat_world
from ow174.game.commands import Command


class SoldierPveKnockbackTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)
    vitals = fixture.SoldierPveStaticTests.vitals

    def arena(self, key, **kwargs):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key, **kwargs)
        soldier.mover.position = list(soldier.mover.position)
        soldier.mover.body = None
        soldier.body_script.ult_seconds = 0
        return run, soldier, actor

    def step(self, run, soldier, buttons=0, *, point=None):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        yaw, pitch = (0, 0) if point is None else aim(run.match.combat._eye(soldier), point)
        soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons)])

    def backstop(self, run, z):
        run.match.collision_world = World([
            (-100, 0, -100, -100, 0, 100, 100, 0, 100),
            (-100, 0, -100, 100, 0, 100, 100, 0, -100),
            (-10, -10, z, 10, 10, z, 10, -10, z),
            (-10, -10, z, -10, 10, z, 10, 10, z),
        ])

    def test_actual_helix_uses_live_response_mods_for_all_five_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key)
                start = actor.position
                self.step(run, soldier, 2, point=(0, .8, 1))
                peak, moved, airborne_record = start[1], 0.0, False
                for _ in range(240):
                    self.step(run, soldier)
                    peak = max(peak, actor.position[1])
                    moved = max(moved, actor.position[2] - start[2])
                    airborne_record |= actor.movement(run.frame).air_ticks > 0
                if defenses.knockback_scale(actor) == 0:
                    self.assertEqual(actor.hero, 0x02E00000000001BA)
                    # Actual1254/state0 sets26=0; this is not a hero-name exception.
                    self.assertEqual(actor.position, start)
                    self.assertIsNone(actor.impulse_mover)
                    self.assertLess(self.vitals(run, actor).total, definition.health)
                    continue
                self.assertGreater(peak, start[1] + .2)
                self.assertGreater(moved, .3)
                self.assertTrue(airborne_record)
                self.assertIsNone(actor.impulse_mover)
                self.assertEqual(actor.velocity, (0, 0, 0))
                self.assertAlmostEqual(actor.position[1], 0, delta=.05)
                settled = actor.position
                for _ in range(100):
                    self.step(run, soldier)
                self.assertEqual(actor.position, settled)

    def test_heavy_zero_response_comes_from_actual_state_and_is_removed_with_it(self):
        for enabled in (True, False):
            with self.subTest(actual_receiver_state=enabled):
                run, soldier, actor = self.arena("5")
                state = next(i for i in actor.body_script.component.instances.values()
                             if i.graph.index == 0x1254).states[0]
                self.assertTrue(state.active)
                if not enabled:
                    state.end(False)  # isolated removal of the real authored receiver modifier
                self.assertEqual(defenses.knockback_scale(actor), 0 if enabled else 1)
                start = actor.position
                before = self.vitals(run, actor).total
                self.step(run, soldier, 2, point=(0, .8, 1))
                peak = start[1]
                for _ in range(120):
                    self.step(run, soldier)
                    peak = max(peak, actor.position[1])
                self.assertEqual(before - self.vitals(run, actor).total, 120)
                if enabled:
                    self.assertEqual(actor.position, start)
                    self.assertIsNone(actor.impulse_mover)
                else:
                    self.assertGreater(peak, start[1] + .2)
                    self.assertGreater(actor.position[2], start[2] + .3)

    def test_ally_and_wall_reject_displacement_for_every_actual_body(self):
        for key, definition in self.catalog.items():
            for case in ("ally", "wall"):
                with self.subTest(npc=definition.name, case=case):
                    run, soldier, actor = self.arena(key, team=0 if case == "ally" else 1)
                    if case == "wall":
                        run.match.collision_world = World([
                            (-100, 0, -100, -100, 0, 100, 100, 0, 100),
                            (-100, 0, -100, 100, 0, 100, 100, 0, -100),
                            (-10, -10, -1, 10, -10, -1, 10, 10, -1),
                            (-10, -10, -1, 10, 10, -1, -10, 10, -1),
                        ])
                    start = actor.position
                    self.step(run, soldier, 2, point=(0, .8, 1))
                    for _ in range(80):
                        self.step(run, soldier)
                    self.assertEqual(actor.position, start)
                    self.assertIsNone(actor.impulse_mover)

    def test_npc_external_motion_cleans_up_on_death_or_removal(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key)
                # The impulse API is isolated here to exercise collision and lifetime.
                # Actual authored Helix delivery is covered in the preceding method.
                actor.apply_impulse((4, 6, 0), run.frame, flat_world(0), category=MOVER)
                self.assertIsNotNone(actor.impulse_mover)
                self.step(run, soldier)
                self.assertGreater(actor.position[0], 0)
                target = next(t for t in run.match.combat._targets() if t.owner is actor)
                run.match.combat._damage(soldier, target, self.vitals(run, actor).total,
                                         False, None, run.frame)
                self.assertTrue(actor.dead)
                self.assertIsNone(actor.impulse_mover)
                self.assertEqual(actor.velocity, (0, 0, 0))
                position = actor.position
                for _ in range(20):
                    self.step(run, soldier)
                self.assertEqual(actor.position, position)
                self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))

    def test_each_real_capsule_stops_at_a_wall_during_external_motion(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key)
                run.match.collision_world = World([
                    (-100, 0, -100, -100, 0, 100, 100, 0, 100),
                    (-100, 0, -100, 100, 0, 100, 100, 0, -100),
                    (.9, -10, -10, .9, 10, 10, .9, 10, -10),
                    (.9, -10, -10, .9, -10, 10, .9, 10, 10),
                ])
                actor.apply_impulse((4, 6, 0), run.frame, run.match.collision_world, category=MOVER)
                radius = actor.impulse_mover.data.stand_radius
                maximum = actor.position[0]
                for _ in range(160):
                    self.step(run, soldier)
                    maximum = max(maximum, actor.position[0])
                self.assertGreater(maximum, .05)
                self.assertLessEqual(maximum, .9 - radius + .02)
                self.assertIsNone(actor.impulse_mover)

    def test_actual_helix_pushes_live_recipients_into_a_backstop(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key)
                start = actor.position
                wall_z = 1.67
                self.backstop(run, wall_z)
                before = self.vitals(run, actor).total
                self.step(run, soldier, 2, point=(0, .8, 1))
                maximum, radius = start[2], None
                for _ in range(240):
                    self.step(run, soldier)
                    maximum = max(maximum, actor.position[2])
                    if actor.impulse_mover is not None:
                        radius = actor.impulse_mover.data.stand_radius
                self.assertEqual(before - self.vitals(run, actor).total, 120)
                self.assertIsNone(actor.impulse_mover)
                self.assertEqual(actor.velocity, (0, 0, 0))
                self.assertAlmostEqual(actor.position[1], 0, delta=.05)
                if key == "5":
                    self.assertIsNone(radius)
                    self.assertEqual(actor.position, start)
                else:
                    self.assertIsNotNone(radius)
                    self.assertGreater(maximum, start[2] + .05)
                    self.assertAlmostEqual(maximum, wall_z - radius, delta=.03)
                settled = actor.position
                for _ in range(20):
                    self.step(run, soldier)
                self.assertEqual(actor.position, settled)

    def test_actual_melee_settles_at_backstop_and_hits_again_after_recovery(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                # The authored melee force is horizontal. Start off the ground
                # so ground friction cannot consume it before wall contact.
                run, soldier, actor = self.arena(key, position=(0, .75, -1.5))
                start = actor.position
                wall_z = -.85
                self.backstop(run, wall_z)
                pool = self.vitals(run, actor)
                before = pool.total
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                for _ in range(10):
                    self.step(run, soldier)
                self.step(run, soldier, 0x800)
                self.assertEqual(before - pool.total, 30)
                self.step(run, soldier)
                self.step(run, soldier, 0x800)
                maximum, radius = start[2], None
                for _ in range(90):
                    self.step(run, soldier)
                    maximum = max(maximum, actor.position[2])
                    if actor.impulse_mover is not None:
                        radius = actor.impulse_mover.data.stand_radius
                self.assertIsNone(actor.impulse_mover)
                self.assertEqual(actor.velocity, (0, 0, 0))
                if key == "5":
                    self.assertIsNone(radius)
                    self.assertEqual(actor.position, start)
                else:
                    self.assertIsNotNone(radius)
                    self.assertAlmostEqual(actor.position[1], 0, delta=.05)
                    self.assertGreater(maximum, start[2] + .05)
                    self.assertAlmostEqual(maximum, wall_z - radius, delta=.03)
                self.assertEqual(before - pool.total, 30)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)
                self.step(run, soldier, 0x800)
                self.assertEqual(before - pool.total, 60)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 2)
                self.assertEqual(ammo.value(), rounds)
