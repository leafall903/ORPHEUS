"""Original224E/31 horizontal Clamp reaches the existing recipient mover."""

import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_brigitte as fixtures
import test_game_brigitte_shield as shields

from ow174.game.commands import FIRE, SECONDARY
from ow174.game.mover import CLAMP, Mover, f32
from ow174.game.physics_snapshot import snapshot_mover


class BashVelocityClampTests(unittest.TestCase):
    def test_normal_bash_enters_original_clamp_once_before_the_single_force(self):
        run, brig = fixtures.BrigitteTests().fixture()
        victim = run.players[1]
        x, y, z = brig.mover.position
        victim.mover.position, victim.mover.body = [x, y, z + 3], None
        shields.BarrierTests().raise_shield(run, brig, 25)
        victim.mover.velocity = (9., 0., 0.)
        events = []
        original = Mover._apply_velocity_mods
        host = run.match.combat.knockbacks

        def consume(mover, active, normal):
            selected = [row for row in active if row.index == 33 and row.op == CLAMP]
            before = tuple(mover.velocity)
            result = original(mover, active, normal)
            if mover is victim.mover and selected:
                events.append((selected, before, tuple(mover.velocity)))
            return result

        with (
            patch.object(Mover, "_apply_velocity_mods", consume),
            patch.object(host, "apply", wraps=host.apply) as force,
        ):
            run.step({brig: (0, 0, SECONDARY | FIRE)})
            for _ in range(8):
                run.step({brig: (0, 0, SECONDARY)})
        self.assertEqual(len(events), 1)
        [record], before, after = events[0]
        self.assertEqual(record.value, (0., 3.))
        self.assertEqual(record.end, record.start + 1)
        self.assertGreater(math.hypot(before[0], before[2]), 3)
        self.assertAlmostEqual(math.hypot(after[0], after[2]), 3, places=5)
        self.assertEqual(after[1], before[1])
        self.assertEqual(force.call_count, 1)
        self.assertEqual(force.call_args.kwargs, {"impact_tick": 1027, "source_entity": brig.body})
        self.assertEqual(host.serial, 1)
        target = fixtures.BrigitteTests().target(run, victim)
        self.assertEqual(run.match.combat.vitals_of(target).total, 195)
        [hit] = brig.client.messages(25001)
        self.assertEqual(hit["+0x84"], 1027)
        self.assertEqual(hit["+0x78"]["+0x4"], 5)
        self.assertTrue(run.match.combat.is_stunned(victim))

    def test_single_scalar_record_matches_pinned_original_consumer_cases(self):
        data = json.loads((Path(__file__).resolve().parents[2] /
                           "fixtures/behavior/brigitte-stun-horizontal-clamp-v1.json").read_text())
        _run, brig = fixtures.BrigitteTests().fixture()
        mover = brig.mover
        for case in data["cases"]:
            with self.subTest(case=case):
                mover.mods.clear()
                mover.queue_velocity_changes(100, (71, 31), ((0, 1, CLAMP, (0., 3.), 0.),))
                [row] = list(mover.mods.values())
                self.assertFalse(row.acts(100))
                self.assertTrue(row.acts(101))
                self.assertFalse(row.acts(102))
                if case is data["cases"][0]:
                    json.dumps(snapshot_mover(mover))  # pending bound-pair remains snapshot-safe
                actual = mover._modified_velocity([row], tuple(case["input"]))
                for value, expected in zip(actual, case["expected"], strict=True):
                    self.assertAlmostEqual(value, f32(expected), places=5)
