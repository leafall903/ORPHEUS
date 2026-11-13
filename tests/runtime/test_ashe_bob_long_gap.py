"""A real ultimate retains every physical charge pose across a long update gap."""
import json
import unittest
from pathlib import Path

import test_ashe_bob_charge as bob_fixture

from ow174.game import combat

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-bob-long-gap-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


class AsheBobLongGapTests(unittest.TestCase):
    def test_real_ultimate_path_matches_dense_split_and_single_updates(self):
        rows = []
        for batches in CONTRACT['input']['update_batches']:
            with self.subTest(batches=batches):
                fixture = bob_fixture.BobChargeTests()
                run, owner, _enemy = fixture.setup(distance=CONTRACT['input']['enemy_distance'])
                actor = fixture.begin(run, owner)
                updates = [1] * CONTRACT['input']['dense_step_count'] if batches == [1] else batches
                for count in updates:
                    run.frame += count
                    run.match.update(run.frame * combat.TICK, run.frame)
                before = tuple(actor.bot.position)
                run.match.combat.companions.update(run.frame)
                self.assertEqual(tuple(actor.bot.position), before)
                self.assertEqual(actor.last_tick, run.frame)
                row = (tuple(actor.bot.position), tuple(actor.bot.velocity), actor.phase,
                       actor.stopped_reason, actor.ready - actor.born, actor.until - actor.born)
                rows.append(row)
                self.assertEqual(row[0], tuple(CONTRACT['expected']['position']))
                self.assertEqual(row[2:], (CONTRACT['expected']['phase'], CONTRACT['expected']['stop'],
                                          CONTRACT['expected']['ready_offset'],
                                          CONTRACT['expected']['until_offset']))
        self.assertTrue(all(row == rows[0] for row in rows))
