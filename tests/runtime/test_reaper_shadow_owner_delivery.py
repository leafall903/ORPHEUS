"""Actual Shadow Step completion delivers only its relocated owner poses."""

import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, correction, reaper
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime import test_game_reaper as reaper_tests


class ReaperShadowOwnerDeliveryTests(unittest.TestCase):
    def scene(self, birth):
        run, owner, victim, _ = reaper_tests.ReaperTests().scenario()
        owner.commands.last_frame = birth
        run.match.switch_hero(owner, heroes()[reaper.HERO], preserve_pose=True)
        owner.body_script.create_arrived = True
        victim.spawned = False
        shadow = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == reaper.SHADOW_GRAPH)
        return run, owner, shadow

    def step(self, run, owner, buttons=0, *, forward=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        owner.take_commands([Command(owner.commands.last_frame + 1, pitch=5000,
                                     buttons=buttons, forward=forward)])

    def test_completed_cast_uses_actual_pose_floor_and_finite_body_lease(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/reaper-shadow-owner-delivery-v1.json'
        fixture = json.loads(path.read_text(encoding='utf-8'))
        for birth in fixture['input']['owner_births']:
            with self.subTest(owner_birth=birth):
                run, owner, shadow = self.scene(birth)
                fight, script = run.match.combat, owner.body_script
                old = birth != run.frame
                if old:
                    self.assertTrue(correction.request_motion(
                        owner, first_frame=script.frame, through_frame=script.frame + 400,
                        expires_tick=run.frame + 400, body=owner.body))
                start = tuple(owner.mover.position)
                self.step(run, owner, 16)
                self.step(run, owner)
                record = fight.reaper_shadows[owner.body]
                destination = record['target']
                self.assertIsNotNone(destination)
                self.step(run, owner, 1)
                with patch.object(correction, 'request_motion', wraps=correction.request_motion) as requested:
                    previous = None
                    for _ in range(fixture['input']['max_cast_steps']):
                        previous = correction.own_record(owner, run.frame)
                        self.step(run, owner)
                        if record['moved']:
                            break
                self.assertTrue(record['moved'])
                self.assertNotEqual(tuple(owner.mover.position), start)
                self.assertLess(math.dist(owner.mover.position, destination), .002)
                self.assertEqual(requested.call_count, 1)
                moved_frame = script.frame
                self.assertEqual(requested.call_args.kwargs,
                                 {'first_frame': moved_frame, 'body': owner.body,
                                  'first_valid_frame': moved_frame})
                update = correction.own_record(owner, run.frame)
                self.assertIsNotNone(update)
                self.assertEqual(update.entity, owner.body)
                self.assertIs(update.correction.mover, owner.mover)
                self.assertIs(update.correction.script, script)
                self.assertEqual(update.movement.frame, moved_frame)
                self.assertEqual(tuple(update.movement.position), tuple(owner.mover.position))
                if previous is not None:
                    self.assertFalse(correction._current(owner, previous.correction))
                for _ in range(60):
                    self.step(run, owner)
                self.assertFalse(shadow.state(19).active)
                self.assertFalse(reaper.intangible(owner))
                landing = tuple(owner.mover.position)
                self.step(run, owner, forward=127)
                self.assertGreater(math.dist(owner.mover.position, landing), .01)
                deadline = owner.motion_correction_deadline
                while run.frame <= deadline:
                    self.step(run, owner)
                self.assertFalse(correction.correcting(owner))
                self.assertIsNone(correction.own_record(owner, run.frame))

    def test_cancel_does_not_request_motion_and_returns_fire_control(self):
        run, owner, shadow = self.scene(9000)
        start = tuple(owner.mover.position)
        with patch.object(correction, 'request_motion', wraps=correction.request_motion) as requested:
            for buttons in (16, 0, 2, 0):
                self.step(run, owner, buttons)
            self.assertFalse(shadow.state(0).active)
            self.assertFalse(shadow.state(6).active)
            self.assertEqual(shadow.vars[61].value(), 0)
            self.assertEqual(tuple(owner.mover.position), start)
            weapon = next(i for i in owner.body_script.component.instances.values()
                          if i.graph.index == reaper.SHOTGUN_GRAPH)
            before = weapon.vars[53].value()
            self.step(run, owner, 1)
            self.assertEqual(weapon.vars[53].value(), before - 1)
            self.assertEqual(requested.call_count, 0)
            self.assertIsNone(correction.own_record(owner, run.frame))
