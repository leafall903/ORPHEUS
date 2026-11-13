"""Two narrow actual-attack checks for original landing and repeated punch rules."""
import json
import math
import unittest
from itertools import pairwise
from pathlib import Path
from unittest import mock

from test_winston_knockback_r2_producers import ActualSkills

from ow174.game import combat
from ow174.game.commands import Command

CONTRACT = Path(__file__).resolve().parents[2] / 'fixtures/behavior/winston-original-force-cadence-v1.json'
OBSERVATIONS = []


class WinstonOriginalForceRulesTests(unittest.TestCase):
    def test_real_landing_has_radial_flat_force_and_separate_damage_falloff(self):
        contract = json.loads(CONTRACT.read_text(encoding='utf-8'))
        rows = []
        for offset in contract['landing_offsets']:
            case = ActualSkills()
            case.victim.mover.position[:] = (1000, 0, 1100)
            before = case.health()
            calls = []
            original = case.fight.knockbacks.apply

            def apply(target, impulse, case=case, calls=calls, forward=original, **kwargs):
                if target.owner is case.victim:
                    calls.append({'source': tuple(case.source.mover.position), 'target': target.feet,
                                  'impulse': tuple(impulse), 'world_tick': case.tick,
                                  'impact_tick': kwargs.get('impact_tick'),
                                  'source_entity': kwargs.get('source_entity')})
                return forward(target, impulse, **kwargs)

            with mock.patch.object(case.fight.knockbacks, 'apply', apply):
                case.step(8, pitch=-16384)
                self.assertEqual(case.health(), before)  # No takeoff contact.
                for _ in range(180):
                    x, _, z = case.source.mover.position
                    case.victim.mover.position[:] = (x + offset[0], 0, z + offset[1])
                    case.step()
                    if case.health() < before:
                        break
                force, = calls
                dx = force['target'][0] - force['source'][0]
                dz = force['target'][2] - force['source'][2]
                planar = math.hypot(dx, dz)
                strength = contract['expected_landing_horizontal']
                self.assertAlmostEqual(force['impulse'][0], dx / planar * strength)
                self.assertAlmostEqual(force['impulse'][2], dz / planar * strength)
                self.assertAlmostEqual(math.hypot(force['impulse'][0], force['impulse'][2]), strength)
                self.assertEqual(force['impact_tick'], force['world_tick'])
                self.assertEqual(force['source_entity'], case.source.body)
                self.assertNotIn(case.source.body, case.fight.winston_jump.flights)
                hit, = case.source.client.messages(combat.HIT)
                self.assertEqual(hit['+0x84'], 9000 + case.tick - 1000)
                # Resending the accepted owner command cannot hit or push again.
                case.source.take_commands([Command(hit['+0x84'])])
                for _ in range(3):
                    case.step()
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(case.source.client.messages(combat.HIT)), 1)
            row = {'kind': 'landing', 'offset': offset, 'damage': before - case.health(), **force}
            rows.append(row)
            OBSERVATIONS.append(row)
        self.assertGreater(rows[0]['damage'], rows[1]['damage'])

    def test_held_primal_keeps_original_cadence_and_one_force_per_damage_activation(self):
        contract = json.loads(CONTRACT.read_text(encoding='utf-8'))
        case = ActualSkills()
        case.rage()
        events = []
        before = case.health()
        with (
            mock.patch.object(case.fight.knockbacks, 'apply', wraps=case.fight.knockbacks.apply) as apply,
            mock.patch.object(case.fight.winston_jump, 'knockback',
                              wraps=case.fight.winston_jump.knockback) as producer,
        ):
            for _ in range(contract['held_punch_frames']):
                # As in the existing held-punch fixture, pin the target to isolate
                # activation timing. Force dispatch itself remains the real path.
                case.victim.mover.position[:] = (1000, 0, 1001.5)
                case.victim.mover.velocity = (0, 0, 0)
                case.victim.mover.body = None
                force_count = apply.call_count
                case.step(1)
                after = case.health()
                if after < before:
                    self.assertEqual(apply.call_count - force_count, 1)
                    call = apply.call_args
                    self.assertIs(call.args[0].owner, case.victim)
                    self.assertEqual(call.kwargs['impact_tick'], case.tick)
                    self.assertEqual(call.kwargs['source_entity'], case.source.body)
                    state = producer.call_args.args[2].index
                    events.append((case.tick, state, before - after))
                    # Duplicate input windows are consumed once by the real queue.
                    frame = 9000 + case.tick - 1000
                    case.source.take_commands([Command(frame, buttons=1)])
                    self.assertEqual(apply.call_count, force_count + 1)
                else:
                    self.assertEqual(apply.call_count, force_count)
                before = after
            self.assertEqual(apply.call_count, len(events))
            self.assertEqual(len(case.source.client.messages(combat.HIT)), len(events))
        self.assertGreaterEqual(len(events), 3)
        low, high = contract['expected_punch_period_frames']
        for first, second in pairwise(events):
            self.assertTrue(low <= second[0] - first[0] <= high)
            self.assertNotEqual(first[1], second[1])
        OBSERVATIONS.append({'kind': 'held_primal', 'events': events,
                             'force_count': len(events), 'native_adoption': 'NOT TESTED'})
