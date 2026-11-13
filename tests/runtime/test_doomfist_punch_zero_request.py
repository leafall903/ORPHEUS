"""Authored enum4 zero produces a real world SET reset and finite Punch effects."""
import json
import unittest
from pathlib import Path

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat
from ow174.game.mover import MULTIPLY


class PunchZeroRequestTests(DoomfistFixture, unittest.TestCase):
    def test_actual_zero_multiplier_resets_velocity_and_keeps_finite_effects(self):
        spec = json.loads((Path(__file__).resolve().parents[2] /
                           'fixtures/behavior/doomfist-punch-zero-request-v1.json').read_text())
        self.prepare(spec['inputs']['distance'])
        self.a.body_script.component.set(10959, spec['inputs']['caller_multiplier'])
        before = self.vitals().total
        serial = self.fight.knockbacks.serial
        position = tuple(self.b.mover.position)
        for _ in range(spec['inputs']['hold_frames']):
            self.run.step({self.a: (0, 0, 2)})
        self.run.step({})
        for _ in range(60):
            self.run.step({})
            if self.vitals().total < before:
                break
        self.assertLess(self.vitals().total, before)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), spec['expected']['hit_count'])
        self.assertEqual(self.fight.knockbacks.serial - serial, spec['expected']['force_serial_delta'])
        self.assertEqual(len(self.b.mover.velocity_changes), spec['expected']['world_set_request_count'])
        change, = self.b.mover.velocity_changes
        self.assertEqual((change.mode, change.value), (spec['expected']['internal_queue_mode'], (0.0, 0.0, 0.0)))
        self.assertEqual(tuple(self.b.mover.velocity), (0, 0, 0))
        self.assertEqual(tuple(self.b.mover.position), position)
        mods = [mod for key, mod in self.b.mover.mods.items() if key[0] == -3]
        self.assertEqual(len(mods), spec['expected']['braking_count'])
        self.assertEqual({(m.index, m.op, m.priority, m.end - m.start) for m in mods},
                         {(4, MULTIPLY, 0, spec['expected']['braking_frames']),
                          (7, MULTIPLY, 1, spec['expected']['braking_frames'])})
        self.assertTrue(self.fight.punches.controlled(self.b.body))
        self.settle(1)
        self.assertFalse(self.b.mover.velocity_changes)
        self.assertFalse(self.b.mover.external_impulse)
        self.assertAlmostEqual(self.b.mover.values.decel_ground_base,
                               self.b.mover.data.decel_ground_base * .01, delta=1e-6)
        self.assertEqual(self.b.mover.values.decel_air_base, 0)
        # Braking ends independently of the existing finite control reconstruction.
        self.settle(22)
        self.assertFalse(any(m.acts(self.b.body_script.component.frame) for m in mods))
        self.assertEqual(self.b.mover.values.decel_ground_base, self.b.mover.data.decel_ground_base)
        self.assertTrue(self.fight.punches.controlled(self.b.body))
        self.settle(20)
        self.assertFalse(self.fight.punches.controlled(self.b.body))
        self.assertFalse(self.b.body_script.component.input_suppressed)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
