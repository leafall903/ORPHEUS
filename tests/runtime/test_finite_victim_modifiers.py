"""Finite effects share actual recipient time without fabricating a force."""
import unittest

import test_canonical_knockback_174 as fixtures
import test_dva_movement_correction as dva_fixture

from ow174.game import combat, correction
from ow174.game.commands import Command
from ow174.game.mover import MULTIPLY


class FiniteVictimModifierTests(unittest.TestCase):
    def test_possessed_body_keeps_its_actual_mover_and_correction_target_without_force(self):
        fixture = dva_fixture.DvaMovementCorrectionTests()
        fixture.setUpClass()
        _client, pilot, mech = fixture.scene()
        fight = pilot.match.combat
        target = next(t for t in fight._targets() if t.owner is mech)
        mover = mech.control_mover
        serial = fight.knockbacks.serial
        self.assertTrue(fight.knockbacks.admit_modifiers(
            target, impact_tick=1070, source_entity=0xA000FF01,
            launch_modifiers=((4, MULTIPLY, .01, .32, 0.), (7, MULTIPLY, 0., .32, 1.))))
        self.assertIsNone(mech.impulse_mover)
        self.assertEqual(mover.velocity_changes, [])
        self.assertFalse(mover.external_impulse)
        self.assertEqual(fight.knockbacks.serial, serial)
        pilot.match.update(1071*combat.TICK, 1071)
        pilot.take_commands([Command(1071)])
        self.assertIs(mech.control_mover, mover)
        self.assertAlmostEqual(mover.values.decel_ground_base,
                               mover.data.decel_ground_base * .01, delta=1e-6)
        update = correction.own_record(pilot, 1071)
        self.assertIsNotNone(update)
        self.assertEqual(update.entity, mech.entity)
        self.assertIs(update.correction.mover, mover)
        self.assertEqual(fight.knockbacks.serial, serial)

    def test_shifted_player_modifiers_consume_and_expire_without_a_velocity_request(self):
        fixture = fixtures.CanonicalKnockbackTests()
        run, source, victim, _target, ids = fixture.scene(victim_birth=9000)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        mover = victim.mover
        position = tuple(mover.position)
        serial = fight.knockbacks.serial
        health = fight.vitals_of(target).total
        self.assertFalse(mover.external_impulse)
        self.assertTrue(fight.knockbacks.admit_modifiers(
            target, impact_tick=run.match.tick, source_entity=source.body,
            launch_modifiers=((4, MULTIPLY, .01, .32, 0.), (7, MULTIPLY, 0., .32, 1.))))
        self.assertEqual(fight.knockbacks.serial, serial)
        self.assertEqual(mover.velocity_changes, [])
        self.assertFalse(mover.external_impulse)
        self.assertNotIn(victim.body, fight.knockbacks.active)
        fixture.step(run, 0, 9000)
        self.assertIs(victim.mover, mover)
        self.assertAlmostEqual(mover.values.decel_ground_base,
                               mover.data.decel_ground_base * .01, delta=1e-6)
        self.assertEqual(mover.velocity, (0., 0., 0.))
        self.assertEqual(tuple(mover.position), position)
        for _ in range(20):
            fixture.step(run, 0, 9000)
        self.assertEqual(mover.values.decel_ground_base, mover.data.decel_ground_base)
        self.assertFalse(any(key[0] == -3 for key in mover.mods))
        self.assertEqual(fight.knockbacks.serial, serial)
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertEqual(ids, tuple(bot.entity for bot in run.match.bots))
