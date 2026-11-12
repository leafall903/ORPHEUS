"""Actual Hyperspheres inputs and the target's Tracer responses."""

import unittest
from dataclasses import replace

import test_game_tracer_dva_matrix as matrix_tests
import test_game_tracer_pulse_deflect as pulse_tests
import test_game_tracer_sigma_accretion as accretion_tests
from test_game_combat import aim

from ow174.game import combat, tracer
from ow174.game.commands import FIRE
from ow174.game.content import find_hero, heroes


class TracerSigmaHypersphereTests(unittest.TestCase):
    def setUp(self):
        accretion_tests.TracerSigmaAccretionTests.setUp(self)

    def place(self, distance, offset=0):
        x, y, z = self.tracer.mover.position
        self.sigma.mover.position = [x - offset, y, z + distance]
        self.sigma.mover.body = None
        self.run.step({})
        self.shot_aim = aim(self.fight._eye(self.sigma), (x - offset, y + 1, z))

    def step(self, *, fire=0, buttons=0, tracer_yaw=0):
        self.run.step({self.sigma: (*self.shot_aim, fire), self.tracer: (tracer_yaw, 0, buttons)})

    def pair(self):
        self.step(fire=FIRE)
        rounds = {}
        for _ in range(100):
            for rocket in self.fight.rockets:
                rounds[id(rocket)] = rocket
            self.step()
        self.assertEqual(len(rounds), 2)
        self.assertFalse(self.fight.rockets)
        return tuple(rounds.values())

    def launch_first(self):
        self.step(fire=FIRE)
        for _ in range(40):
            if self.fight.rockets:
                return self.fight.rockets[0]
            self.step()
        self.fail("Hypersphere did not launch")

    def test_actual_pair_direct_hit_applies_damage_and_splash_pull(self):
        self.place(6)
        before = self.tracer.mover.position[2]
        self.pair()
        self.assertEqual(self.vitals.total, 40)
        self.assertGreater(self.tracer.mover.position[2], before + 0.05)

    def test_both_spheres_expire_and_airburst_once_each(self):
        self.place(22, 1.2)
        rockets = self.pair()
        self.assertEqual(self.vitals.total, 90)
        for rocket in rockets:
            self.assertEqual(rocket.gravity, 5.5)
            self.assertAlmostEqual(rocket.body_radius, 0.2)
            self.assertAlmostEqual(rocket.wall_radius, 0.1)
            self.assertAlmostEqual(rocket.flown, 0.44)
            self.assertAlmostEqual(rocket.left, 0)
        self.assertEqual(len(self.sigma.client.messages(combat.HIT)), 2)
        for _ in range(30):
            self.step()
        self.assertEqual(self.vitals.total, 90)

    def test_static_wall_reflects_both_spheres_and_protects_tracer(self):
        self.place(6)
        x, y, z = self.tracer.mover.position
        accretion_tests.TracerSigmaAccretionTests.wall(self, x, y, z + 2.5, z + 3)
        self.step(fire=FIRE)
        reflected = {}
        for _ in range(100):
            for rocket in self.fight.rockets:
                if rocket.direction[2] > 0:
                    reflected[id(rocket)] = rocket
            self.step()
        self.assertEqual(len(reflected), 2)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals.total, 150)
        for rocket in reflected.values():
            self.assertLess(rocket.bounces, 100)
            self.assertAlmostEqual(rocket.flown, 0.44)

    def test_recall_rejects_incoming_pair_during_active_rewind(self):
        self.place(12)
        self.launch_first()
        self.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        saw_second = False
        first = self.fight.rockets[0]
        for _ in range(75):
            saw_second |= any(rocket is not first for rocket in self.fight.rockets)
            self.assertEqual(self.vitals.total, 150)
            self.step()
        self.assertTrue(saw_second)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.sigma.client.messages(combat.HIT))

    def test_blink_leaves_both_expiry_blasts_out_of_range(self):
        self.place(22, 1.2)
        self.launch_first()
        before = self.tracer.mover.position[0]
        self.step(buttons=8, tracer_yaw=16384)
        self.assertGreater(self.tracer.mover.position[0], before + 6)
        for _ in range(80):
            self.step()
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals.total, 150)

    def test_allied_tracer_receives_no_contact_or_airburst_damage(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team = self.sigma.team
        self.place(6)
        self.pair()
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.tracer.mover.external_impulse)

    def test_body_replacement_retires_live_sphere_without_burst(self):
        self.place(22, 1.2)
        self.launch_first()
        self.assertTrue(self.run.match.switch_hero(self.sigma, heroes()[0x02E000000000006E]))
        self.assertFalse(self.fight.rockets)
        for _ in range(55):
            self.step()
        self.assertEqual(self.vitals.total, 150)

    def test_breaking_sphere_does_not_damage_tracer_through_allied_winston_bubble(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.sigma.team = 0, 1
        self.place(12)
        winston, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        winston.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        winston.mover.position = [x, y, z - 4]
        winston.mover.body = None
        for _ in range(25):
            self.step()
        self.run.step({winston: (0, 0, 16)})
        for _ in range(4):
            self.step()
        barrier = self.fight.winston_barriers.actors[winston.body]
        self.assertEqual(barrier.health, 700)
        barrier.health = 55
        self.step(fire=FIRE)
        for _ in range(65):
            if barrier.health == 0:
                break
            self.step()
        self.assertEqual(barrier.health, 0)
        self.assertEqual(self.vitals.total, 150)
        for _ in range(45):
            self.step()
        self.assertEqual(self.vitals.total, 95)
        self.assertEqual(len(self.sigma.client.messages(combat.HIT)), 2)

    def test_allied_grasp_absorbs_both_spheres_before_they_reach_tracer(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.sigma.team = 0, 1
        self.place(12)
        ally, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer, hero=self.sigma.hero.guid)
        ally.team = self.tracer.team
        for _ in range(25):
            self.step()
        self.run.step({ally: (0, 0, 8)})
        grasp = next(i for i in ally.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.states[1].active)
        self.step(fire=FIRE)
        for _ in range(95):
            self.step()
        self.assertFalse(self.fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 110)
        self.assertEqual(self.vitals.total, 150)

    def test_genji_reflects_sphere_into_tracer_with_new_owner_attribution(self):
        self.place(4)
        x, y, z = self.tracer.mover.position
        genji, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000029)
        genji.mover.position = [x, y, z - 4]
        genji.mover.body = None
        self.tracer.mover.position = [x + 2, y, z]
        self.tracer.mover.body = None
        for _ in range(25):
            self.step()
        self.shot_aim = aim(self.fight._eye(self.sigma), (x, y + 1, z - 4))
        deflect_aim = aim(self.fight._eye(genji), (x + 2, y + 1, z))
        self.run.step({genji: (*deflect_aim, 16)})
        self.run.step({self.sigma: (*self.shot_aim, FIRE), genji: (*deflect_aim, 16)})
        returned = []
        for _ in range(95):
            for rocket in self.fight.rockets:
                if rocket.shooter is genji and all(rocket is not item for item in returned):
                    self.assertEqual(rocket.sphere_owner, genji.body)
                    self.assertLess(rocket.left, 0.44)
                    self.assertIsNone(rocket.shot)
                    returned.append(rocket)
            self.run.step({self.sigma: (*self.shot_aim, 0), genji: (*deflect_aim, 16)})
        self.assertEqual(len(returned), 2)
        self.assertEqual(self.vitals.total, 40)
        self.assertFalse(self.sigma.client.messages(combat.HIT))
        self.assertEqual(len(genji.client.messages(combat.HIT)), 4)

    def test_allied_matrix_absorbs_both_spheres_before_they_reach_tracer(self):
        matrix_tests.TracerDvaMatrixTests.setUpClass()
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.sigma.team = 0, 1
        self.place(12)
        mech, _ = pulse_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E000000000007A)
        self.assertTrue(self.run.match.switch_hero(mech, replace(find_hero("dva"), body=0x0400000000000407)))
        mech.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        mech.mover.position = [x, y, z + 3]
        mech.mover.body = None
        for _ in range(110):
            self.step()
        target = next(t for t in self.fight._targets() if t.owner is mech)
        health = self.fight.vitals_of(target)
        for _ in range(6):
            self.run.step({mech: (0, 0, 2)})
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        self.run.step({self.sigma: (*self.shot_aim, FIRE), mech: (0, 0, 2)})
        seen = {}
        for _ in range(65):
            for rocket in self.fight.rockets:
                seen[id(rocket)] = rocket
            self.run.step({self.sigma: (*self.shot_aim, 0), mech: (0, 0, 2)})
        self.assertEqual(len(seen), 2)
        self.assertFalse(self.fight.rockets)
        for rocket in seen.values():
            self.assertLess(rocket.flown, 0.44)
        self.assertEqual(health.total, 600)
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.sigma.client.messages(combat.HIT))
