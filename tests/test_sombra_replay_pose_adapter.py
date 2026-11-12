"""Accepted pose replay retains separate clocks and the existing history schema."""

import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import sombra_camo, sombra_replay


class SombraReplayPoseAdapterTests(unittest.TestCase):
    def test_recall_pose_uses_world_tick_and_preserves_all_stored_target_fields(self):
        script = SimpleNamespace(hero=sombra_camo.HERO, retired=False, frame=1000, frame_ms=16,
                                 world_frame_anchor=(100, 1000),
                                 component=SimpleNamespace(owner_dead=False))
        mover = SimpleNamespace(position=[1.0, 2.0, 3.0], yaw=0, pitch=0, last_command=None)
        player = SimpleNamespace(body=20, body_script=script, spawned=True, has_body=True, mover=mover)
        match = SimpleNamespace(ended=False, tick=110, _shown=lambda _: True)
        original = (110, (0.0, 0.0, 0.0), 1.7, 1.3, (0.1, 1.5, 0.2), 1234, False)
        history = deque([original])
        fight = SimpleNamespace(match=match, tick=110, now=1.76, is_dead=lambda _: False,
                                translocators=SimpleNamespace(recall_poses={20: object()}),
                                history={20: history})
        received = []

        def pose(command, seconds, tick, *, replay_accepted):
            received.append((command.frame, tick, seconds, replay_accepted, match.tick, fight.tick))
            script.frame = command.frame

        player._step_body_pose = pose
        with patch.object(sombra_replay, "_next_frame", side_effect=[1001, None]):
            sombra_replay.replay_player(fight, player, 101, .016)
        self.assertEqual(received, [(1001, 101, .016, True, 101, 101)])
        self.assertEqual(history[-1], (original[0], (1.0, 2.0, 3.0), *original[2:]))
        self.assertEqual((match.tick, fight.tick, fight.now), (110, 110, 1.76))
