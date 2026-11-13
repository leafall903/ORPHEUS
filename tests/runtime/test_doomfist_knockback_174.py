"""Doomfist-specific tracked-hit force, separate from native adoption."""

import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat
from ow174.game.content import heroes
from tests.runtime.test_doomfist_combat import DoomfistFixture


class UppercutTrackedForceTests(DoomfistFixture, unittest.TestCase):
    def contract(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/doomfist-knockback-174-v1.json"
        return json.loads(path.read_text())

    def test_actual_tracked_hit_pushes_radially_once_and_lands(self):
        spec = self.contract()
        self.prepare(1.5)
        self.match.switch_hero(self.b, self.a.hero)
        x, y, z = self.a.mover.position
        dx, dy, dz = spec["input"]["target_offset"]
        self.place_player(self.b, (x + dx, y + dy, z + dz))
        self.settle(1)
        before = self.vitals().total
        start = tuple(self.b.mover.position)
        serial = self.fight.knockbacks.serial
        with patch.object(self.fight.knockbacks, "apply", wraps=self.fight.knockbacks.apply) as apply:
            self.run.step({self.a: (0, 0, spec["input"]["ability_button"])})
            own = self.fight.player_target(self.a)
            self.assertEqual(self.fight.vitals_of(own).temporary_shields,
                             spec["expected"]["passive_shields"])
            self.settle(160)
        expected = spec["expected"]
        self.assertEqual(apply.call_count, expected["accepted_impulse_count"])
        impulse = tuple(apply.call_args.args[1])
        self.assertAlmostEqual(math.hypot(impulse[0], impulse[2]), expected["horizontal_speed"])
        self.assertAlmostEqual(impulse[1], expected["upward_impulse"])
        self.assertAlmostEqual(impulse[0] / impulse[2], dx / dz)
        self.assertEqual(self.fight.knockbacks.serial - serial, expected["accepted_impulse_count"])
        self.assertEqual(before - self.vitals().total, expected["damage"])
        self.assertGreater(self.b.mover.position[0], start[0] + .1)
        self.assertGreater(self.b.mover.position[2], start[2] + .1)
        self.assertFalse(self.b.mover.airborne)
        self.assertAlmostEqual(self.b.mover.position[1], start[1])
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_actual_fortify_hit_keeps_damage_without_accepting_force(self):
        spec = self.contract()
        self.prepare(1.5)
        self.match.switch_hero(self.b, heroes()[0x02E000000000013E])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 1.5))
        self.run.step({self.b: (0, 0, 8)})
        target = self.fight.player_target(self.b)
        from ow174.game.doomfist import Slams

        self.assertEqual(Slams._target_impulse_scale(target), spec["expected"]["fortify_response"])
        before, serial = self.vitals().total, self.fight.knockbacks.serial
        start = tuple(self.b.mover.position)
        self.run.step({self.a: (0, 0, spec["input"]["ability_button"])})
        self.settle(30)
        self.assertLess(self.vitals().total, before)
        self.assertEqual(self.fight.knockbacks.serial - serial,
                         spec["expected"]["fortify_accepted_impulse_count"])
        self.assertEqual(tuple(self.b.mover.position), start)
