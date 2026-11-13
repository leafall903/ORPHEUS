"""Actual Punch authored enum4 packs the original internal world SET request."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat, doomfist
from ow174.game.content import heroes
from ow174.game.mover import MULTIPLY
from ow174.game.script import expr


class PunchMode4Tests(DoomfistFixture, unittest.TestCase):
    def _impact(self, name):
        spec = json.loads((Path(__file__).resolve().parents[2] /
                           'fixtures/behavior/doomfist-punch-mode4-v1.json').read_text())
        case = next(row for row in spec['cases'] if row['name'] == name)
        self.prepare(spec['inputs']['distance'])
        if case['k'] == 0:
            position = tuple(self.b.mover.position)
            self.match.switch_hero(self.b, heroes()[0x02E000000000013E])
            self.place_player(self.b, position)
            self.run.step({self.b: (0, 0, 8)})
        self.a.body_script.component.set(10959, case['caller_r'])
        self.b.body_script.component.set(10959, case['recipient_r'])
        before = self.vitals().total
        events = []
        original = self.fight.knockbacks.apply

        def capture(target, vector, **kwargs):
            instance = next(i for i in self.a.body_script.component.instances.values()
                            if i.graph.index == 0xD06)
            dash = self.fight.punches.dashes[self.a.body]
            accepted = original(target, vector, **kwargs)
            mover = self.fight.knockbacks.active.get(target.entity)
            mods = tuple(mod for key, mod in mover.mods.items() if key[0] == -3) if mover else ()
            events.append((tuple(vector), kwargs, instance.vars[2083].value(), dash.knockback, mods))
            return accepted

        scale = doomfist.Slams._target_impulse_scale
        def response(target):
            if target.entity != self.b.body:
                return scale(target)
            if case.get('retained_forward') is not None:
                instance = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0xD06)
                instance.write((expr.INSTANCE, 2083), expr.Vec3(*case['retained_forward']))
            if case['k'] == 0:
                self.assertEqual(scale(target), 0)
                return scale(target)
            return case['k']

        with patch.object(self.fight.knockbacks, 'apply', side_effect=capture), \
                patch.object(doomfist.Slams, '_target_impulse_scale', side_effect=response), \
                patch.object(self.b.mover, 'add_timed_mod', wraps=self.b.mover.add_timed_mod) as timed:
            for _ in range(spec['inputs']['hold_frames']):
                self.run.step({self.a: (0, 0, 2)})
            self.run.step({})
            for _ in range(spec['inputs']['settle_frames']):
                self.run.step({})
        self.assertEqual(timed.call_count, 0 if case['k'] == 0 else 2)
        self.assertLess(self.vitals().total, before)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertEqual(len(events), 0 if case['k'] == 0 else 1)
        if events:
            vector, kwargs, forward, strength, mods = events[0]
            r = case['caller_r'] if case['caller_r'] is not None else 1
            h = expr.f32(expr.f32(strength * r) * case['k'])
            expected = tuple(expr.f32(expr.f32(h * f) + (r if axis == 1 else 0))
                             for axis, f in enumerate(forward))
            for actual, want in zip(vector, expected, strict=True):
                self.assertAlmostEqual(actual, want, places=5)
            self.assertEqual(kwargs['velocity_mode'], 0)
            self.assertEqual(kwargs['source_entity'], self.a.body)
            self.assertEqual(kwargs['velocity_priority'], 1)
            self.assertEqual(tuple((i, op, round(v, 6), round(seconds, 6), priority)
                                   for i, op, v, seconds, priority in kwargs['launch_modifiers']),
                             ((4, MULTIPLY, .01, .32, 0),
                              (7, MULTIPLY, 0, .32, 1)))
            self.assertEqual({(m.index, m.op, m.priority, m.end - m.start) for m in mods},
                             {(4, MULTIPLY, 0, 20), (7, MULTIPLY, 1, 20)})
        self.assertNotIn(self.b.body, self.fight.punches.controls)
        if events:
            self.assertFalse(any(m.acts(self.b.body_script.component.frame) for m in events[0][-1]))
        return events

    def test_actual_default_punch_queues_full_vector_mode4(self):
        self._impact('default')

    def test_actual_punch_uses_caller_r_and_scales_only_forward_by_response(self):
        self._impact('caller_scaled')

    def test_actual_zero_response_preserves_damage_without_force_or_control(self):
        self._impact('fortify')
