"""One actual Hellfire primary against original104319 per-pellet vectors."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, reaper
from tests.runtime import test_game_reaper as scenarios


class ReaperNativeRingTests(unittest.TestCase):
    def test_actual_primary_matches_original_ring_and_registers_each_pellet(self):
        contract = json.loads((Path(__file__).resolve().parents[2] /
                               'fixtures/behavior/reaper-native-ring-v1.json').read_text(encoding='utf-8'))
        case = scenarios.ReaperTests()
        run, owner, victim, target = case.scenario()
        fight = run.match.combat
        with patch.object(reaper, 'shotgun_directions', wraps=reaper.shotgun_directions) as spread, \
                patch.object(fight, '_submit_attack', wraps=fight._submit_attack) as submitted, \
                patch.object(fight, '_damage', wraps=fight._damage) as damage:
            case.fire(run, owner, victim)
        state = spread.call_args.args[1]
        identity = contract['input']['hostIdentity']
        self.assertEqual((state.instance.component.entity, state.instance.id, state.index),
                         (identity['sourceBody'], identity['instance'], identity['state']))
        self.assertEqual(spread.call_args.kwargs,
                         {'number': identity['shot'], 'counter': identity['counter']})
        actual = [c.args[2].direction for c in submitted.call_args_list if c.args[2].state is state]
        expected = contract['expected']
        self.assertEqual(len(actual), len(expected['directions']))
        for ray, original in zip(actual, expected['directions'], strict=True):
            for value, reference in zip(ray, original, strict=True):
                self.assertLess(abs(value - reference), expected['componentTolerance'])
        self.assertEqual(damage.call_count, expected['damageApplications'])
        self.assertEqual(len(owner.client.messages(combat.HIT)), expected['hitMessages'])
        self.assertEqual(200 - fight.vitals_of(target).total, expected['healthLoss'])
        self.assertEqual(state.instance.vars[53].value(), expected['ammo'])
