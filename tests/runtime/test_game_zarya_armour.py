"""Actual Zarya attacks against Reinhardt's authored300-health/200-armour body.

Beam/DOT policy follows Blizzard's2019-03-19 and2020-08-31 patch notes.
Pool-boundary integration and Graviton's impact classification remain host policy.
"""

import struct
import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.content import heroes


class ZaryaArmourTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Reinhardt", "BarrierCaster"))
        self.a, self.b, self.c = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[0x02E0000000000068])
        self.run.match.switch_hero(self.b, heroes()[0x02E0000000000007])
        self.run.match.switch_hero(self.c, heroes()[0x02E0000000000068])
        self.c.team = self.b.team
        self.a.mover.position[:] = [0, 1, 0]
        self.b.mover.position[:] = [0, 1, 5]
        self.c.mover.position[:] = [20, 1, 5]
        target = next(t for t in self.fight._targets() if t.owner is self.b)
        self.vitals = self.fight.vitals_of(target)
        self.assertEqual(self.vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(self.vitals.now[combat.ARMOUR_POOL], 200)

    def advance(self, frames, buttons=0):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, buttons)})

    def beam(self, energy, loss):
        self.a.body_script.component.entity_var(87).base = energy
        self.advance(51, 1)
        self.assertAlmostEqual(200 - self.vitals.now[combat.ARMOUR_POOL], loss, places=4)
        self.assertEqual(self.vitals.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(self.a.stats.values[combat.stats.HERO_DAMAGE], loss, places=4)
        self.assertAlmostEqual(self.b.body_script.component.entity_var(1732).value(),
                               500 - loss, places=3)
        armour_part = self.vitals.component({combat.ARMOUR_POOL})[51][16]
        self.assertAlmostEqual(struct.unpack_from("<f", armour_part, 8)[0], 200 - loss, places=3)

    def test_zero_energy_beam_loses_42_armour_instead_of_30(self):
        self.beam(0, 42)

    def test_50_energy_beam_loses_68_6_armour(self):
        self.beam(50, 68.6)

    def test_100_energy_beam_loses_95_2_armour(self):
        self.beam(100, 95.2)

    def test_secondary_retains_discrete_hit_reduction(self):
        self.advance(1, 2)
        self.advance(60)
        self.assertAlmostEqual(self.vitals.now[combat.ARMOUR_POOL], 158)
        self.assertEqual(self.vitals.now[combat.HEALTH_POOL], 300)

    def test_melee_retains_discrete_hit_reduction(self):
        self.b.mover.position[2] = 1.5
        self.advance(1, 0x800)
        self.advance(40)
        self.assertEqual(self.vitals.now[combat.ARMOUR_POOL], 175)
        self.assertEqual(self.vitals.now[combat.HEALTH_POOL], 300)

    def test_graviton_field_dot_loses_20_armour_without_mitigation(self):
        self.a.body_script.set_ult_charge(2100)
        self.advance(1, 0x20)
        for _ in range(100):
            if self.fight.zarya_gravitons.fields:
                break
            self.advance(1)
        self.assertTrue(self.fight.zarya_gravitons.fields)
        after_impact = self.vitals.now[combat.ARMOUR_POOL]
        self.advance(270)
        self.assertAlmostEqual(after_impact - self.vitals.now[combat.ARMOUR_POOL], 20)
        self.assertEqual(self.vitals.now[combat.HEALTH_POOL], 300)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_projected_barrier_uses_raw_beam_damage_before_recipient_armour(self):
        self.c.mover.position[:] = [4, 1, 5]
        self.run.step({self.c: (-16384, 0, 16)})
        instance = next(i for i in self.c.body_script.component.instances.values()
                        if i.graph.index == 0x025C)
        self.assertTrue(instance.state(10).active)
        self.advance(51, 1)
        self.assertEqual(self.vitals.now[combat.ARMOUR_POOL], 200)
        self.assertEqual(self.vitals.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(self.c.body_script.component.entity_var(87).value(), 12, places=4)
        self.assertAlmostEqual(self.fight.zarya_barriers.projected[self.c.body].remaining, 140, places=4)


class ArmourPoolPolicyTests(unittest.TestCase):
    def test_beam_scalar_only_applies_after_shields_are_consumed(self):
        vitals = combat.Vitals(1, 300, 100, 16)
        self.assertAlmostEqual(vitals.damage(20, armour_scalar=0.7), 18.8)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        self.assertAlmostEqual(vitals.now[combat.ARMOUR_POOL], 97.2)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)

    def test_beam_scalar_does_not_reduce_unarmoured_health(self):
        vitals = combat.Vitals(1, 300)
        self.assertEqual(vitals.damage(20, armour_scalar=0.7), 20)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 280)

    def test_dot_preserves_full_damage_across_armour_health_boundary(self):
        vitals = combat.Vitals(1, 300, 3)
        self.assertEqual(vitals.damage(20, armour_scalar=1.0), 20)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 283)

    def test_beam_overflow_retains_reduced_remainder_as_declared_host_policy(self):
        vitals = combat.Vitals(1, 300, 3)
        self.assertEqual(vitals.damage(10, armour_scalar=0.7), 7)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 296)


if __name__ == "__main__":
    unittest.main()
