"""Actual Sigma Accretion and Tracer commands over the reconstructed host."""

import unittest

import test_game_tracer as tracer_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import sigma_accretion, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.nodes import Ability


class TracerSigmaAccretionTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.sigma, _ = tracer_tests.TracerPistolTests().fight()
        self.fight = self.run.match.combat
        self.assertTrue(self.run.match.switch_hero(self.sigma, heroes()[sigma_accretion.HERO]))
        x, y, z = self.tracer.mover.position
        self.sigma.mover.position = [x, y, z + 10]
        self.sigma.mover.body = None
        for _ in range(30):
            self.run.step({})
        self.rock_aim = aim(self.fight._eye(self.sigma), (x, y + 0.7, z))
        self.pistol_aim = aim(self.fight._eye(self.tracer), (x, y + 1, z + 10))
        self.target = next(t for t in self.fight._targets() if t.owner is self.tracer)
        self.vitals = self.fight.vitals_of(self.target)

    def step(self, *, rock=0, tracer_buttons=0):
        self.run.step({self.sigma: (*self.rock_aim, rock), self.tracer: (*self.pistol_aim, tracer_buttons)})

    def launch(self, tracer_buttons=0):
        self.step(rock=16, tracer_buttons=tracer_buttons)
        for _ in range(55):
            if self.fight.rockets:
                return self.fight.rockets[0]
            self.step(tracer_buttons=tracer_buttons)
        self.fail("Accretion did not launch")

    def test_actual_motion_damage_interrupt_and_blink_recovers_at_stun_expiry(self):
        rocket = self.launch(FIRE)
        self.assertEqual(rocket.gravity, 18)
        initial_velocity = rocket.velocity
        self.step(tracer_buttons=FIRE)
        self.assertAlmostEqual(rocket.velocity[1] - initial_velocity[1], -18 * 0.016)
        for _ in range(30):
            if self.fight.is_stunned(self.tracer):
                break
            self.step(tracer_buttons=FIRE)
        self.assertEqual(self.vitals.total, 80)
        deadline = self.fight.stuns[self.tracer.body]
        self.assertEqual(deadline - self.fight.tick, 50)
        component = self.tracer.body_script.component
        blink = next(i for i in component.instances.values() if i.graph.index == 0x24)
        self.assertFalse(component.held)
        mover = self.tracer.mover
        impact_z = mover.position[2]
        self.assertTrue(mover.external_impulse)
        self.assertLess(mover.velocity[2], -4.5)
        while self.fight.tick < deadline - 1:
            self.step(tracer_buttons=FIRE | 8)
            self.assertFalse(component.held)
            self.assertEqual(blink.vars[11].value(), 3)
        self.step()
        self.assertLess(mover.position[2], impact_z - 1)
        self.assertFalse(mover.external_impulse)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.step(tracer_buttons=8)
        self.assertEqual(blink.vars[11].value(), 2)

    def test_actual_recall_at_impact_rejects_damage_and_control(self):
        self.launch()
        self.step(tracer_buttons=16)
        for _ in range(30):
            if not self.fight.rockets:
                break
            self.step()
        self.assertFalse(self.fight.rockets)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.vitals.total, 150)
        self.assertNotIn(self.tracer.body, self.fight.stuns)
        self.assertFalse(self.tracer.mover.external_impulse)

    def test_switching_caster_body_retires_the_live_rock(self):
        self.launch()
        self.assertTrue(self.run.match.switch_hero(self.sigma, heroes()[0x02E000000000006E]))
        self.assertFalse(self.fight.rockets)
        for _ in range(25):
            self.step()
        self.assertEqual(self.vitals.total, 150)
        self.assertNotIn(self.tracer.body, self.fight.stuns)

    def test_early_recast_is_rejected_and_cooldown_allows_a_second_hit(self):
        instance = next(i for i in self.sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_accretion.ACCRETION)
        ability = next(state for state in instance.states.values() if isinstance(state, Ability))
        self.launch()
        for _ in range(40):
            self.step()
        self.assertEqual(self.vitals.total, 80)
        self.assertGreater(ability.remaining(), 9)
        self.step(rock=16)
        for _ in range(60):
            self.step()
            self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals.total, 80)
        for _ in range(630):
            if ability.remaining() == 0:
                break
            self.step()
        self.assertEqual(ability.remaining(), 0)
        x, y, z = self.tracer.mover.position
        self.rock_aim = aim(self.fight._eye(self.sigma), (x, y + 0.7, z))
        self.launch()
        for _ in range(40):
            if not self.fight.rockets:
                break
            self.step()
        self.assertEqual(self.vitals.total, 10)
        self.assertTrue(self.fight.is_stunned(self.tracer))

    def wall(self, x, y, z0, z1):
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        self.run.match.collision_world = World(floor + box(x - 4, x + 4, y, y + 4, z0, z1))
        for player in self.run.players:
            player.mover.use(world=self.run.match.collision_world)

    def test_wall_splash_pushes_tracer_without_direct_stun(self):
        x, y, z = self.tracer.mover.position
        self.tracer.mover.position[0] += 1.2
        self.tracer.mover.body = None
        self.wall(x, y, z - 1.8, z - 0.8)
        self.rock_aim = (-32768, 0)
        self.launch()
        for _ in range(50):
            if not self.fight.rockets:
                break
            self.step()
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals.total, 110)
        self.assertNotIn(self.tracer.body, self.fight.stuns)
        self.assertGreater(self.tracer.mover.velocity[0], 0)
        self.assertGreater(self.tracer.mover.velocity[2], 0)

    def test_static_wall_clips_direct_knockback(self):
        x, y, z = self.tracer.mover.position
        self.wall(x, y, z - 2, z - 1)
        self.launch()
        furthest = z
        for _ in range(90):
            self.step()
            furthest = min(furthest, self.tracer.mover.position[2])
        self.assertEqual(self.vitals.total, 80)
        self.assertGreater(z - furthest, 0.3)
        self.assertLess(z - furthest, 1)
        self.assertFalse(self.tracer.mover.external_impulse)
