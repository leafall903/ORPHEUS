"""Actual Tracer inputs against Sigma's 1B55 Experimental Barrier."""
import os
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim

from ow174.game import combat, sigma_barrier, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "verified external C015 model required")
class TracerSigmaBarrierTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.sigma, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.sigma, heroes()[sigma_barrier.HERO]))
        x, y, z = self.tracer.mover.position
        self.move(self.sigma, (x, y, z + 10))
        self.step(30)
        self.step(8, SECONDARY)
        actor, = self.fight.sigma_barriers.values()
        moving = actor.position
        self.step(27)
        self.barrier = actor
        self.assertNotEqual(actor.position, moving)
        self.assertEqual(actor.phase, 3)
        self.assertTrue(actor.active and actor.child_request_seen)
        self.assertEqual(actor.vitals.total, 700)
        target = next(t for t in self.fight._targets() if t.owner is self.sigma)
        self.vitals = self.fight.vitals_of(target)

    @staticmethod
    def move(player, position):
        player.mover.position = list(position)
        player.mover.body = None

    def step(self, frames, buttons=0):
        for _ in range(frames):
            self.run.step({self.sigma: (-32768, 0, buttons)})

    def fire(self, buttons=FIRE):
        x, y, z = self.sigma.mover.position
        yaw, pitch = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        self.run.step({self.tracer: (yaw, pitch, buttons), self.sigma: (-32768, 0, 0)})

    def test_fire_strike_crosses_projected_barrier_before_hitting_tracer(self):
        caster, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E0000000000007,
        )
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.sigma.team = 0
        caster.team = 1
        x, y, _ = self.tracer.mover.position
        barrier_z = self.barrier.position[2]
        self.move(caster, (x, y, barrier_z - 6))
        self.move(self.tracer, (x, y, barrier_z + 3))
        target = next(t for t in self.fight._targets() if t.owner is self.tracer)
        health = self.fight.vitals_of(target)
        for frame in range(34):
            self.run.step({caster: (0, 0, 16 if frame == 0 else 0)})
        strike, = self.fight.rockets
        self.assertEqual(strike.definition, combat.FIRE_STRIKE)
        self.step(55)
        self.assertEqual(self.barrier.vitals.total, 600)
        self.assertEqual(strike.hit_barriers, {self.barrier.entity})
        self.assertIn(self.tracer.body, strike.hit_entities)
        self.assertEqual(health.total, 50)

    def test_earthshatter_stops_at_projected_barrier_before_tracer(self):
        caster, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E0000000000007,
        )
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.sigma.team = 0
        caster.team = 1
        x, y, _ = self.tracer.mover.position
        barrier_z = self.barrier.position[2]
        self.move(caster, (x, y, barrier_z - 6))
        self.move(self.tracer, (x, y, barrier_z + 3))
        target = next(t for t in self.fight._targets() if t.owner is self.tracer)
        health = self.fight.vitals_of(target)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(72):
            self.run.step({caster: (0, 0, 32 if frame == 0 else 0)})
        self.assertEqual(self.barrier.vitals.total, 650)
        self.assertEqual(health.total, 150)
        self.assertFalse(self.fight.is_stunned(self.tracer))

    def test_earthshatter_meets_sigma_barrier_while_it_is_projecting(self):
        for sigma_offset in (4.6, 5.0, 5.3, 5.4):
            with self.subTest(sigma_offset=sigma_offset):
                self.setUp()
                self.step(1, SECONDARY)
                self.step(160)
                self.assertFalse(self.barrier.active)
                caster, _ = support_tests.PulseDeflectTests().support(
                    self.run, self.tracer, hero=0x02E0000000000007,
                )
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.tracer.team = self.sigma.team = 0
                caster.team = 1
                x, y, z = self.tracer.mover.position
                self.move(caster, (x, y, z - 11))
                self.move(self.sigma, (x, y, z + sigma_offset))
                target = next(t for t in self.fight._targets() if t.owner is self.tracer)
                health = self.fight.vitals_of(target)
                script = caster.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                previous_position = None
                for frame in range(75):
                    self.run.step({
                        caster: (0, 0, 32 if frame == 0 else 0),
                        self.sigma: (-32768, 0, SECONDARY if frame >= 22 else 0),
                    })
                    if self.barrier.vitals.total < 700:
                        break
                    if self.barrier.phase == 2:
                        previous_position = self.barrier.position
                self.assertEqual(self.barrier.phase, 2)
                self.assertIsNotNone(previous_position)
                self.assertLess(self.barrier.position[2], previous_position[2])
                self.assertLess(caster.mover.position[2], self.barrier.position[2])
                self.assertLess(self.barrier.position[2], z)
                self.assertEqual(self.barrier.vitals.total, 650)
                wave = self.fight.earthshatters.waves[caster.body]
                self.assertIn(self.barrier.entity, wave.hit_barriers)
                self.assertNotIn(self.tracer.body, wave.hit_bodies)
                for _ in range(45):
                    self.step(1, SECONDARY)
                self.assertIsNone(wave.fronts[len(wave.fronts) // 2])
                self.assertNotIn(caster.body, self.fight.earthshatters.waves)
                self.assertEqual(self.barrier.vitals.total, 650)
                self.assertEqual(health.total, 150)
                self.assertFalse(self.fight.is_stunned(self.tracer))

    def test_pistols_hit_mesh_and_recall_regenerates_then_redeploys(self):
        self.assertEqual(len(self.barrier.geometry.triangles), 120)
        self.fire()
        self.assertEqual(self.barrier.vitals.total, 688)
        self.assertEqual(self.vitals.total, 400)
        self.assertEqual(self.tracer.stats.value(stats.HERO_DAMAGE), 0)
        self.step(14)
        self.step(1, SECONDARY)
        self.step(2)
        self.assertFalse(self.barrier.active)
        self.assertGreater(self.barrier.recharge_at, self.fight.now)
        ability = self.barrier.instance.states[111]
        self.assertGreater(ability.remaining(), 1.9)
        self.step(1, SECONDARY)
        self.step(2)
        self.assertFalse(self.barrier.active)
        self.fire()
        self.assertEqual(self.vitals.total, 388)
        self.step(160)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(self.barrier.vitals.total, 700)
        self.step(8, SECONDARY)
        self.step(27)
        self.assertTrue(self.barrier.active)
        self.fire()
        self.assertEqual(self.barrier.vitals.total, 688)
        self.assertEqual(self.vitals.total, 388)

    def test_breaking_pellet_stops_but_second_pellet_reaches_owner(self):
        self.barrier.vitals.now[combat.HEALTH_POOL] = 6
        self.fire()
        self.assertEqual(self.barrier.vitals.total, 0)
        self.assertFalse(self.barrier.active)
        self.assertEqual(self.vitals.total, 394)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 1)
        self.step(3)
        self.assertGreater(self.barrier.instance.states[111].remaining(), 0)

    def test_melee_reaches_projected_mesh_and_leaves_distant_owner_untouched(self):
        x, _, z = self.barrier.position
        self.move(self.tracer, (x, self.sigma.mover.position[1], z - 2))
        self.step(1)
        self.fire(0x800)
        self.step(40)
        self.assertEqual(self.barrier.vitals.total, 670)
        self.assertEqual(self.vitals.total, 400)

    def test_pulse_contacts_projected_mesh_and_blast_damages_barrier_once(self):
        x, _, z = self.barrier.position
        self.move(self.tracer, (x, self.sigma.mover.position[1], z - 3))
        self.step(1)
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        bombs = self.fight.pulse_bombs.bombs
        self.run.step({self.tracer: (0, 0, 32), self.sigma: (-32768, 0, 0)})
        for _ in range(55):
            if bombs and bombs[0].stuck:
                break
            self.step(1)
        bomb, = bombs
        self.assertEqual(bomb.attached_projected, self.barrier.entity)
        self.assertEqual(self.barrier.vitals.total, 695)
        self.assertEqual(self.vitals.total, 400)
        self.step(70)
        self.assertFalse(bombs)
        self.assertEqual(self.barrier.vitals.total, 345)
        self.assertEqual(self.vitals.total, 400)
