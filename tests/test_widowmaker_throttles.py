"""Actual hook lifecycle preserves authored lateral restriction and raw input."""

import unittest
from unittest.mock import patch

from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import mover
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command


class WidowmakerThrottleTests(_WidowmakerMatch, unittest.TestCase):
    def test_hook_modifier_lifetime_and_next_frame_recovery(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                self.setUp()
                faces = [(-50, 0, 10, 50, 0, 10, 50, 50, 10),
                         (-50, 0, 10, 50, 50, 10, -50, 50, 10)]
                faces += [(*f[6:9], *f[3:6], *f[0:3]) for f in faces[:]]
                self.run.match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50), *faces,
                ])
                instance = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0x5A)
                observed = []
                original = mover.Mover._accelerate

                def capture(body, dt, right, forward, axes, grounded,
                            observed=observed, original=original):
                    if body is self.a.mover:
                        observed.append((right, forward))
                    return original(body, dt, right, forward, axes, grounded)

                active_frames = 0
                ended = None
                with patch.object(mover.Mover, '_accelerate', capture):
                    for n in range(100):
                        self.run.frame += 1
                        frame = self.run.frame
                        self.run.match.update(frame * 0.016, frame)
                        observed.clear()
                        buttons = 8 if n == 0 else JUMP if jump and active_frames == 5 else 0
                        self.a.take_commands([Command(frame, right=127, buttons=buttons, extra=0)])
                        self.assertEqual(self.a.mover.throttles, (127, 0))
                        mods = [m for m in self.a.mover.mods.values() if m.index == 19]
                        if instance.node_state(instance.graph.node(12)).active:
                            active_frames += 1
                            self.assertEqual(len(mods), 1, (frame, instance.graph.node(12).fields,
                                                           mover.statescript_mods(self.a.body_script.component)))
                            self.assertEqual((mods[0].op, mods[0].value), (mover.SET, 0))
                            self.assertEqual(mover.modified_throttles(mods, 1, 0), (0, 0))
                            if active_frames == 1:
                                self.assertEqual(mods[0].start, frame + 1)
                                self.assertFalse(mods[0].acts(frame))
                            else:
                                self.assertEqual(observed, [])  # authored MoveTo owns the pull
                        elif active_frames and ended is None:
                            ended = frame
                            self.assertEqual(mods[0].end, frame + 1)
                            self.assertTrue(mods[0].acts(frame))
                        elif ended is not None:
                            self.assertEqual(frame, ended + 1)
                            self.assertEqual(mods, [])
                            self.assertEqual(observed, [(1, 0)])
                            break
                self.assertGreater(active_frames, 1)
                self.assertIsNotNone(ended)
