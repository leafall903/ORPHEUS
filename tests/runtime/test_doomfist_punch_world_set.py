"""Real legacy Punch owns one damage/HIT/action; authored4 is world SET, including zero."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat, doomfist, velocity


class PunchWorldSetTests(DoomfistFixture, unittest.TestCase):
    def actual_contact(self, caller_r):
        self.prepare(3)
        self.a.body_script.component.set(10959, caller_r)
        before, serial = self.vitals().total, self.fight.knockbacks.serial
        original_damage, original_consume = self.fight._damage, velocity.consume
        consumed = []

        def damage(*args, **kwargs):
            result = original_damage(*args, **kwargs)
            if len(args) > 4 and args[4] == doomfist.PUNCH and result:
                self.b.mover.velocity = (4.0, 0.0, 30.0)
            return result

        def consume(current, jump, changes, frame):
            result = original_consume(current, jump, changes, frame)
            for row in changes:
                if changes is self.b.mover.velocity_changes and row.frame == frame and row.index == -1:
                    consumed.append((current, row, result))
            return result

        with patch.object(self.fight, '_damage', side_effect=damage), \
                patch.object(velocity, 'consume', side_effect=consume):
            for _ in range(18):
                self.run.step({self.a: (0, 0, 2)})
            self.run.step({})
            for _ in range(60):
                self.run.step({})
                if consumed:
                    break
        self.assertEqual(len(consumed), 1)
        current, row, result = consumed[0]
        self.assertGreater(current[0], 0)
        self.assertGreater(current[2], row.value[2])
        self.assertEqual(result, row.value)
        self.assertEqual((row.mode, row.op, row.priority), (0, 2, 1))
        self.assertEqual(result[0], 0)
        self.assertEqual(self.fight.knockbacks.serial - serial, 1)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertAlmostEqual(before - self.vitals().total, 53.643378832541636, places=5)
        mods = [m for k, m in self.b.mover.mods.items() if k[0] == -3]
        self.assertEqual({(m.index, m.priority, m.end - m.start) for m in mods}, {(4, 0, 20), (7, 1, 20)})
        self.assertFalse(any(i.graph.index == 0xD07 for i in self.b.body_script.component.instances.values()))
        if caller_r == 0:
            self.assertEqual(result, (0.0, 0.0, 0.0))
        return result

    def test_actual_positive_contact_reduces_faster_velocity_and_clears_orthogonal_motion(self):
        self.actual_contact(1)

    def test_actual_zero_contact_consumes_one_real_reset_without_a_second_damage_path(self):
        self.actual_contact(0)

    def test_native_world_set_oracle_and_existing_projection_have_distinct_queue_meanings(self):
        spec = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                           / 'velocity-authored-enum4-world-set-v1.json').read_text())
        for case in spec['cases']:
            row = velocity.world_set(4, case['requested'])
            self.assertEqual((row.mode, row.op), (spec['internal_queue_mode'], spec['operation']))
            self.assertEqual(velocity.consume(tuple(case['current']), 6, [row], 4), tuple(case['resolved']))
        self.assertEqual(velocity.consume((4., 0., 30.), 6, [velocity.projected(4, (0, 1, 15))], 4),
                         (4., 0., 30.))
