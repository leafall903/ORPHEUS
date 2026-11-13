"""Real command producers against the shared receiver candidate; no native pass."""

import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands

from ow174.game import combat
from ow174.game.content import heroes


class ForceProducerTests(DoomfistFixture, unittest.TestCase):
    def trigger(self, skill, fortify=False):
        spec = json.loads((Path(__file__).resolve().parents[2] /
                           "fixtures/behavior/doomfist-force-producers-r2-v1.json").read_text())
        inputs = spec["inputs"][skill]
        self.prepare(inputs["distance"])
        if skill == "meteor":
            charge_by_neutral_commands(self)
            self.run.step({self.a: (0, 0, 32)})
            self.settle(70)
        if fortify:
            position = tuple(self.b.mover.position)
            self.match.switch_hero(self.b, heroes()[0x02E000000000013E])
            self.place_player(self.b, position)
            self.run.step({self.b: (0, 0, 8)})
        start = tuple(self.b.mover.position)
        before = self.vitals().total
        serial = self.fight.knockbacks.serial
        events = []
        real_apply = self.fight.knockbacks.apply

        def observe(target, vector, **kwargs):
            accepted = real_apply(target, vector, **kwargs)
            events.append((target.entity, tuple(vector), kwargs, self.match.tick, accepted))
            return accepted

        with patch.object(self.fight.knockbacks, "apply", side_effect=observe):
            if skill == "punch":
                for _ in range(inputs["hold_frames"]):
                    self.run.step({self.a: (0, 0, 2)})
                self.run.step({})
            else:
                self.run.step({self.a: (0, 0, 1 if skill == "meteor" else 16)})
            for _ in range(160):
                self.run.step({})
        enemy = [e for e in events if e[0] == self.b.body]
        self.assertLess(self.vitals().total, before)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertEqual(self.fight.knockbacks.serial - serial, 0 if fortify else 1)
        if skill == "punch" and fortify:
            self.assertEqual(enemy, [])  # original helper66 gates action58 before submission
            self.assertEqual(tuple(self.b.mover.position), start)
            self.assertNotIn(self.b.body, self.fight.punches.controls)
            return
        self.assertEqual(len(enemy), 1)
        entity, vector, kwargs, tick, accepted = enemy[0]
        expected = {"impact_tick": tick, "source_entity": self.a.body}
        if skill == "punch":
            self.assertEqual(kwargs["velocity_mode"], 4)
            self.assertEqual(kwargs["velocity_priority"], 1)
            self.assertEqual(len(kwargs["launch_modifiers"]), 2)
            expected.update({key: kwargs[key] for key in
                             ("velocity_mode", "velocity_priority", "launch_modifiers")})
        self.assertEqual(kwargs, expected)
        self.assertEqual(accepted, not fortify)
        if fortify:
            self.assertEqual(vector, (0, 0, 0))
            self.assertEqual(tuple(self.b.mover.position), start)
            self.assertNotIn(entity, self.fight.punches.controls)
        else:
            self.assertGreater(math.dist(self.b.mover.position, start), .05)
            if skill == "punch":
                self.assertGreaterEqual(math.hypot(vector[0], vector[2]), 15)
                self.assertLessEqual(math.hypot(vector[0], vector[2]), 30)
                self.assertEqual(vector[1], 1)
                self.assertNotIn(entity, self.fight.punches.controls)
            elif skill == "slam":
                self.assertAlmostEqual(math.hypot(vector[0], vector[2]), 3)
                self.assertEqual(vector[1], spec["expected"]["slam_wave"][1])
                self.assertLess(vector[2], 0)
                self.assertFalse(self.b.mover.airborne)
            else:
                self.assertEqual(vector[1], spec["expected"]["meteor_y"])
                self.assertGreaterEqual(math.hypot(vector[0], vector[2]), 1)
                self.assertLessEqual(math.hypot(vector[0], vector[2]), 10)

    def test_actual_punch_damage_and_force_once_then_control_returns(self):
        self.trigger("punch")

    def test_actual_slam_wave_damage_and_force_once_then_lands(self):
        self.trigger("slam")

    def test_actual_meteor_damage_and_force_once(self):
        self.trigger("meteor")

    def test_actual_fortify_keeps_damage_and_rejects_each_force(self):
        for skill in ("punch", "slam", "meteor"):
            with self.subTest(skill=skill):
                self.trigger(skill, fortify=True)

    def test_actual_slam_close_contact_damages_and_adds_explicit_vector_once(self):
        self.prepare(10)
        ground = self.b.mover.position[1]
        original = self.fight.knockbacks.apply
        with patch.object(self.fight.knockbacks, "apply", wraps=original) as apply:
            self.run.step({self.a: (0, 0, 16)})
            for _ in range(120):
                if (self.a.mover.airborne and self.a.mover.velocity[1] < 0
                        and self.a.mover.position[1] < ground + .8):
                    x, _, z = self.a.mover.position
                    self.place_player(self.b, (x, ground, z + 1))
                self.run.step({})
                if self.vitals().total < 200:
                    break
            self.assertLess(self.vitals().total, 200)
            self.assertEqual(apply.call_count, 1)
            self.assertEqual(tuple(apply.call_args.args[1]), (0, 5, 5))
            impact = apply.call_args.kwargs["impact_tick"]
            self.assertEqual(apply.call_args.kwargs["source_entity"], self.a.body)
            self.settle(1)
            mover = self.b.mover
            brakes = [mod for key, mod in mover.mods.items() if key[0] == -2]
            self.assertEqual(len(brakes), 1)
            self.assertEqual((brakes[0].start, brakes[0].end), (impact + 1, impact + 21))
            self.assertAlmostEqual(brakes[0].value, .01)
            self.settle(160)
            self.assertEqual(apply.call_count, 1)
            self.assertFalse(mover.airborne)
            self.assertFalse(any(key[0] == -2 for key in mover.mods))
            self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
