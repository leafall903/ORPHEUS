"""Real active interception at resource-camera boundaries; native attachment open."""

import os
import unittest
from functools import partial

import test_soldier_absorb as absorb
import test_soldier_barrier as barrier
import test_soldier_deflect as reflect

from ow174.game import combat, mover, stats
from ow174.game.collision import World


@unittest.skipUnless(os.environ.get('ORPHEUS_VIEW_OFFSETS'), 'explicit FirstPerson catalog required')
class SoldierViewInterceptionTests(unittest.TestCase):
    def elevated_ray(self, run, soldier, defender, eye, offset, *, lateral=0):
        soldier.mover.position[:] = [lateral, 3 + eye + offset - 1.55, 0]
        defender.mover.position[:] = [0, 3, 6]
        for player in (soldier, defender):
            player.mover.velocity = (0, 0, 0)
            player.mover.body = None
        # Separate real support planes keep both bodies at their chosen heights
        # during finite Helix flight. They sit below the horizontal attack ray.
        triangles = []
        for y, low, high in ((soldier.mover.position[1], -2, 1), (3, 5, 8)):
            triangles.extend([(-3, y, low, -3, y, high, 3, y, high),
                              (-3, y, low, 3, y, high, 3, y, low)])
        run.match.collision_world = World(triangles)

    def attack(self, run, step, weapon):
        step(2 if weapon == 'helix' else 1)
        if weapon == 'helix':
            self.assertTrue(run.match.combat.rockets)
            for _ in range(150):
                if not run.match.combat.rockets:
                    break
                step(0)
            self.assertFalse(run.match.combat.rockets)

    def assert_body_miss(self, run, soldier, defender):
        descriptor = next(t for t in run.match.combat._targets() if t.owner is defender)
        a, b = descriptor.segment()
        self.assertIsNone(combat.ray_capsule(run.match.combat._eye(soldier), (0, 0, 1),
                                           a, b, descriptor.radius))

    def test_actual_genji_stances_move_authored_upper_deflect_boundary(self):
        fixture = reflect.SoldierDeflectTests()
        for weapon in ('rifle', 'helix'):
            for crouch, eye in ((False, 1.2), (True, .84)):
                for inside in (False, True):
                    with self.subTest(weapon=weapon, crouch=crouch, inside=inside):
                        run, soldier, genji = fixture.scene(distance=6)
                        held = 0x80 if crouch else 0
                        for _ in range(6):
                            fixture.step(run, soldier, genji, genji_buttons=held)
                        self.assertEqual(genji.mover.crouched, crouch)
                        self.assertAlmostEqual(fixture.target(run, genji).eye, eye)
                        self.assertTrue(fixture.state(genji).active)
                        #03A3 front upper localY=.75. Lateral offset misses the capsule.
                        self.elevated_ray(run, soldier, genji, eye, .75 + (-.04 if inside else .04),
                                          lateral=.65)
                        self.assert_body_miss(run, soldier, genji)
                        step = partial(fixture.step, run, soldier, genji, genji_buttons=held)
                        self.attack(run, step, weapon)
                        amount = (120 if weapon == 'helix' else 40) if inside else 0
                        self.assertEqual(fixture.pool(run, genji).total, 200)
                        self.assertEqual(fixture.pool(run, soldier).total, 200 - amount)
                        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), amount)

    def test_actual_sigma_stances_move_authored_upper_grasp_boundary(self):
        if not os.environ.get('ORPHEUS_PROJECTILE_TAGS'):
            self.skipTest('explicit projectile tags required')
        fixture = absorb.SoldierAbsorbTests()
        for weapon in ('rifle', 'helix'):
            for crouch, eye in ((False, 2.1), (True, 1.75)):
                for inside in (False, True):
                    with self.subTest(weapon=weapon, crouch=crouch, inside=inside):
                        run, soldier, sigma = fixture.scene()
                        held = 0x80 if crouch else 0
                        for _ in range(6):
                            fixture.step(run, soldier, sigma, sigma_buttons=held)
                        self.assertEqual(sigma.mover.crouched, crouch)
                        self.assertAlmostEqual(run.match.combat._eye(sigma)[1], eye)
                        self.assertTrue(fixture.state(sigma).active)
                        #1D02 upper localY=1.1; body-only admission misses this ray.
                        self.elevated_ray(run, soldier, sigma, eye, 1.1 + (-.04 if inside else .04))
                        self.assert_body_miss(run, soldier, sigma)
                        step = partial(fixture.step, run, soldier, sigma, sigma_buttons=held)
                        self.attack(run, step, weapon)
                        amount = (120 if weapon == 'helix' else 20) if inside else 0
                        self.assertEqual(fixture.instance(sigma).find_var(0, 21312).value(), amount)
                        self.assertEqual(fixture.pool(run, sigma).base_total, 400)
                        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_reinhardt_raised_mesh_uses_resource_eye_and_blocks_crouch_intent(self):
        if not os.environ.get('ORPHEUS_REINHARDT_COLLISION'):
            self.skipTest('explicit original shield mesh required')
        fixture = barrier.SoldierBarrierTests()
        for crouch in (False, True):
            with self.subTest(crouch=crouch):
                run, soldier, rein = fixture.scene()
                held = 2 | (0x80 if crouch else 0)
                for _ in range(6):
                    fixture.step(run, soldier, rein, held=held)
                self.assertFalse(rein.mover.crouched)
                modifiers = mover.statescript_mods(rein.body_script.component)
                self.assertTrue(any(v[0] == mover.CROUCH_INTENT and v[1:3] == (2, 0.0)
                                    for v in modifiers.values()))  #0095/state2 original SET21=0
                self.assertAlmostEqual(run.match.combat._eye(rein)[1], 1.8)
                self.elevated_ray(run, soldier, rein, 1.8, 0)
                fixture.step(run, soldier, rein, 1, held=held)
                self.assertEqual(fixture.shield(run, rein).current, 1580)
                self.assertEqual(fixture.pool(run, rein).total, 500)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
