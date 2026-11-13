"""Real Recall/Blink inputs enable only finite, current-body owner corrections."""
import json
import math
import unittest
from pathlib import Path

import test_game_tracer as scenes

from ow174.game import combat, correction
from ow174.game.commands import Command


class TracerMotionActivationTests(unittest.TestCase):
    def scene(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/tracer-motion-activation-v1.json'
        self.fixture = json.loads(path.read_text(encoding='utf-8'))
        case = scenes.TracerRecallTests()
        case.setUp()
        self.case = case
        self.run, self.player = case.run, case.player
        birth = self.fixture['input']['owner_birth']
        self.assertTrue(self.run.match.switch_hero(self.player, self.player.hero,
                                                  preserve_pose=True, start_frame=birth))
        self.player.commands.reset(last_frame=birth)
        self.player.body_script.create_arrived = True
        self.owner_frame = birth
        self.target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.health = self.run.match.combat.vitals_of(self.target)

    def step(self, buttons=0, forward=0):
        self.run.frame += 1
        self.owner_frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.player.take_commands([Command(self.owner_frame, buttons=buttons, forward=forward)])

    def test_real_recall_restores_then_opens_and_expires_owner_window(self):
        self.scene()
        for _ in range(self.fixture['input']['history_steps']):
            self.step(forward=127)
        fight = self.run.match.combat
        fight._damage(self.case.enemy, self.target, self.fixture['input']['incoming_damage'],
                      False, None, self.owner_frame)
        self.assertEqual(self.health.total, 80)
        self.step(self.fixture['input']['recall_button'])
        before = tuple(self.player.mover.position)
        phase = fight.recalls.phases[self.player.body]
        restore_world = math.ceil(phase.ends / combat.TICK - 1e-7)
        expected = phase.sample.feet
        while self.run.frame + 1 < restore_world:
            self.step()
            self.assertEqual(tuple(self.player.mover.position), before)
            self.assertEqual(self.health.total, 80)
            self.assertFalse(correction.correcting(self.player))
        self.step()
        self.assertEqual(self.run.frame, restore_world)
        self.assertNotIn(self.player.body, fight.recalls.phases)
        self.assertEqual(tuple(self.player.mover.position), expected)
        self.assertEqual(self.health.total, self.fixture['expected']['restored_health'])
        record = correction.own_record(self.player, self.run.frame)
        self.assertIsNotNone(record, 'Recall restored only authority; owner motion activation missing')
        self.assertEqual(record.entity, self.player.body)
        self.assertIs(record.correction.mover, self.player.mover)
        self.assertIs(record.correction.script, self.player.body_script)
        self.assertEqual(record.correction.frame, self.owner_frame)
        self.assertEqual(record.movement.position, tuple(self.player.mover.position))
        self.assertEqual(self.player.impulse_correction[0], self.owner_frame)
        deadline = self.player.motion_correction_deadline
        self.assertLess(deadline - self.run.frame, 126)
        # No repeated activation: an unsent lease still closes by physical time.
        while self.run.frame <= deadline:
            self.step()
        self.assertIsNone(correction.own_record(self.player, self.run.frame))
        self.assertIsNone(self.player.impulse_correction)
        self.assertFalse(correction.correcting(self.player))
        self.assertFalse(hasattr(self.player, 'motion_correction_floor'))
        corrected = self.player.mover.corrected
        correction.sent(self.player, record)
        self.assertEqual(self.player.mover.corrected, corrected)

    def test_real_blink_current_pose_waits_for_create_ack_and_retires_on_body_change(self):
        self.scene()
        self.player.body_script.create_arrived = False
        before = tuple(self.player.mover.position)
        self.step(self.fixture['input']['blink_button'])
        after = tuple(self.player.mover.position)
        self.assertAlmostEqual(after[2] - before[2], self.fixture['expected']['blink_distance'], places=3)
        self.assertEqual(self.player.mover.history[-1].position, after)
        self.assertIsNone(correction.own_record(self.player, self.run.frame))
        self.player.body_script.create_arrived = True
        record = correction.own_record(self.player, self.run.frame)
        self.assertIsNotNone(record, 'Blink same-frame authority has no finite owner activation')
        self.assertEqual(record.correction.frame, self.owner_frame)
        self.assertEqual(record.movement.position, after)
        old_body = self.player.body
        self.assertTrue(self.run.match.switch_hero(self.player, self.player.hero, start_frame=9300))
        self.assertNotEqual(self.player.body, old_body)
        self.player.body_script.create_arrived = True
        self.assertIsNone(correction.own_record(self.player, self.run.frame))
        self.assertIsNone(self.player.impulse_correction)
        self.assertFalse(hasattr(self.player, 'motion_correction_floor'))
        corrected = self.player.mover.corrected
        correction.sent(self.player, record)
        self.assertEqual(self.player.mover.corrected, corrected)
